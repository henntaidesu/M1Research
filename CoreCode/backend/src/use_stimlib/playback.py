"""头显播放控制 + 实验会话记录：网页发指令，头显轮询状态并照做，后端是唯一的时钟。

外网部署下服务器连不进头显（见 CLAUDE.md），所以是「头显拉状态」：
GET /api/stimlib/playback?client=vr 每 0.3s 一次（兼作在线心跳，并带回 server_ms 供头显对时）。
头显每秒回报一次播放状态（POST /api/stimlib/playback/status），
并以 60Hz 采 FEA、每秒批量上传（POST /api/stimlib/fea，附头显估计的时钟偏移）。

模式：
  manual    网页手动选刺激 → 播放 / 暂停 / 跳转 / 灰屏 / 评分 / 停止（调试、演示）
  sequence  按流程自动跑一场实验，两种：
            protocol  正式实验：静息基线 → 表演校准 → [注视 → 刺激 → 自评 → 恢复] × 试次数（protocol.py），
                      各阶段时长 / 评分方式按刺激的 trial_class 取试次模板（trialtypes.py）；
                      中间按设定插休息；每个试次算 FEA 覆盖率，不合格的追加到队尾重看
            compare   图片线 vs 视频线预评定（A/B 平衡分组，见 build_compare_playlist）

计时以后端为准，但**刺激阶段要等头显确认已开始呈现才计时**（或等待超时）：外网下载、
视频准备都要时间，否则受试者实际看到的时长会比设定短且每次不一样。
头显确认时带上 onset_headset_ms（首帧可见的头显时刻），用它回报的时钟偏移换算成服务器时钟作为 onset
（onset_src=headset）；没带就用服务器收到确认的时刻（server），等确认超时则记 timeout。
场景 / 模仿片段里的事件时刻（巨响、片段结束……）由头显打点，POST /api/stimlib/events 写进 events.csv。

每场实验直接落成训练用的时间轴格式（与 use_capture/session_store 同一套，切窗脚本可直接读）：
  Capture/<subject>/<session_id>/
    meta.json        受试者、流程快照、播放顺序、种子、FEA 帧率、时钟偏移、状态
    fea.csv          timestamp_ms(服务器时钟), f0..f69, headset_ms, source, conf_lower, conf_upper
    events.csv       每个阶段的起止与事件（服务器时钟），…, detail, src(server/headset), headset_ms
    stimulus.csv     onset_ms, image_id, target_emotion(=条件), offset_ms, dwell_ms, scene_id, trial, line,
                     attempt, fea_cov, quality, onset_src, kind, trial_class
    selfreport.csv   timestamp_ms, image_id, label7, valence, arousal, scene_id, trial, rating_ms, intensity, attempt,
                     rating_mode(full/quick)
    posed.csv        onset_ms, offset_ms, emotion, rep, fea_cov（表演校准，标签就是提示的类别）
自评同时追加到 Stimulus/lib/ratings.csv（跨会话汇总，方案对比用）。
"""
import bisect
import collections
import csv
import json
import logging
import random
import threading
import time

from .. import config
from ..use_model.fea_emotion import FEA_DIM, OVR_FACE_EXPRESSIONS, normalize_blendshapes
from ..use_web import stimulus_control
from . import catalog, conditions, library, protocol, trialtypes

logger = logging.getLogger(__name__)

SEQ_DEFAULTS = {"fixation_s": 5.0, "stimulus_s": 30.0, "rating_timeout_s": 0.0, "recovery_s": 10.0,
                "ack_timeout_s": 20.0, "rest_s": 0.0,
                # 以下默认关闭（compare 预评定用）；正式实验的取值来自 protocol.json
                "posed_reps": 0, "posed_hold_s": 4.0, "posed_relax_s": 3.0,
                "recovery_long_s": 30.0, "long_recovery_arousal": 0, "break_every": 0, "break_s": 120.0,
                "require_label7": False, "ask_intensity": False,
                "min_fea_coverage": 0.8, "requeue_bad": False, "max_attempts": 1,
                "templates": {}}

_LOCK = threading.RLock()
_state = {
    "version": 0, "mode": "idle", "phase": "idle", "stimulus": None, "paused": False,
    "seek": {"pos_s": 0.0, "ver": 0}, "phase_started_ms": 0, "phase_duration_s": None,
    "waiting_headset": False, "run": None, "manual_subject": "anon",
    "cue": None,                  # 表演校准当前提示 {"emotion", "rep", "i", "n"}
    "upcoming": None,             # 注视阶段：接下来要呈现的刺激（头显据此把灰屏亮度匹配到该图）
    "rating_opts": {"require_label7": False, "ask_intensity": False},
}
_manual_prefetch: list = []
_hs = {"stimulus_id": "", "state": "", "position_s": 0.0, "cached_ids": [], "error": "", "ts_ms": 0,
       "clock_offset_ms": None, "fea_ok": None, "kind": ""}

_items: list = []                 # [{trial, stimulus, status, rating, attempt, fea_cov, quality, ...}]
_seq_thread = None
_seq_stop = threading.Event()
_seq_skip = threading.Event()
_accum_s = 0.0
_resume_t = 0.0
_counting = False
_ack_wait_t = 0.0
_stim_onset_ms = 0
_ack_timed_out = False
_onset_src = ""

