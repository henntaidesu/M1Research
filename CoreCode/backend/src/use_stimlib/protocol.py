"""④ 实验流程：用哪些场景、什么顺序、各阶段多长。存于 Stimulus/protocol.json。

一次实验（会话）的结构：
    静息基线 rest_s（灰屏，整场一次，作个人校准）
    对每个场景：注视 fixation_s → 刺激 stimulus_s → 自评 → 恢复 recovery_s
    结束

order：
    fixed    按场景列表顺序（场景1 → 场景2 → …）
    random   每名受试者按种子随机打乱，约束同一条件不连续超过 2 次（推荐：排除顺序效应）
"""
import json
import random
import threading

from .. import config
from . import library, scenes

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
}
_NUM_KEYS = ("rest_s", "fixation_s", "stimulus_s", "rating_timeout_s", "recovery_s", "ack_timeout_s")


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
    if p["stimulus_s"] < 1:
        raise ValueError("刺激时长至少 1 秒")
    with _LOCK:
        PROTOCOL_PATH.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def resolve(p: dict | None = None) -> dict:
    """把流程展开成可执行的试次列表，并检查每个场景能否播放。

    返回 {"items": [...按场景列表顺序...], "problems": [...]}；problems 非空就不能开始实验。
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
        stim = library.get_stimulus(f"{s['selected']['pair_id']}:{p['line']}")
        if not stim:
            problems.append(f"{label} 选定的那一对没有{'视频' if p['line'] == 'video' else '图片'}")
            continue
        items.append({**stim, "scene_id": s["id"], "scene_no": s["no"], "scene_name": s["name"],
                      "condition": s["condition"]})
    if not items and not problems:
        problems.append("流程里没有场景")
    return {"items": items, "problems": problems}


def playlist(p: dict, seed: int) -> list:
    """按 order 生成本次会话的试次顺序。"""
    items = resolve(p)["items"]
    if p["order"] == "fixed":
        return items
    rng = random.Random(seed)
    for _ in range(300):
        rng.shuffle(items)
        if _cond_runs_ok(items):
            break
    return items


def _cond_runs_ok(items: list) -> bool:
    run = 1
    for a, b in zip(items, items[1:]):
        run = run + 1 if a["condition"] == b["condition"] else 1
        if run > 2:
            return False
    return True


def estimate_s(p: dict, n: int) -> float:
    """整场大致时长（自评按 15 秒估）。"""
    per = p["fixation_s"] + p["stimulus_s"] + 15 + p["recovery_s"]
    return p["rest_s"] + n * per
