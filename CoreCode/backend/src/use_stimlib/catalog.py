"""统一刺激清单（刺激重建方案 B.3）：每个刺激一个 catalog/<id>.json，目录本身就是真源。

    Stimulus/catalog/<id>.json        清单条目（字段见 _DEFAULTS）
    Stimulus/catalog/assets/<file>    视频 / 模仿片段等素材，经 /api/stimulus/files 静态服务给头显下载

kind（头显据此选呈现方式，见契约 contract v2）：
    skybox_image / skybox_video   360° 全景图 / 视频（天空盒）
    scene                         打进 APK 的 Unity 场景（prefab_key 查 StimulusSceneRegistry），没有 url
    mimicry                       正前方平面上播放的面孔 / 人声片段（media=video）
trial_class 决定试次模板（trialtypes.py）：immersive / event / mimicry。

现有图片库（library.py）的刺激不复制进来，而是按 id 自动映射：
    "<pair_id>:image" → skybox_image，"<pair_id>:video" → skybox_video，trial_class=immersive，rating=full。
所以 get_stimulus(id) 是全后端唯一的刺激查找入口：先查 catalog，再查图片库。

events 里可带抖动：{key, t_s, jitter_s: [lo, hi]}；to_headset 按种子确定性地算出实际时刻
（同一受试者同一种子重放得到同样的时刻，不同受试者不同，避免可预期）。
"""
import hashlib
import json
import os
import random
import threading

from .. import config
from ..safe_path import is_safe_id
from . import conditions, library

CATALOG_DIR = config.IMAGE_DIR / "catalog"
ASSETS_DIR = CATALOG_DIR / "assets"
FILES_URL = "/api/stimulus/files/catalog/assets"      # 复用 IMAGE_DIR 的静态服务（头显下载前缀已放行）
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

KINDS = ("skybox_image", "skybox_video", "scene", "mimicry")
TRIAL_CLASSES = ("immersive", "event", "mimicry")
RATINGS = ("full", "quick", "none")
QCS = ("pending", "ok", "reject")
_KIND_CLASS = {"skybox_image": "immersive", "skybox_video": "immersive", "scene": "event", "mimicry": "mimicry"}
_CLASS_RATING = {"immersive": "full", "event": "quick", "mimicry": "quick"}

_DEFAULTS = {
    "id": "", "name": "", "kind": "skybox_image", "trial_class": "", "rating": "",
    "condition": "NEU",           # 5 条件（HVHA / HVLA / LVHA / LVLA / NEU），播放顺序约束与操纵检验用
    "target_emotion": "",         # 7 类之一或空
    "audio": False, "prefab_key": "", "file": "", "duration_s": 0.0,
    "events": [],                 # [{key, t_s, jitter_s?: [lo, hi]}]
    "params": {},                 # kind 专用参数，给头显时序列化成 params_json
    "source": "", "license": "",
    "qc": "pending", "enabled": True,
    "norms": {"valence_mean": None, "arousal_mean": None, "valence_sd": None, "arousal_sd": None,
              "n": 0, "source": ""},
    "ethics_tier": 0,             # 0 = 普通；1 = 需提示；2 = 惊吓 / 强负性，需单独同意
    "habituation_group": "",      # 同组刺激（如 startle）在播放顺序里尽量隔开
    "note": "",
}

_LOCK = threading.Lock()
_sha_cache: dict = {}             # 素材路径 → (mtime_ns, size, sha1)


# ── 读写 ────────────────────────────────────────────────────────────
def _path(item_id: str):
    if not is_safe_id(item_id):
        raise ValueError("非法 id（只能是字母、数字、_、-）")
    return CATALOG_DIR / f"{item_id}.json"


def _normalize(raw: dict, item_id: str) -> dict:
    """补默认值 + 推出 trial_class / rating 的默认（按 kind）。"""
    it = {**_DEFAULTS, **raw, "id": item_id}
    it["norms"] = {**_DEFAULTS["norms"], **(raw.get("norms") or {})}
    if not it["trial_class"]:
        it["trial_class"] = _KIND_CLASS.get(it["kind"], "immersive")
    if not it["rating"]:
        it["rating"] = _CLASS_RATING.get(it["trial_class"], "full")
    return it