_session_dir = None
_fea_file = None
_fea_writer = None
_fea_last_flush = 0.0
_fea_rows = 0
_fea_batch_ms = 0                  # 最近一次收到头显批量 FEA 的服务器时间
_fea_recent = collections.deque()  # (server_ms, n_rows)，估算实时帧率
_fea_ts: list = []                 # 本会话已落盘帧的服务器时间（升序），算每个试次的 FEA 覆盖率


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bump() -> None:
    _state["version"] += 1


# ── 状态 ────────────────────────────────────────────────────────────
def _elapsed() -> float:
    if not _counting:
        return _accum_s
    return _accum_s + (time.monotonic() - _resume_t)


def state() -> dict:
    with _LOCK:
        s = json.loads(json.dumps(_state))
        s["phase_elapsed_s"] = round(_elapsed(), 2)
        s["lang"] = stimulus_control.get_control()["lang"]
        s["prefetch"] = _prefetch_list()
        s["recording"] = _fea_writer is not None
        s["server_ms"] = _now_ms()        # 头显用往返时间估计时钟偏移
    return s


def _fea_hz() -> float:
    now = _now_ms()
    while _fea_recent and now - _fea_recent[0][0] > 5000:
        _fea_recent.popleft()
    return round(sum(n for _, n in _fea_recent) / 5.0, 1)


def headset_status() -> dict:
    with _LOCK:
        h = dict(_hs)
        h["fea_hz"] = _fea_hz()
        h["fea_rows"] = _fea_rows
    h["age_s"] = round((_now_ms() - h["ts_ms"]) / 1000, 1) if h["ts_ms"] else None
    pref = {s["id"] for s in _prefetch_list()}
    h["cached_count"] = len(pref & set(h.get("cached_ids") or []))
    h["prefetch_count"] = len(pref)
    return h


def _prefetch_list() -> list:
    if _state["mode"] == "sequence" and _items:
        idx = (_state["run"] or {}).get("index", 0)
        return [it["stimulus"] for it in _items[max(0, idx - 1):]]
    return list(_manual_prefetch)


def report(body: dict) -> dict:
    """头显回报：{stimulus_id, state(loading/playing/shown/paused/error), position_s, cached_ids, error,
    clock_offset_ms, fea_ok, onset_headset_ms?, kind?}。

    确认开始呈现时，onset 优先用头显首帧时刻 + 时钟偏移（服务器 − 头显）；换算结果不在
    [进入刺激阶段 − 1 s, 现在 + 1 s] 内（偏移没估好 / 头显时钟跳变）就退回服务器接收时刻。
    刺激时长从 onset 起算，所以头显打点更早时，后端会相应少等一点。
    """
    global _counting, _resume_t, _stim_onset_ms, _onset_src
    with _LOCK:
        _hs.update({
            "stimulus_id": str(body.get("stimulus_id") or ""),
            "state": str(body.get("state") or ""),
            "position_s": float(body.get("position_s") or 0.0),
            "cached_ids": [str(x) for x in (body.get("cached_ids") or [])][:2000],
            "error": str(body.get("error") or "")[:300],
            "ts_ms": _now_ms(),
            "clock_offset_ms": body.get("clock_offset_ms"),
            "fea_ok": body.get("fea_ok"),
            "kind": str(body.get("kind") or ""),
        })
        stim = _state["stimulus"]
        if (_state["waiting_headset"] and stim and _hs["stimulus_id"] == stim["id"]
                and _hs["state"] in ("playing", "shown")):
            now = _now_ms()
            onset, src = now, "server"
            try:
                hms, off = body.get("onset_headset_ms"), body.get("clock_offset_ms")
                if hms is not None and off is not None:
                    cand = int(hms) + int(off)
                    if _state["phase_started_ms"] - 1000 <= cand <= now + 1000:
                        onset, src = min(cand, now), "headset"
            except (TypeError, ValueError):
                pass
            _state["waiting_headset"] = False
            _state["phase_started_ms"] = _stim_onset_ms = onset
            _onset_src = src
            _counting = not _state["paused"]
            _resume_t = time.monotonic() - (now - onset) / 1000
            _event("ack", detail=f"{_hs['state']} onset={src}")
            _bump()
    return {"ok": True}


# ── 手动模式 ────────────────────────────────────────────────────────
def _require_manual():
    if _state["mode"] == "sequence":
        raise RuntimeError("实验正在运行，请先停止")


def _enter_phase(phase: str, stimulus=None, duration=None, wait_ack=False) -> None:
    global _accum_s, _counting, _resume_t, _ack_wait_t, _stim_onset_ms, _ack_timed_out, _onset_src
    _state["phase"] = phase
    _state["stimulus"] = stimulus
    _state["phase_duration_s"] = duration
    _state["phase_started_ms"] = _now_ms()
    _state["waiting_headset"] = wait_ack
    _state["cue"] = None
    _state["upcoming"] = None
    _accum_s = 0.0
    _resume_t = time.monotonic()
    _ack_wait_t = time.monotonic()
    _stim_onset_ms = 0 if wait_ack else _now_ms()
    _ack_timed_out = False
    _onset_src = "" if wait_ack else "server"
    _counting = not wait_ack and not _state["paused"]
    _bump()


