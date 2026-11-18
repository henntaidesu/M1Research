"""头显播放控制：网页发指令，头显轮询状态并照做，后端是唯一的时钟。

外网部署下服务器连不进头显（见 CLAUDE.md），所以和 /api/stimulus/control 一样是
「头显拉状态」：GET /api/stimlib/playback?client=vr 每 0.3s 一次，兼作在线心跳。
头显每秒回报一次自己的播放状态（POST /api/stimlib/playback/status）。

两种模式：
  manual    网页手动选刺激 → 播放 / 暂停 / 跳转 / 灰屏 / 让受试者评分 / 停止
  sequence  按实验流程自动跑一个播放列表：注视(灰屏) → 刺激 → 自评 → 恢复(灰屏) → 下一个

计时以后端为准，但**刺激阶段要等头显确认已经开始播放才开始计时**（或等待超时）：
外网下载慢、视频准备要时间，否则受试者实际看到的时长会比设定短，而且每次都不一样。

序列运行时，/api/fea 收到的帧同时写进 runs/<run_id>/fea.csv（带服务器时间与当前阶段），
事件写进 events.csv，供 compare.py 计算各刺激的面部表情反应强度。
"""
import csv
import json
import random
import threading
import time

from .. import config
from ..use_model.fea_emotion import FEA_DIM, normalize_blendshapes
from ..use_web import stimulus_control
from . import conditions, library

RUNS_DIR = config.STIMLIB_DIR / "runs"

SEQ_DEFAULTS = {"fixation_s": 5.0, "stimulus_s": 30.0, "rating_timeout_s": 0.0, "recovery_s": 10.0,
                "ack_timeout_s": 20.0}

_LOCK = threading.RLock()
_state = {
    "version": 0, "mode": "idle", "phase": "idle", "stimulus": None, "paused": False,
    "seek": {"pos_s": 0.0, "ver": 0}, "phase_started_ms": 0, "phase_duration_s": None,
    "waiting_headset": False, "run": None, "prefetch": [], "manual_subject": "anon",
}
_manual_prefetch: list = []
_hs = {"stimulus_id": "", "state": "", "position_s": 0.0, "cached_ids": [], "error": "", "ts_ms": 0}

# 序列运行时的私有状态
_items: list = []                 # [{trial, stimulus, status, rating}]
_seq_thread = None
_seq_stop = threading.Event()
_seq_skip = threading.Event()
_accum_s = 0.0                    # 当前阶段已计时长（暂停时冻结）
_resume_t = 0.0                   # 本段计时起点（monotonic）
_counting = False
_ack_wait_t = 0.0
_fea_file = None
_fea_writer = None
_fea_last_flush = 0.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bump() -> None:
    _state["version"] += 1


# ── 对外：状态 ──────────────────────────────────────────────────────
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
    return s


def headset_status() -> dict:
    with _LOCK:
        h = dict(_hs)
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
    """头显回报：{stimulus_id, state(loading/ready/playing/shown/paused/ended/error), position_s, cached_ids, error}。"""
    global _counting, _resume_t
    with _LOCK:
        _hs.update({
            "stimulus_id": str(body.get("stimulus_id") or ""),
            "state": str(body.get("state") or ""),
            "position_s": float(body.get("position_s") or 0.0),
            "cached_ids": [str(x) for x in (body.get("cached_ids") or [])][:2000],
            "error": str(body.get("error") or "")[:300],
            "ts_ms": _now_ms(),
        })
        stim = _state["stimulus"]
        if (_state["waiting_headset"] and stim and _hs["stimulus_id"] == stim["id"]
                and _hs["state"] in ("playing", "shown")):
            _state["waiting_headset"] = False
            _state["phase_started_ms"] = _now_ms()
            _counting = not _state["paused"]
            _resume_t = time.monotonic()
            _event("ack", detail=_hs["state"])
            _bump()
    return {"ok": True}


# ── 手动模式 ────────────────────────────────────────────────────────
def _require_manual():
    if _state["mode"] == "sequence":
        raise RuntimeError("播放序列正在运行，请先停止序列")


