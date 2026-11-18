"""④ 实验流程：用哪些场景、什么顺序、各阶段多长。存于 Stimulus/protocol.json。

一次实验（会话）的结构：
    静息基线 rest_s（灰屏，整场一次，作个人校准）
    表演校准 posed_reps 轮 × 7 类（提示「请做出 X 的表情」保持 posed_hold_s，放松 posed_relax_s）
    对每个场景：注视 fixation_s → 刺激 stimulus_s → 自评 → 恢复 recovery_s
        自评唤醒度 ≥ long_recovery_arousal 时恢复期延长到 recovery_long_s
        每 break_every 个试次插一次休息 break_s（操作员可点「跳过」提前结束）
    结束

数据质量（每试次）：刺激期间实际收到的 FEA 帧数 / 期望帧数 = fea_cov。
    fea_cov < min_fea_coverage 或头显没确认呈现（ack 超时）→ 该试次记为 bad；
    requeue_bad 时把它追加到队尾重看一次（attempt=2，分析时可按 attempt 过滤）。

order：
    fixed    按场景列表顺序（场景1 → 场景2 → …），extra_ids 接在后面
    random   每名受试者按种子随机打乱（推荐：排除顺序效应），约束：
             同一条件不连续超过 2 次；同一 trial_class 不连续超过 3 次；
             同一 habituation_group（如 startle）的刺激尽量相隔 ≥ 3 个试次（做不到时取违例最少的顺序）

extra_ids：catalog 里的刺激 id（事件场景、模仿片段、带常模视频……），追加在场景刺激之后，
    混合范式先这样拼；不存在 / 已剔除 / 已停用的 id 会作为 problems 报出来。
templates：按 trial_class 覆盖试次模板的时长（见 trialtypes.py）。
"""
import json
import random
import threading

from .. import config
from . import catalog, library, scenes, trialtypes

PROTOCOL_PATH = config.IMAGE_DIR / "protocol.json"
_LOCK = threading.Lock()

DEFAULTS = {
    "scene_ids": [],            # 空 = 全部场景（按场景列表顺序）
    "order": "random",
    "line": "image",            # image / video
    "rest_s": 60.0,
    "fixation_s": 5.0,
    "stimulus_s": 30.0,
    "rating_timeout_s": 0.0,    # 0 = 一直等受试者提交
    "recovery_s": 10.0,
    "ack_timeout_s": 20.0,
    "posed_reps": 2,            # 表演校准轮数；0 = 不做
    "posed_hold_s": 4.0,
    "posed_relax_s": 3.0,
    "recovery_long_s": 30.0,
    "long_recovery_arousal": 7,  # 自评唤醒度 ≥ 此值时恢复期用 recovery_long_s；0 = 不延长
    "break_every": 0,           # 每 N 个试次休息一次；0 = 不休息
    "break_s": 120.0,
    "require_label7": True,     # 自评面板 7 类情绪必选（不选则该试次没有 7 类标签）
    "ask_intensity": True,      # 追问所选情绪的强度 1–5
    "min_fea_coverage": 0.8,
    "requeue_bad": True,
    "max_attempts": 2,
    "extra_ids": [],            # catalog 刺激 id，追加在场景刺激之后
    "templates": {},            # {trial_class: {fixation_s, stimulus_s, rating_timeout_s, recovery_s}}
}
_NUM_KEYS = ("rest_s", "fixation_s", "stimulus_s", "rating_timeout_s", "recovery_s", "ack_timeout_s",
             "posed_hold_s", "posed_relax_s", "recovery_long_s", "break_s")
_INT_KEYS = {"posed_reps": (0, 5), "long_recovery_arousal": (0, 9), "break_every": (0, 100), "max_attempts": (1, 3)}
_BOOL_KEYS = ("require_label7", "ask_intensity", "requeue_bad")