def cmd_show(stim_id: str, subject_id: str = "") -> dict:
    stim = catalog.get_stimulus(stim_id)
    if not stim:
        raise ValueError(f"找不到刺激 {stim_id}")
    with _LOCK:
        _require_manual()
        _state["mode"] = "manual"
        _state["paused"] = False
        _state["seek"] = {"pos_s": 0.0, "ver": _state["seek"]["ver"] + 1}
        if subject_id:
            _state["manual_subject"] = subject_id
        _enter_phase("stimulus", stim, None)
    return state()


def cmd_blank() -> dict:
    with _LOCK:
        _require_manual()
        _state["mode"] = "manual"
        _state["paused"] = False
        _enter_phase("fixation", None, None)
    return state()


def cmd_rate() -> dict:
    with _LOCK:
        _require_manual()
        stim = _state["stimulus"]
        if not stim:
            raise RuntimeError("当前没有刺激可评分")
        _state["paused"] = True
        _enter_phase("rating", stim, None)
    return state()


def cmd_pause(paused: bool) -> dict:
    global _accum_s, _counting, _resume_t
    with _LOCK:
        if _state["mode"] == "idle" or _state["paused"] == paused:
            return state()
        _state["paused"] = paused
        if paused:
            _accum_s = _elapsed()
            _counting = False
        elif not _state["waiting_headset"]:
            _resume_t = time.monotonic()
            _counting = True
        _event("pause" if paused else "resume")
        _bump()
    return state()


def cmd_seek(pos_s: float) -> dict:
    with _LOCK:
        _require_manual()
        _state["seek"] = {"pos_s": max(0.0, float(pos_s)), "ver": _state["seek"]["ver"] + 1}
        _bump()
    return state()


def cmd_stop() -> dict:
    if _state["mode"] == "sequence":
        _seq_stop.set()
        t = _seq_thread
        if t is not None:
            t.join(timeout=3)
    with _LOCK:
        _state["mode"] = "idle"
        _state["paused"] = False
        _enter_phase("idle", None, None)
    return state()


def set_prefetch(ids) -> dict:
    """手动模式的预缓存列表；'all' = 当前流程要用到的全部刺激（流程有问题时退回库里全部未剔除的）。"""
    global _manual_prefetch
    if ids == "all":
        stims = protocol.resolve()["items"]
        if not stims:
            stims = [s for m in library.list_pairs() if m.get("qc") != "reject" for s in m["stimuli"]]
    else:
        stims = [s for s in (catalog.get_stimulus(i) for i in ids) if s]
    with _LOCK:
        _manual_prefetch = stims
        _bump()
    return state()


# ── 序列（一场实验）────────────────────────────────────────────────
def build_compare_playlist(cb_group: str, conds: list, qc_only: bool, seed: int) -> list:
    """图片线 vs 视频线的预评定播放列表（平衡分组）。

    A / B   同一受试者每对只看一种版本（避免同一场景看两遍带来的熟悉化）；每个条件内按 pair_id
            排序后交替分配图片 / 视频，A、B 起点相反，两组互补
    both    每对两种都看（有熟悉化混淆，只建议预览时用）
    image / video  只看一条线
    """
    pairs = [m for m in library.list_pairs() if m.get("status") in ("ok", "image_only")
             and m.get("qc") != "reject" and (not qc_only or m.get("qc") == "ok")
             and (not conds or m.get("condition") in conds)]
    chosen = []
    for cond in conditions.CONDITION_KEYS:
        cp = sorted([m for m in pairs if m["condition"] == cond], key=lambda m: m["pair_id"])
        for j, m in enumerate(cp):
            by_line = {s["line"]: s for s in m["stimuli"]}
            if cb_group in ("A", "B"):
                want = "image" if (j % 2 == 0) == (cb_group == "A") else "video"
                s = by_line.get(want) or by_line.get("image")
                if s:
                    chosen.append(s)
            elif cb_group == "both":
                chosen.extend(by_line.values())
            elif cb_group in by_line:
                chosen.append(by_line[cb_group])
    rng = random.Random(seed)
    for _ in range(300):
        rng.shuffle(chosen)
        if _order_ok(chosen):
            break
    return chosen


def _order_ok(items: list) -> bool:
    run = 1
    for a, b in zip(items, items[1:]):
        if a["pair_id"] == b["pair_id"]:
            return False
        run = run + 1 if a["condition"] == b["condition"] else 1
        if run > 2:
            return False
    return True


