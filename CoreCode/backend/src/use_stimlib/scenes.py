"""场景预设：场景1、场景2、场景3……由实验者事先定好，取代原来「刺激种子 + DeepSeek 动态提示词」。

每个场景是实验里的一个固定刺激位置：
    id            稳定 id（S01、S02…），生成的图、评分、FEA 都靠它关联，改名、改序不影响
    name          场景名（给人看，如「黄昏海滩」）
    condition     目标条件（HVHA / HVLA / LVHA / LVLA / NEU），用于操纵检验
    prompt        画面描述（中英文均可；生成时自动补 360 全景前缀）
    motion        视频线才用：画面里什么在动
    selected_pair 选定作为正式刺激的那一对（③ 选图）
    template      从内置模板创建时记录来源（conditions.SCENES 的 "<条件>/<序号>"）

顺序即列表顺序（场景1 就是第一个）。存于 Stimulus/scenes.json。
"""
import json
import threading

from .. import config
from . import conditions, library

SCENES_PATH = config.IMAGE_DIR / "scenes.json"
_LOCK = threading.Lock()

# 首次使用时的默认 10 个场景：每个条件 2 个，取自内置模板
_DEFAULT_TEMPLATES = [("HVLA", 0), ("HVHA", 0), ("NEU", 0), ("LVLA", 0), ("LVHA", 0),
                      ("HVLA", 2), ("HVHA", 2), ("NEU", 2), ("LVLA", 1), ("LVHA", 1)]


def _from_template(cond: str, idx: int, sid: str) -> dict:
    t = conditions.SCENES[cond][idx]
    return {"id": sid, "name": t["zh"], "condition": cond, "prompt": t["scene"], "motion": t["motion"],
            "selected_pair": "", "template": f"{cond}/{idx}", "note": ""}


def _load() -> list:
    if SCENES_PATH.exists():
        try:
            data = json.loads(SCENES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
    scenes = [_from_template(c, i, f"S{n + 1:02d}") for n, (c, i) in enumerate(_DEFAULT_TEMPLATES)]
    _save(scenes)
    _adopt_legacy_pairs(scenes)
    return scenes


def _save(scenes: list) -> None:
    tmp = SCENES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(scenes, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SCENES_PATH)


def _adopt_legacy_pairs(scenes: list) -> None:
    """场景功能上线前生成的对没有 scene_id：按 (条件, 模板序号) 归到对应的默认场景。"""
    by_tpl = {s["template"]: s["id"] for s in scenes if s.get("template")}
    for m in library.list_pairs():
        if m.get("scene_id"):
            continue
        sid = by_tpl.get(f"{m.get('condition')}/{m.get('scene_idx')}")
        if sid:
            meta = library.read_meta(m["pair_id"])
            meta["scene_id"] = sid
            library.write_meta(m["pair_id"], meta)


def _next_id(scenes: list) -> str:
    nums = [int(s["id"][1:]) for s in scenes if s["id"][1:].isdigit()]
    return f"S{(max(nums) + 1) if nums else 1:02d}"


def _clean(s: dict, base: dict) -> dict:
    out = dict(base)
    for k in ("name", "prompt", "motion", "note"):
        if k in s:
            out[k] = str(s[k] or "").strip()[:2000]
    if "condition" in s:
        if s["condition"] not in conditions.CONDITION_KEYS:
            raise ValueError(f"条件只能是 {', '.join(conditions.CONDITION_KEYS)}")
        out["condition"] = s["condition"]
    if not out.get("name"):
        raise ValueError("场景名不能为空")
    if not out.get("prompt"):
        raise ValueError("画面描述不能为空")
    return out


def list_scenes() -> list:
    """场景列表，附带序号（no = 场景几）与该场景下已生成的候选数、选定图的地址。"""
    with _LOCK:
        scenes = _load()
    pairs = library.list_pairs()
    by_scene = {}
    for m in pairs:
        by_scene.setdefault(m.get("scene_id", ""), []).append(m)
    out = []
    for n, s in enumerate(scenes):
        cands = by_scene.get(s["id"], [])
        sel = next((m for m in cands if m["pair_id"] == s.get("selected_pair")), None)
        out.append({**s, "no": n + 1, "n_candidates": len(cands),
                    "selected": {"pair_id": sel["pair_id"], "urls": sel["urls"], "has_video": bool(sel["urls"].get("video"))}
                    if sel else None})
    return out


def get(scene_id: str) -> dict | None:
    with _LOCK:
        return next((s for s in _load() if s["id"] == scene_id), None)


def create(body: dict) -> list:
    with _LOCK:
        scenes = _load()
        tpl = body.get("template")
        if tpl:
            cond, _, idx = str(tpl).partition("/")
            if cond not in conditions.SCENES or not idx.isdigit() or int(idx) >= len(conditions.SCENES[cond]):
                raise ValueError("无效的模板")
            new = _from_template(cond, int(idx), _next_id(scenes))
        else:
            new = _clean(body, {"id": _next_id(scenes), "condition": "NEU", "motion": "", "selected_pair": "",
                                "template": "", "note": ""})
        scenes.append(new)
        _save(scenes)
    return list_scenes()


def update(scene_id: str, body: dict) -> list:
    with _LOCK:
        scenes = _load()
        for i, s in enumerate(scenes):
            if s["id"] == scene_id:
                scenes[i] = _clean(body, s)
                break
        else:
            raise ValueError("场景不存在")
        _save(scenes)
    return list_scenes()


def delete(scene_id: str) -> list:
    """只删场景定义；已生成的图留在刺激库里（可能已被评过分，不能悄悄丢）。"""
    with _LOCK:
        scenes = [s for s in _load() if s["id"] != scene_id]
        _save(scenes)
    return list_scenes()


def reorder(ids: list) -> list:
    with _LOCK:
        scenes = _load()
        by_id = {s["id"]: s for s in scenes}
        if sorted(ids) != sorted(by_id):
            raise ValueError("排序列表必须包含全部场景且不重复")
        _save([by_id[i] for i in ids])
    return list_scenes()


def select_pair(scene_id: str, pair_id: str) -> list:
    """③ 选图：把某一对设为该场景的正式刺激。pair_id 为空 = 取消选定。"""
    if pair_id:
        meta = library.read_meta(pair_id)
        if not meta or meta.get("scene_id") != scene_id:
            raise ValueError("这张图不属于该场景")
        if meta.get("qc") == "reject":
            raise ValueError("这张图已被质检剔除，不能选用")
    with _LOCK:
        scenes = _load()
        for s in scenes:
            if s["id"] == scene_id:
                s["selected_pair"] = pair_id
                break
        else:
            raise ValueError("场景不存在")
        _save(scenes)
    return list_scenes()


def templates() -> list:
    return [{"template": f"{c}/{i}", "condition": c, "name": t["zh"], "prompt": t["scene"]}
            for c, items in conditions.SCENES.items() for i, t in enumerate(items)]


def prompts(scene: dict) -> tuple:
    """场景 → (全景正向提示词, 视频正向提示词)。"""
    p = scene["prompt"].strip()
    pano = p if p.lower().startswith("360 panorama") else conditions.PANO_PREFIX + p
    motion = scene.get("motion", "").strip() or "subtle ambient motion"
    video = (f"{p}. {motion}. static camera, fixed viewpoint, no camera movement, "
             "360 degree equirectangular panorama")
    return pano, video