def _enter_phase(phase: str, stimulus=None, duration=None, wait_ack=False) -> None:
    global _accum_s, _counting, _resume_t, _ack_wait_t
    _state["phase"] = phase
    _state["stimulus"] = stimulus
    _state["phase_duration_s"] = duration
    _state["phase_started_ms"] = _now_ms()
    _state["waiting_headset"] = wait_ack
    _accum_s = 0.0
    _resume_t = time.monotonic()
    _ack_wait_t = time.monotonic()
    _counting = not wait_ack and not _state["paused"]
    _bump()


def cmd_show(stim_id: str, subject_id: str = "") -> dict:
    stim = library.get_stimulus(stim_id)
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
    """手动模式下让受试者对当前刺激评分（头显弹出自评面板）。"""
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
    """停止一切：手动播放回到 idle；序列则结束本次运行。"""
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


def set_prefetch(ids: list) -> dict:
    """手动模式的预缓存列表；'all' = 库里全部未被质检剔除的刺激。"""
    global _manual_prefetch
    if ids == "all":
        stims = [s for m in library.list_pairs() if m.get("qc") != "reject" for s in m["stimuli"]]
    else:
        stims = [s for s in (library.get_stimulus(i) for i in ids) if s]
    with _LOCK:
        _manual_prefetch = stims
        _bump()
    return state()