def start_sequence(body: dict) -> dict:
    """开始一场实验。kind=protocol（默认，按 ④ 实验流程）或 compare（图片 vs 视频预评定）。"""
    global _items, _seq_thread
    subject_id = "".join(ch for ch in str(body.get("subject_id") or "").strip() if ch.isalnum() or ch in "_-")
    if not subject_id:
        raise ValueError("请填写受试者 ID（字母、数字、_、-）")
    kind = str(body.get("kind") or "protocol")
    seed = int(body.get("seed", -1))
    if seed < 0:
        seed = random.randint(0, 2 ** 31 - 1)

    if kind == "protocol":
        p = protocol.get()
        res = protocol.resolve(p)
        if res["problems"]:
            raise ValueError("实验流程还不能运行：" + "；".join(res["problems"]))
        playlist = protocol.playlist(p, seed)
        tier2 = sorted({x["id"] for x in playlist if int(x.get("ethics_tier") or 0) >= 2})
        if tier2 and not body.get("consent_tier2"):
            raise ValueError("流程含伦理等级 2 的刺激（惊吓 / 欺骗类：" + "、".join(tier2)
                             + "），需确认受试者已签署单独知情同意（consent_tier2）")
        settings = {k: p.get(k, v) for k, v in SEQ_DEFAULTS.items()}
        extra = {"protocol": p}
        extra["consent_tier2"] = bool(body.get("consent_tier2"))
        resume = str(body.get("resume_session") or "")
        if resume:                      # 接着上一场没做完的试次跑（崩溃 / 中途停止后不必从头来）
            done = _done_stimuli(subject_id, resume)
            playlist = [s for s in playlist if s["id"] not in done]
            settings["rest_s"] = min(float(settings["rest_s"]), 30.0)
            settings["posed_reps"] = 0
            extra["resumed_from"] = resume
            if not playlist:
                raise ValueError(f"会话 {resume} 的试次都已完成")
        cb_group = ""
    elif kind == "compare":
        cb_group = str(body.get("cb_group") or "A")
        if cb_group not in ("A", "B", "both", "image", "video"):
            raise ValueError("cb_group 只能是 A / B / both / image / video")
        settings = dict(SEQ_DEFAULTS)
        for k in ("fixation_s", "stimulus_s", "rating_timeout_s", "recovery_s", "ack_timeout_s", "rest_s"):
            if k in body:
                settings[k] = max(0.0, float(body[k]))
        playlist = build_compare_playlist(cb_group, body.get("conditions") or [], bool(body.get("qc_only")), seed)
        extra = {}
    else:
        raise ValueError("kind 只能是 protocol / compare")
    if not playlist:
        raise ValueError("没有可播放的刺激")

    with _LOCK:
        if _state["mode"] == "sequence":
            raise RuntimeError("已有实验在运行")
        session_id = "stim_" + time.strftime("%Y%m%d_%H%M%S")
        _items = [_new_item(i + 1, s, 1) for i, s in enumerate(playlist)]
        _state["mode"] = "sequence"
        _state["paused"] = False
        _state["rating_opts"] = {"require_label7": bool(settings["require_label7"]),
                                 "ask_intensity": bool(settings["ask_intensity"])}
        _state["run"] = {"run_id": session_id, "session_id": session_id, "subject_id": subject_id,
                         "kind": kind, "cb_group": cb_group, "seed": seed, "settings": settings,
                         "index": 0, "total": len(_items), "started_ms": _now_ms(), **extra}
        _open_session(subject_id, session_id)
        _write_meta(status="running")
        _seq_stop.clear()
        _seq_thread = threading.Thread(target=_seq_loop, name="stimlib-seq", daemon=True)
        _seq_thread.start()
    return state()


def _new_item(trial: int, stim: dict, attempt: int) -> dict:
    return {"trial": trial, "stimulus": stim, "status": "pending", "rating": None,
            "attempt": attempt, "fea_cov": None, "quality": "", "onset_ms": 0, "offset_ms": 0, "acked": False,
            "onset_src": ""}


def _done_stimuli(subject_id: str, session_id: str) -> set:
    """上一场里已经有合格评分的刺激（已评分且 quality 不是 bad）。"""
    d = (config.CAPTURE_DIR / subject_id / session_id).resolve()
    if not d.is_relative_to(config.CAPTURE_DIR.resolve()) or not (d / "meta.json").exists():
        raise ValueError(f"找不到会话 {subject_id}/{session_id}")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return {it["stimulus_id"] for it in meta.get("playlist", [])
            if it.get("status") == "rated" and it.get("quality") != "bad"}


def seq_skip() -> dict:
    """跳过当前试次剩下的阶段（记 skip 事件），直接进下一个试次。"""
    with _LOCK:
        if _state["mode"] != "sequence":
            raise RuntimeError("没有在运行的实验")
        idx = _state["run"]["index"]
        if 0 < idx <= len(_items) and _items[idx - 1]["status"] == "running":
            _items[idx - 1]["status"] = "skipped"
        _event("skip")
        _seq_skip.set()
    return state()


def run_items() -> list:
    with _LOCK:
        return [{"trial": it["trial"], "stimulus_id": it["stimulus"]["id"], "line": it["stimulus"]["line"],
                 "condition": it["stimulus"]["condition"], "scene_id": it["stimulus"].get("scene_id", ""),
                 "scene_name": it["stimulus"].get("scene_name", ""), "scene_no": it["stimulus"].get("scene_no"),
                 "status": it["status"], "rating": it["rating"], "attempt": it["attempt"],
                 "fea_cov": it["fea_cov"], "quality": it["quality"],
                 "kind": it["stimulus"].get("kind", ""), "trial_class": it["stimulus"].get("trial_class", "immersive"),
                 "onset_src": it["onset_src"]}
                for it in _items]