def _validate(it: dict) -> None:
    if it["kind"] not in KINDS:
        raise ValueError(f"kind 只能是 {' / '.join(KINDS)}")
    if it["trial_class"] not in TRIAL_CLASSES:
        raise ValueError(f"trial_class 只能是 {' / '.join(TRIAL_CLASSES)}")
    if it["rating"] not in RATINGS:
        raise ValueError(f"rating 只能是 {' / '.join(RATINGS)}")
    if it["qc"] not in QCS:
        raise ValueError("qc 只能是 pending / ok / reject")
    if it["condition"] not in conditions.CONDITION_KEYS:
        raise ValueError(f"condition 只能是 {', '.join(conditions.CONDITION_KEYS)}")
    if it["target_emotion"] and it["target_emotion"] not in config.TRAIN_CLASSES:
        raise ValueError("target_emotion 必须是 7 类之一或留空")
    if int(it["ethics_tier"]) not in (0, 1, 2):
        raise ValueError("ethics_tier 只能是 0 / 1 / 2")
    if it["file"] and (os.path.basename(it["file"]) != it["file"] or it["file"].startswith(".")):
        raise ValueError("file 只能是 catalog/assets/ 下的文件名")
    for e in it["events"]:
        if not isinstance(e, dict) or not e.get("key"):
            raise ValueError("events 每项需要 key 与 t_s")
        float(e.get("t_s", 0))


def _read(item_id: str) -> dict | None:
    p = _path(item_id)
    if not p.exists():
        return None
    try:
        return _normalize(json.loads(p.read_text(encoding="utf-8")), item_id)
    except (OSError, ValueError):
        return None