# ── 序列模式 ────────────────────────────────────────────────────────
def build_playlist(subject_id: str, cb_group: str, conds: list, qc_only: bool, seed: int) -> list:
    """生成播放列表。

    cb_group 平衡分组（默认）：同一受试者每对刺激只看一种呈现方式，避免同一场景看两遍
    带来的熟悉化；A 组与 B 组互补（A 看图的那几对，B 看视频），跨受试者两条线的刺激完全对等。
      A / B   每个条件内按 pair_id 排序后交替分配图片 / 视频，A、B 起点相反
      both    每对两种都看（有熟悉化混淆，只建议预览时用）
      image / video  只看一条线
    顺序按种子随机，约束同一条件不连续出现超过 2 次。
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
    if not chosen:
        return []

    rng = random.Random(seed)
    best = None
    for _ in range(300):
        rng.shuffle(chosen)
        if _order_ok(chosen):
            best = list(chosen)
            break
    return best or list(chosen)


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
    global _items, _seq_thread
    subject_id = str(body.get("subject_id") or "").strip()
    if not subject_id:
        raise ValueError("请填写受试者 ID")
    cb_group = str(body.get("cb_group") or "A")
    if cb_group not in ("A", "B", "both", "image", "video"):
        raise ValueError("cb_group 只能是 A / B / both / image / video")
    seed = int(body.get("seed", -1))
    if seed < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    settings = dict(SEQ_DEFAULTS)
    for k in SEQ_DEFAULTS:
        if k in body:
            settings[k] = max(0.0, float(body[k]))
    playlist = build_playlist(subject_id, cb_group, body.get("conditions") or [],
                              bool(body.get("qc_only")), seed)
    if not playlist:
        raise ValueError("没有可播放的刺激（检查条件筛选 / 质检状态）")

    with _LOCK:
        if _state["mode"] == "sequence":
            raise RuntimeError("已有序列在运行")
        run_id = f"{subject_id}_{time.strftime('%Y%m%d_%H%M%S')}"
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _items = [{"trial": i + 1, "stimulus": s, "status": "pending", "rating": None}
                  for i, s in enumerate(playlist)]
        _state["mode"] = "sequence"
        _state["paused"] = False
        _state["run"] = {"run_id": run_id, "subject_id": subject_id, "cb_group": cb_group,
                         "seed": seed, "settings": settings, "index": 0, "total": len(_items),
                         "started_ms": _now_ms()}
        _write_run_meta(status="running")
        _open_fea(run_dir)
        _seq_stop.clear()
        _seq_thread = threading.Thread(target=_seq_loop, name="stimlib-seq", daemon=True)
        _seq_thread.start()
    return state()


def seq_skip() -> dict:
    """跳过当前试次剩下的阶段（记 skip 事件），直接进下一个试次。"""
    with _LOCK:
        if _state["mode"] != "sequence":
            raise RuntimeError("没有在运行的序列")
        idx = _state["run"]["index"]
        if 0 < idx <= len(_items) and _items[idx - 1]["status"] == "running":
            _items[idx - 1]["status"] = "skipped"
        _event("skip")
        _seq_skip.set()
    return state()


def run_items() -> list:
    with _LOCK:
        return [{"trial": it["trial"], "stimulus_id": it["stimulus"]["id"], "line": it["stimulus"]["line"],
                 "condition": it["stimulus"]["condition"], "status": it["status"], "rating": it["rating"]}
                for it in _items]


def _seq_loop() -> None:
    try:
        for i, it in enumerate(_items):
            if _seq_stop.is_set():
                break
            st = _state["run"]["settings"]
            _seq_skip.clear()
            with _LOCK:
                _state["run"]["index"] = i + 1
                it["status"] = "running"
            # 注视 → 刺激 → 自评 → 恢复；任一阶段被跳过就直接进下一个试次
            steps = [("fixation", None, st["fixation_s"], False),
                     ("stimulus", it["stimulus"], st["stimulus_s"], True),
                     ("rating", it["stimulus"], st["rating_timeout_s"] or None, False),
                     ("recovery", None, st["recovery_s"], False)]
            outcome = "done"
            for name, stim, dur, ack in steps:
                if name == "recovery":
                    with _LOCK:
                        if it["status"] == "running":
                            it["status"] = "rated" if it["rating"] else "no_rating"
                _phase(name, stim, dur, wait_ack=ack)
                outcome = _wait_phase(rating=(name == "rating"))
                if outcome != "done":
                    break
            if outcome == "stop":
                break
        stopped = _seq_stop.is_set()
        with _LOCK:
            _event("stopped" if stopped else "done")
            _state["paused"] = False
            _enter_phase("done" if not stopped else "idle", None, None)
            _write_run_meta(status="stopped" if stopped else "done")
            _close_fea()
            _state["mode"] = "idle"
            _bump()
    except Exception:          # noqa: BLE001 —— 线程里的异常必须收尾，否则状态卡在 sequence
        import logging
        logging.getLogger(__name__).exception("stimlib sequence crashed")
        with _LOCK:
            _write_run_meta(status="error")
            _close_fea()
            _state["mode"] = "idle"
            _enter_phase("idle", None, None)


def _phase(name: str, stim, duration, wait_ack=False) -> None:
    with _LOCK:
        _enter_phase(name, stim, duration, wait_ack=wait_ack)
        _event("phase", detail=f"{duration if duration is not None else 'open'}")


def _wait_phase(rating: bool = False) -> str:
    """等当前阶段结束：返回 "done" / "skip" / "stop"。"""
    global _counting, _resume_t
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
                    _event("ack_timeout")
                    _bump()
            dur = _state["phase_duration_s"]
            if dur is not None and not _state["waiting_headset"] and _elapsed() >= dur:
                return "done"
        time.sleep(0.05)
    return "stop"


# ── 自评 ────────────────────────────────────────────────────────────
def submit_rating(body: dict) -> dict:
    stim_id = str(body.get("stimulus_id") or "")
    try:
        v, a = int(body.get("valence")), int(body.get("arousal"))
    except (TypeError, ValueError):
        raise ValueError("valence / arousal 必须是 1–9 的整数")
    if not (1 <= v <= 9 and 1 <= a <= 9):
        raise ValueError("valence / arousal 必须在 1–9")
    label7 = str(body.get("label7") or "")
    if label7 and label7 not in config.TRAIN_CLASSES:
        label7 = ""
    stim = library.get_stimulus(stim_id)
    if not stim:
        raise ValueError(f"找不到刺激 {stim_id}")

    with _LOCK:
        run = _state["run"] if _state["mode"] == "sequence" else None
        trial = ""
        if run:
            idx = run["index"]
            it = _items[idx - 1] if 0 < idx <= len(_items) else None
            if not it or it["stimulus"]["id"] != stim_id or _state["phase"] != "rating":
                raise RuntimeError("当前不在该刺激的评分阶段")
            it["rating"] = {"valence": v, "arousal": a, "label7": label7}
            it["status"] = "rated"
            trial = it["trial"]
        row = {
            "ts_ms": _now_ms(),
            "run_id": run["run_id"] if run else "manual",
            "subject_id": run["subject_id"] if run else _state["manual_subject"],
            "cb_group": run["cb_group"] if run else "",
            "trial": trial, "stimulus_id": stim_id, "pair_id": stim["pair_id"], "line": stim["line"],
            "condition": stim["condition"], "valence": v, "arousal": a, "label7": label7,
            "rating_ms": int(body.get("rating_ms") or 0),
        }
        library.append_rating(row)
        _event("rating", detail=f"v={v} a={a} {label7}")
        if run:
            _state["phase"] = "rating_done"      # _wait_phase(rating=True) 看到后进入恢复期
            _bump()
        elif _state["phase"] == "rating":
            _state["paused"] = False
            _enter_phase("fixation", None, None)
    return {"ok": True, "row": row}


# ── 运行记录：事件 + FEA ────────────────────────────────────────────
def _event(kind: str, detail: str = "") -> None:
    run = _state.get("run")
    if _state["mode"] != "sequence" or not run:
        return
    path = RUNS_DIR / run["run_id"] / "events.csv"
    new = not path.exists()
    stim = _state["stimulus"] or {}
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts_ms", "trial", "phase", "event", "stimulus_id", "line", "condition", "detail"])
        w.writerow([_now_ms(), run["index"], _state["phase"], kind, stim.get("id", ""),
                    stim.get("line", ""), stim.get("condition", ""), detail])


def _write_run_meta(status: str) -> None:
    run = _state.get("run")
    if not run:
        return
    meta = {**run, "status": status, "updated_ms": _now_ms(),
            "playlist": [it["stimulus"]["id"] for it in _items]}
    (RUNS_DIR / run["run_id"] / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _open_fea(run_dir) -> None:
    global _fea_file, _fea_writer
    _fea_file = open(run_dir / "fea.csv", "w", encoding="utf-8", newline="")
    _fea_writer = csv.writer(_fea_file)
    _fea_writer.writerow(["server_ms", "headset_ms", "trial", "phase", "stimulus_id"]
                         + [f"f{i}" for i in range(FEA_DIM)])


def _close_fea() -> None:
    global _fea_file, _fea_writer
    if _fea_file:
        try:
            _fea_file.close()
        except OSError:
            pass
    _fea_file = None
    _fea_writer = None


def record_fea(headset_ms: int, blendshapes) -> None:
    """/api/fea 每收到一帧就调用；只在序列运行时落盘。时间以服务器接收时刻为准，
    与 events.csv 同一时钟（头显时钟与服务器不同步，不能直接比）。"""
    global _fea_last_flush
    if _fea_writer is None:
        return
    with _LOCK:
        if _fea_writer is None or _state["mode"] != "sequence":
            return
        stim = _state["stimulus"] or {}
        _fea_writer.writerow([_now_ms(), int(headset_ms or 0), _state["run"]["index"], _state["phase"],
                              stim.get("id", "")] + [f"{x:.4f}" for x in normalize_blendshapes(blendshapes)])
        if time.monotonic() - _fea_last_flush > 1.0:
            _fea_file.flush()
            _fea_last_flush = time.monotonic()


def list_runs() -> list:
    out = []
    if not RUNS_DIR.exists():
        return out
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        p = d / "meta.json"
        if p.exists():
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
                m.pop("playlist", None)
                out.append(m)
            except (OSError, ValueError):
                continue
    return out