def _seq_loop() -> None:
    try:
        st = _state["run"]["settings"]
        if st.get("rest_s", 0) > 0 and not _seq_stop.is_set():
            _phase("rest", None, st["rest_s"])          # 整场一次的静息基线
            if _wait_phase() == "stop":
                _seq_stop.set()
        if int(st.get("posed_reps", 0)) > 0 and not _seq_stop.is_set():
            _run_posed(st)
        i = 0
        since_break = 0
        while i < len(_items) and not _seq_stop.is_set():
            it = _items[i]
            i += 1
            if st.get("break_every") and since_break >= int(st["break_every"]):
                since_break = 0
                _seq_skip.clear()
                _phase("break", None, st["break_s"])     # 「跳过」= 提前结束休息
                if _wait_phase() == "stop":
                    break
            since_break += 1
            _seq_skip.clear()
            with _LOCK:
                _state["run"]["index"] = i
                it["status"] = "running"
            outcome = _run_trial(it, st)
            _judge_trial(it, st)
            with _LOCK:
                if (it["quality"] == "bad" and st.get("requeue_bad") and outcome != "stop"
                        and it["attempt"] < int(st.get("max_attempts", 1))):
                    _items.append(_new_item(len(_items) + 1, it["stimulus"], it["attempt"] + 1))
                    _state["run"]["total"] = len(_items)
                    _event("requeue", detail=f"trial {it['trial']} -> {len(_items)}")
                _write_meta(status="running")
            if outcome == "stop":
                break
        stopped = _seq_stop.is_set()
        with _LOCK:
            _event("stopped" if stopped else "done")
            _state["paused"] = False
            _enter_phase("idle" if stopped else "done", None, None)
            _write_meta(status="stopped" if stopped else "done")
            _close_session()
            _state["mode"] = "idle"
            _bump()
    except Exception:          # noqa: BLE001 —— 线程里的异常必须收尾，否则状态卡在 sequence
        logger.exception("stimlib sequence crashed")
        with _LOCK:
            _write_meta(status="error")
            _close_session()
            _state["mode"] = "idle"
            _enter_phase("idle", None, None)


def _run_trial(it: dict, st: dict) -> str:
    """一个试次：注视 → 刺激（等头显确认）→ 自评 → 恢复。返回 done / skip / stop。

    各阶段时长按刺激 trial_class 的模板（trialtypes.plan，注视随机时长按 运行种子+试次 确定）；
    rating=none 时跳过自评阶段。
    """
    plan = trialtypes.plan(it["stimulus"], st, _state["run"]["seed"], it["trial"])
    for name in ("fixation", "stimulus", "rating", "recovery"):
        if name == "rating" and plan["rating"] == "none":
            continue
        stim = it["stimulus"] if name in ("stimulus", "rating") else None
        dur = {"fixation": plan["fixation_s"], "stimulus": plan["stimulus_s"],
               "rating": plan["rating_timeout_s"], "recovery": plan["recovery_s"]}[name]
        if name == "recovery":
            with _LOCK:
                if it["status"] == "running":
                    it["status"] = "rated" if it["rating"] else "no_rating"
            thr = int(st.get("long_recovery_arousal") or 0)
            if thr and it["rating"] and it["rating"]["arousal"] >= thr:
                dur = max(dur, float(st["recovery_long_s"]))
        _phase(name, stim, dur, wait_ack=(name == "stimulus"))
        if name == "fixation":
            with _LOCK:
                _state["upcoming"] = it["stimulus"]
                _bump()
        outcome = _wait_phase(rating=(name == "rating"))
        if name == "stimulus":
            with _LOCK:
                it["offset_ms"] = _now_ms()
                it["onset_ms"] = _stim_onset_ms or it["offset_ms"]
                it["acked"] = not _ack_timed_out
                it["onset_src"] = "timeout" if _ack_timed_out else (_onset_src or "server")
        if outcome != "done":
            return outcome
    return "done"


def _judge_trial(it: dict, st: dict) -> None:
    """刺激期间的 FEA 覆盖率 → quality（ok / bad），写 stimulus.csv。

    头显每秒才批量上传一次，所以等刺激结束后至少 2 秒再数（恢复期一般已经够长）。
    """
    if not it["onset_ms"]:
        return                              # 刺激阶段之前就被跳过 / 停止
    wait = it["offset_ms"] + 2000 - _now_ms()
    if wait > 0:
        time.sleep(wait / 1000)
    with _LOCK:
        it["fea_cov"] = _coverage(it["onset_ms"], it["offset_ms"])
        bad = (not it["acked"]) or it["fea_cov"] < float(st.get("min_fea_coverage", 0))
        it["quality"] = "bad" if bad else "ok"
        _log_stimulus(it)


def _coverage(on_ms: int, off_ms: int) -> float:
    """[on, off] 内实际落盘帧数 / 按头显采样率应有的帧数。"""
    if off_ms <= on_ms:
        return 0.0
    n = bisect.bisect_right(_fea_ts, off_ms) - bisect.bisect_left(_fea_ts, on_ms)
    rate = float(_hs.get("fea_rate_hz") or 60.0)
    return round(min(1.0, n / ((off_ms - on_ms) / 1000 * rate)), 3)