def save(item: dict) -> dict:
    """写一个条目（整条替换）。"""
    item_id = str(item.get("id") or "")
    it = _normalize({k: v for k, v in item.items() if k in _DEFAULTS}, item_id)
    _validate(it)
    p = _path(item_id)
    with _LOCK:
        CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(it, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    return it


def patch(item_id: str, body: dict) -> dict | None:
    """网页质检：改 qc / enabled / note。图片库的刺激（"<pair_id>:<line>"）只能改 qc，转给 library.set_qc。"""
    if ":" in item_id:
        if "qc" not in body:
            raise ValueError("图片库刺激只能改 qc（启用 / 停用请在场景里换选图）")
        m = library.set_qc(item_id.partition(":")[0], str(body["qc"]), str(body.get("note") or ""))
        return get(item_id) if m else None
    with _LOCK:
        it = _read(item_id)
    if not it:
        return None
    if "qc" in body:
        it["qc"] = str(body["qc"])
    if "enabled" in body:
        it["enabled"] = bool(body["enabled"])
    if "note" in body:
        it["note"] = str(body["note"] or "")[:200]
    return save(it)


def list_items(kind: str | None = None, qc: str | None = None, include_library: bool = False) -> list:
    """catalog 目录里的全部条目（按 id 排序）；include_library 时附上图片库映射出的条目。"""
    out = []
    if CATALOG_DIR.exists():
        for p in sorted(CATALOG_DIR.glob("*.json")):
            if not is_safe_id(p.stem):
                continue
            it = _read(p.stem)
            if it:
                out.append(it)
    if include_library:
        out += [_from_library(s, m.get("qc", "pending")) for m in library.list_pairs() for s in m["stimuli"]]
    return [it for it in out if (not kind or it["kind"] == kind) and (not qc or it["qc"] == qc)]


# ── 查找（catalog 优先，再图片库）──────────────────────────────────
def _from_library(stim: dict, qc: str = "pending") -> dict:
    kind = "skybox_video" if stim.get("media") == "video" else "skybox_image"
    return _normalize({"name": stim["id"], "kind": kind, "trial_class": "immersive", "rating": "full",
                       "condition": stim.get("condition") or "NEU", "qc": qc, "enabled": True,
                       "source": "library", "_lib": stim}, stim["id"])


def get(item_id: str) -> dict | None:
    """catalog 条目，或图片库刺激映射出的条目（带 _lib = 原刺激对象）。"""
    item_id = str(item_id or "")
    if ":" in item_id:
        stim = library.get_stimulus(item_id)
        if not stim:
            return None
        try:
            meta = library.read_meta(stim["pair_id"]) or {}
        except ValueError:
            meta = {}
        return _from_library(stim, meta.get("qc", "pending"))
    try:
        return _read(item_id)
    except ValueError:
        return None


def get_stimulus(stim_id: str, seed: int = 0) -> dict | None:
    """id → 发给头显的刺激对象（全后端唯一的查找入口）。"""
    it = get(stim_id)
    return to_headset(it, seed) if it else None


def asset_path(it: dict):
    if not it.get("file"):
        return None
    p = (ASSETS_DIR / it["file"]).resolve()
    if not p.is_relative_to(ASSETS_DIR.resolve()):
        return None
    return p


def _sha1(path) -> str:
    """素材文件 sha1（头显校验缓存用），按 mtime + 大小缓存，避免每次轮询都重算大视频。"""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = str(path)
    hit = _sha_cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    _sha_cache[key] = (st.st_mtime_ns, st.st_size, h.hexdigest())
    return _sha_cache[key][2]


def _jittered_events(it: dict, seed: int) -> list:
    """events 的抖动按 (种子, id) 确定性展开；结果按时刻排序，限制在 [0, duration_s]。"""
    rng = random.Random(f"{seed}:{it['id']}")
    dur = float(it.get("duration_s") or 0)
    out = []
    for e in it.get("events") or []:
        t = float(e.get("t_s", 0))
        j = e.get("jitter_s")
        if isinstance(j, (list, tuple)) and len(j) == 2:
            t += rng.uniform(float(j[0]), float(j[1]))
        t = max(0.0, min(t, dur) if dur > 0 else t)
        out.append({"key": str(e["key"]), "t_s": round(t, 3)})
    out.sort(key=lambda e: e["t_s"])
    return out


def to_headset(it: dict, seed: int = 0) -> dict:
    """条目 → 契约里的刺激对象（state.stimulus / upcoming / prefetch 共用）。"""
    extra = {
        "kind": it["kind"], "trial_class": it["trial_class"], "rating": it["rating"], "audio": bool(it["audio"]),
        "prefab_key": it.get("prefab_key") or "", "target_emotion": it.get("target_emotion") or "",
        "events": _jittered_events(it, seed),
        "params_json": json.dumps(it["params"], ensure_ascii=False) if it.get("params") else "",
        "duration_s": float(it.get("duration_s") or 0), "habituation_group": it.get("habituation_group") or "", "ethics_tier": int(it.get("ethics_tier") or 0),
    }
    if it.get("_lib"):
        return {**it["_lib"], **extra}
    p = asset_path(it)
    has_file = p is not None and p.is_file()
    media = "video" if it["kind"] in ("skybox_video", "mimicry") else ("image" if it["kind"] == "skybox_image" else "")
    return {"id": it["id"], "pair_id": it["id"], "line": "catalog", "media": media,
            "url": f"{FILES_URL}/{it['file']}" if has_file else "", "sha": _sha1(p) if has_file else "",
            "condition": it["condition"], "scene_id": "", "scene_name": it.get("name") or it["id"], **extra}


def problems_of(it: dict | None, item_id: str) -> list:
    """一个条目能不能进实验：不存在 / 已剔除 / 已停用 / 素材缺失。"""
    if not it:
        return [f"刺激 {item_id} 不存在"]
    if it["qc"] == "reject":
        return [f"刺激 {item_id} 已被质检剔除"]
    if not it.get("enabled", True):
        return [f"刺激 {item_id} 已停用"]
    if it["kind"] == "scene" and not it.get("prefab_key"):
        return [f"场景刺激 {item_id} 没有 prefab_key"]
    if it["kind"] != "scene" and not it.get("_lib"):
        p = asset_path(it)
        if p is None or not p.is_file():
            return [f"刺激 {item_id} 的素材 {it.get('file') or '(未填)'} 不在 catalog/assets/"]
    return []