def get() -> dict:
    with _LOCK:
        p = dict(DEFAULTS)
        if PROTOCOL_PATH.exists():
            try:
                p.update(json.loads(PROTOCOL_PATH.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return p


def save(body: dict) -> dict:
    p = get()
    if "scene_ids" in body:
        p["scene_ids"] = [str(x) for x in (body["scene_ids"] or [])]
    if "extra_ids" in body:
        p["extra_ids"] = [str(x) for x in (body["extra_ids"] or [])]
    if "templates" in body:
        p["templates"] = trialtypes.clean_overrides(body["templates"])
    if "order" in body:
        if body["order"] not in ("fixed", "random"):
            raise ValueError("order 只能是 fixed / random")
        p["order"] = body["order"]
    if "line" in body:
        if body["line"] not in library.LINES:
            raise ValueError("line 只能是 image / video")
        p["line"] = body["line"]
    for k in _NUM_KEYS:
        if k in body:
            p[k] = max(0.0, min(3600.0, float(body[k])))
    for k, (lo, hi) in _INT_KEYS.items():
        if k in body:
            p[k] = max(lo, min(hi, int(body[k])))
    for k in _BOOL_KEYS:
        if k in body:
            p[k] = bool(body[k])
    if "min_fea_coverage" in body:
        p["min_fea_coverage"] = max(0.0, min(1.0, float(body["min_fea_coverage"])))
    if p["stimulus_s"] < 1:
        raise ValueError("刺激时长至少 1 秒")
    with _LOCK:
        PROTOCOL_PATH.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def resolve(p: dict | None = None, seed: int = 0) -> dict:
    """把流程展开成可执行的试次列表，并检查每个场景 / catalog 刺激能否播放。

    返回 {"items": [...按场景列表顺序，再接 extra_ids...], "problems": [...]}；problems 非空就不能开始实验。
    seed 只影响 events 的抖动（同一种子展开结果相同）。
    """
    p = p or get()
    all_scenes = scenes.list_scenes()
    wanted = p["scene_ids"] or [s["id"] for s in all_scenes]
    by_id = {s["id"]: s for s in all_scenes}
    items, problems = [], []
    for sid in wanted:
        s = by_id.get(sid)
        if not s:
            problems.append(f"流程里的场景 {sid} 已被删除")
            continue
        label = f"场景{s['no']}「{s['name']}」"
        if not s.get("selected"):
            problems.append(f"{label} 还没有选定图片")
            continue
        stim = catalog.get_stimulus(f"{s['selected']['pair_id']}:{p['line']}", seed)
        if not stim:
            problems.append(f"{label} 选定的那一对没有{'视频' if p['line'] == 'video' else '图片'}")
            continue
        items.append({**stim, "scene_id": s["id"], "scene_no": s["no"], "scene_name": s["name"],
                      "condition": s["condition"]})
    for cid in p.get("extra_ids") or []:
        it = catalog.get(cid)
        bad = catalog.problems_of(it, cid)
        if bad:
            problems += bad
            continue
        items.append(catalog.to_headset(it, seed))
    if not items and not problems:
        problems.append("流程里没有场景")
    return {"items": items, "problems": problems}


def playlist(p: dict, seed: int) -> list:
    """按 order 生成本次会话的试次顺序。"""
    items = resolve(p, seed)["items"]
    if p["order"] == "fixed":
        return items
    rng = random.Random(seed)
    best, best_v = list(items), None
    for _ in range(300):
        rng.shuffle(items)
        v = _violations(items)
        if best_v is None or v < best_v:
            best, best_v = list(items), v
        if v == 0:
            break
    return best


def _violations(items: list) -> int:
    """顺序约束的违例数：同条件连续 > 2、同 trial_class 连续 > 3、同 habituation_group 间隔 < 3。"""
    n = 0
    run_c = run_t = 1
    for a, b in zip(items, items[1:]):
        run_c = run_c + 1 if a["condition"] == b["condition"] else 1
        run_t = run_t + 1 if a.get("trial_class", "immersive") == b.get("trial_class", "immersive") else 1
        n += (run_c > 2) + (run_t > 3)
    last = {}
    for i, s in enumerate(items):
        g = s.get("habituation_group")
        if g:
            if g in last and i - last[g] < 3:
                n += 1
            last[g] = i
    return n


POSED_ORDER = ["neutral", "happy", "surprise", "sad", "angry", "fear", "disgust"]


def posed_cues(p: dict, seed: int) -> list:
    """表演校准的提示序列：每轮 7 类各一次，第 1 轮固定顺序（先易后难），之后各轮按种子打乱。"""
    rng = random.Random(seed ^ 0x5EED)
    cues = []
    for r in range(int(p.get("posed_reps", 0))):
        order = list(POSED_ORDER)
        if r > 0:
            rng.shuffle(order)
        cues += [{"rep": r + 1, "emotion": e} for e in order]
    return cues


def estimate_s(p: dict, n: int, items: list | None = None) -> float:
    """整场大致时长（按每个刺激的试次模板估；没给 items 时按 n 个图片线试次、自评 15 秒估）。"""
    if items is not None:
        trials = sum(trialtypes.estimate_s(s, p) for s in items)
    else:
        trials = n * (p["fixation_s"] + p["stimulus_s"] + 15 + p["recovery_s"])
    posed = int(p.get("posed_reps", 0)) * 7 * (p.get("posed_hold_s", 0) + p.get("posed_relax_s", 0))
    breaks = (max(0, n - 1) // p["break_every"]) * p["break_s"] if p.get("break_every") else 0
    return p["rest_s"] + posed + trials + breaks