def _run_posed(st: dict) -> None:
    """表演校准：提示「请做出 X 的表情」保持 posed_hold_s，再放松 posed_relax_s。

    标签就是提示的类别，和 FEA 不同源；用于个人归一化，以及「表演 → 自发」迁移分析。
    """
    cues = protocol.posed_cues(st, _state["run"]["seed"])
    for k, c in enumerate(cues):
        if _seq_stop.is_set():
            return
        _seq_skip.clear()
        with _LOCK:
            _enter_phase("posed", None, st["posed_hold_s"])
            _state["cue"] = {"emotion": c["emotion"], "rep": c["rep"], "i": k + 1, "n": len(cues)}
            _event("phase", detail=f"posed:{c['emotion']}")
        on = _now_ms()
        outcome = _wait_phase()
        off = _now_ms()
        if outcome != "stop":
            _phase("posed_relax", None, st["posed_relax_s"])
            outcome = _wait_phase()
        with _LOCK:
            _append("posed.csv", ["onset_ms", "offset_ms", "emotion", "rep", "fea_cov"],
                    [on, off, c["emotion"], c["rep"], _coverage(on, off)])
        if outcome == "stop":
            return


def _phase(name: str, stim, duration, wait_ack=False) -> None:
    with _LOCK:
        _enter_phase(name, stim, duration, wait_ack=wait_ack)
        _event("phase", detail=f"{duration if duration is not None else 'open'}")


def _wait_phase(rating: bool = False) -> str:
    """等当前阶段结束：返回 "done" / "skip" / "stop"。"""
    global _counting, _resume_t, _stim_onset_ms, _ack_timed_out, _onset_src
    while not _seq_stop.is_set():
        if _seq_skip.is_set():
            return "skip"
        with _LOCK:
            if rating and _state["phase"] != "rating":
                return "done"                        # 评分已提交，submit_rating 切走了阶段
            if _state["waiting_headset"]:
                ack_to = _state["run"]["settings"]["ack_timeout_s"]
                if not _state["paused"] and time.monotonic() - _ack_wait_t > ack_to:
                    _state["waiting_headset"] = False
                    _counting = True
                    _resume_t = time.monotonic()
                    _stim_onset_ms = _now_ms()
                    _ack_timed_out = True
                    _onset_src = "timeout"
                    _event("ack_timeout")
                    _bump()
            dur = _state["phase_duration_s"]
            if dur is not None and not _state["waiting_headset"] and _elapsed() >= dur:
                return "done"
        time.sleep(0.05)
    return "stop"


# ── 自评 ────────────────────────────────────────────────────────────
def submit_rating(body: dict) -> dict:
    """头显自评：{stimulus_id, valence, arousal, label7?, intensity?, rating_ms?, mode?(full/quick)}。

    quick = 情感网格快评（V/A 来自网格，不问强度，intensity 记 0）；require_label7 两种都生效。
    """
    stim_id = str(body.get("stimulus_id") or "")
    mode = str(body.get("mode") or "full")
    if mode not in ("full", "quick"):
        raise ValueError("mode 只能是 full / quick")
    try:
        v, a = int(body.get("valence")), int(body.get("arousal"))
    except (TypeError, ValueError):
        raise ValueError("valence / arousal 必须是 1–9 的整数")
    if not (1 <= v <= 9 and 1 <= a <= 9):
        raise ValueError("valence / arousal 必须在 1–9")
    label7 = str(body.get("label7") or "")
    if label7 and label7 not in config.TRAIN_CLASSES:
        label7 = ""
    try:
        intensity = int(body.get("intensity") or 0)
    except (TypeError, ValueError):
        intensity = 0
    intensity = intensity if 1 <= intensity <= 5 and mode == "full" else 0
    if _state["mode"] == "sequence" and _state["rating_opts"].get("require_label7") and not label7:
        raise ValueError("请选择最接近的情绪")
    stim = catalog.get_stimulus(stim_id)
    if not stim:
        raise ValueError(f"找不到刺激 {stim_id}")
    rating_ms = int(body.get("rating_ms") or 0)

    with _LOCK:
        run = _state["run"] if _state["mode"] == "sequence" else None
        trial = ""
        attempt = 1
        if run:
            idx = run["index"]
            it = _items[idx - 1] if 0 < idx <= len(_items) else None
            if not it or it["stimulus"]["id"] != stim_id or _state["phase"] != "rating":
                raise RuntimeError("当前不在该刺激的评分阶段")
            it["rating"] = {"valence": v, "arousal": a, "label7": label7, "intensity": intensity, "mode": mode}
            it["status"] = "rated"
            trial = it["trial"]
            attempt = it["attempt"]
            stim = it["stimulus"]
        now = _now_ms()
        row = {
            "ts_ms": now, "run_id": run["run_id"] if run else "manual",
            "subject_id": run["subject_id"] if run else _state["manual_subject"],
            "cb_group": run["cb_group"] if run else "", "trial": trial, "stimulus_id": stim_id,
            "pair_id": stim["pair_id"], "line": stim["line"], "condition": stim["condition"],
            "valence": v, "arousal": a, "label7": label7, "rating_ms": rating_ms,
            "scene_id": stim.get("scene_id", ""), "intensity": intensity, "attempt": attempt, "rating_mode": mode,
        }
        library.append_rating(row)
        if run:
            _append("selfreport.csv",
                    ["timestamp_ms", "image_id", "label7", "valence", "arousal", "scene_id", "trial", "rating_ms",
                     "intensity", "attempt", "rating_mode"],
                    [now, stim_id, label7, v, a, stim.get("scene_id", ""), trial, rating_ms, intensity, attempt, mode])
            _event("rating", detail=f"v={v} a={a} {label7} i={intensity} {mode}")
            _state["phase"] = "rating_done"      # _wait_phase(rating=True) 看到后进入恢复期
            _bump()
        elif _state["phase"] == "rating":
            _state["paused"] = False
            _enter_phase("fixation", None, None)
    return {"ok": True, "row": row}


# ── 会话落盘 ────────────────────────────────────────────────────────
def _open_session(subject_id: str, session_id: str) -> None:
    global _session_dir, _fea_file, _fea_writer, _fea_rows, _fea_ts
    _session_dir = config.CAPTURE_DIR / subject_id / session_id
    _session_dir.mkdir(parents=True, exist_ok=True)
    _fea_file = open(_session_dir / "fea.csv", "w", encoding="utf-8", newline="")
    _fea_writer = csv.writer(_fea_file)
    # 前 71 列与 Capture 格式一致（timestamp_ms, f0..f69），切窗脚本只读这些；后两列是附加信息
    _fea_writer.writerow(["timestamp_ms"] + [f"f{i}" for i in range(FEA_DIM)]
                         + ["headset_ms", "source", "conf_lower", "conf_upper"])
    _fea_rows = 0
    _fea_ts = []


def _close_session() -> None:
    global _fea_file, _fea_writer
    if _fea_file:
        try:
            _fea_file.close()
        except OSError:
            pass
    _fea_file = None
    _fea_writer = None


def _append(name: str, header: list, row: list) -> None:
    if _session_dir is None:
        return
    path = _session_dir / name
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


EVENTS_HEADER = ["ts_ms", "trial", "phase", "event", "stimulus_id", "scene_id", "line", "condition", "detail",
                 "src", "headset_ms"]


def _event(kind: str, detail: str = "") -> None:
    run = _state.get("run")
    if _state["mode"] != "sequence" or not run:
        return
    stim = _state["stimulus"] or {}
    _append("events.csv", EVENTS_HEADER,
            [_now_ms(), run["index"], _state["phase"], kind, stim.get("id", ""), stim.get("scene_id", ""),
             stim.get("line", ""), stim.get("condition", ""), detail, "server", ""])


def record_events(body: dict) -> dict:
    """头显打点：{offset_ms, events: [{headset_ms, key, stimulus_id, detail}]} → events.csv（event = "evt:"+key）。

    ts_ms = headset_ms + offset_ms（服务器时钟，与 fea.csv 同一换算）；trial 按 stimulus_id 回找最近的试次
    （批量上传会晚到 0.5–1 s，那时可能已进入下一阶段）。只在实验运行中落盘，返回写入条数。
    """
    try:
        offset = int(body.get("offset_ms") or 0)
    except (TypeError, ValueError):
        offset = 0
    written = 0
    with _LOCK:
        run = _state.get("run")
        if _state["mode"] != "sequence" or not run or _session_dir is None:
            return {"ok": True, "written": 0}
        for e in (body.get("events") or [])[:500]:
            if not isinstance(e, dict):
                continue
            try:
                hms = int(e.get("headset_ms"))
            except (TypeError, ValueError):
                continue
            key = str(e.get("key") or "").strip()
            key = key[4:] if key.startswith("evt:") else key        # 头显已带前缀也只加一次
            if not key:
                continue
            sid = str(e.get("stimulus_id") or "")
            idx = run["index"]
            it = next((x for x in reversed(_items[:idx]) if x["stimulus"]["id"] == sid), None) if sid else None
            s = it["stimulus"] if it else {}
            _append("events.csv", EVENTS_HEADER,
                    [hms + offset, it["trial"] if it else idx, _state["phase"], "evt:" + key[:60], sid,
                     s.get("scene_id", ""), s.get("line", ""), s.get("condition", ""),
                     str(e.get("detail") or "")[:200], "headset", hms])
            written += 1
    return {"ok": True, "written": written}


def _log_stimulus(it: dict) -> None:
    """试次结束时写一行 stimulus.csv（onset 取头显确认开始呈现的时刻）。"""
    s = it["stimulus"]
    on, off = it["onset_ms"], it["offset_ms"]
    _append("stimulus.csv",
            ["onset_ms", "image_id", "target_emotion", "offset_ms", "dwell_ms", "scene_id", "trial", "line",
             "attempt", "fea_cov", "quality", "onset_src", "kind", "trial_class"],
            [on, s["id"], s["condition"], off, off - on, s.get("scene_id", ""), it["trial"], s["line"],
             it["attempt"], it["fea_cov"], it["quality"], it["onset_src"], s.get("kind", ""),
             s.get("trial_class", "immersive")])


def _write_meta(status: str) -> None:
    run = _state.get("run")
    if not run or _session_dir is None:
        return
    rated = sum(1 for it in _items if it["status"] == "rated")
    first = _items[0]["stimulus"] if _items else {}
    meta = {
        "subject_id": run["subject_id"], "session_id": run["session_id"], "source": "stimlib",
        "kind": run["kind"], "status": status, "created_ms": run["started_ms"], "updated_ms": _now_ms(),
        "seed": run["seed"], "consent_tier2": run.get("consent_tier2", False), "cb_group": run["cb_group"], "settings": run["settings"],
        "protocol": run.get("protocol"), "line": first.get("line", ""), "resumed_from": run.get("resumed_from", ""),
        "n_trials": len(_items), "n_rated": rated, "fea_rows": _fea_rows,
        "n_bad": sum(1 for it in _items if it["quality"] == "bad"),
        "fea_rate_hz": _hs.get("fea_rate_hz"), "clock_offset_ms": _hs.get("clock_offset_ms"),
        "sync_offset_ms": 0,   # fea.csv 已换算到服务器时钟
        "fea_dim": FEA_DIM, "fea_names": OVR_FACE_EXPRESSIONS,
        "emotion_order": list(config.TRAIN_CLASSES), "device": "Meta Quest Pro",
        "playlist": [{"trial": it["trial"], "stimulus_id": it["stimulus"]["id"],
                      "scene_id": it["stimulus"].get("scene_id", ""), "scene_name": it["stimulus"].get("scene_name", ""),
                      "condition": it["stimulus"]["condition"], "status": it["status"],
                      "attempt": it["attempt"], "fea_cov": it["fea_cov"], "quality": it["quality"],
                      "kind": it["stimulus"].get("kind", ""),
                      "trial_class": it["stimulus"].get("trial_class", "immersive"),
                      "onset_src": it["onset_src"]} for it in _items],
    }
    (_session_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _write_fea(ts_ms: int, headset_ms: int, bs, source: str, conf=("", "")) -> None:
    global _fea_rows, _fea_last_flush
    _fea_writer.writerow([ts_ms] + [f"{x:.4f}" for x in normalize_blendshapes(bs)] + [headset_ms, source, *conf])
    _fea_rows += 1
    if not _fea_ts or ts_ms >= _fea_ts[-1]:
        _fea_ts.append(ts_ms)
    else:
        bisect.insort(_fea_ts, ts_ms)
    if time.monotonic() - _fea_last_flush > 1.0:
        _fea_file.flush()
        _fea_last_flush = time.monotonic()


def record_fea_batch(body: dict) -> dict:
    """头显批量 FEA：{offset_ms, rate_hz, rows: [[headset_ms, f0..f69, conf_lower, conf_upper], ...]}。

    offset_ms = 服务器时钟 − 头显时钟（头显用轮询往返时间估计，取最小往返那次），
    据此把每帧换算到服务器时钟，和 events.csv 对齐。不在实验中时只计帧率不落盘
    （网页上要能在开始前就看到「FEA 正常」）。
    """
    global _fea_batch_ms
    rows = body.get("rows") or []
    try:
        offset = int(body.get("offset_ms") or 0)
    except (TypeError, ValueError):
        offset = 0
    now = _now_ms()
    written = 0
    with _LOCK:
        _fea_batch_ms = now
        _fea_recent.append((now, len(rows)))
        if body.get("rate_hz"):
            _hs["fea_rate_hz"] = body.get("rate_hz")
        if _fea_writer is not None and _state["mode"] == "sequence":
            for r in rows:
                if not isinstance(r, list) or len(r) < 64:
                    continue
                try:
                    hms = int(r[0])
                    conf = (tuple(f"{float(x):.3f}" for x in r[1 + FEA_DIM:3 + FEA_DIM])
                            if len(r) >= 3 + FEA_DIM else ("", ""))
                    _write_fea(hms + offset, hms, r[1:1 + FEA_DIM], "headset_batch", conf)
                    written += 1
                except (TypeError, ValueError):
                    continue
    return {"ok": True, "written": written}


def record_fea(headset_ms: int, blendshapes) -> None:
    """/api/fea 单帧（QuestProFaceCapture）：只在头显没有批量上传时兜底落盘，时间用服务器接收时刻。"""
    if _fea_writer is None:
        return
    with _LOCK:
        if _fea_writer is None or _state["mode"] != "sequence" or _now_ms() - _fea_batch_ms < 3000:
            return
        _write_fea(_now_ms(), int(headset_ms or 0), blendshapes, "api_fea")


# ── 会话列表（⑥ 数据）──────────────────────────────────────────────
def list_sessions() -> list:
    out = []
    if not config.CAPTURE_DIR.exists():
        return out
    for p in config.CAPTURE_DIR.glob("*/*/meta.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("source") != "stimlib":
            continue
        m.pop("fea_names", None)
        m.pop("playlist", None)
        m["dir"] = str(p.parent.relative_to(config.ROOT)) if p.parent.is_relative_to(config.ROOT) else str(p.parent)
        out.append(m)
    out.sort(key=lambda m: m.get("created_ms", 0), reverse=True)
    return out


def session_detail(subject_id: str, session_id: str) -> dict:
    d = (config.CAPTURE_DIR / subject_id / session_id).resolve()
    if not d.is_relative_to(config.CAPTURE_DIR.resolve()) or not (d / "meta.json").exists():
        raise ValueError("会话不存在")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta.pop("fea_names", None)
    ratings = []
    if (d / "selfreport.csv").exists():
        with open(d / "selfreport.csv", encoding="utf-8", newline="") as f:
            ratings = list(csv.DictReader(f))
    return {"meta": meta, "ratings": ratings}
