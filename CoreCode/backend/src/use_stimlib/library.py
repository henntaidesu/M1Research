"""刺激库：每对刺激一个目录 + meta.json；自评追加写 ratings.csv。

    lib/<pair_id>/meta.json   生成参数、提示词、种子、模型、低层特征、质检状态
    lib/<pair_id>/pano.png    原始全景（存档用，不直接作刺激）
    lib/<pair_id>/still.jpg   图片线刺激
    lib/<pair_id>/video.mp4   视频线刺激
    lib/<pair_id>/thumb.jpg   缩略图

清单不单独维护一份总表，而是每次扫描 meta.json 拼出来：生成任务和网页删除并发时，
总表很容易和磁盘对不上，目录本身才是真源。

刺激 id = "<pair_id>:image" / "<pair_id>:video"。
"""
import csv
import json
import shutil
import threading
import time

from .. import config

LINES = ("image", "video")
_LOCK = threading.Lock()

RATINGS_PATH = config.STIMLIB_DIR / "ratings.csv"
RATING_FIELDS = ["ts_ms", "run_id", "subject_id", "cb_group", "trial", "stimulus_id", "pair_id", "line",
                 "condition", "valence", "arousal", "label7", "rating_ms"]

FILES_URL = "/api/stimulus/files/lib"


def pair_dir(pair_id: str):
    d = (config.STIMLIB_DIR / pair_id).resolve()
    if not d.is_relative_to(config.STIMLIB_DIR.resolve()) or d == config.STIMLIB_DIR.resolve():
        raise ValueError("非法 pair_id")
    return d


def new_pair_id(cond: str, seq: int) -> str:
    return f"{cond}_{time.strftime('%Y%m%d_%H%M%S')}_{seq:03d}"


def write_meta(pair_id: str, meta: dict) -> None:
    d = pair_dir(pair_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "meta.json")


def read_meta(pair_id: str) -> dict | None:
    p = pair_dir(pair_id) / "meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _with_urls(meta: dict) -> dict:
    pid = meta["pair_id"]
    files = meta.get("files") or {}
    urls = {k: f"{FILES_URL}/{pid}/{v}" for k, v in files.items() if v}
    return {**meta, "urls": urls, "stimuli": stimuli_of(meta)}


def stimuli_of(meta: dict) -> list:
    """一对 → 可呈现的刺激列表（视频没生成成功的只有图片线）。"""
    pid = meta["pair_id"]
    files = meta.get("files") or {}
    sha = meta.get("sha") or {}
    out = []
    if files.get("still"):
        out.append({"id": f"{pid}:image", "pair_id": pid, "line": "image", "media": "image",
                    "url": f"{FILES_URL}/{pid}/{files['still']}", "sha": sha.get("still", ""),
                    "condition": meta.get("condition")})
    if files.get("video"):
        out.append({"id": f"{pid}:video", "pair_id": pid, "line": "video", "media": "video",
                    "url": f"{FILES_URL}/{pid}/{files['video']}", "sha": sha.get("video", ""),
                    "condition": meta.get("condition")})
    return out


def list_pairs(condition: str = "", qc: str = "") -> list:
    items = []
    if not config.STIMLIB_DIR.exists():
        return items
    for d in sorted(config.STIMLIB_DIR.iterdir()):
        if not d.is_dir() or d.name == "runs":
            continue
        meta = read_meta(d.name)
        if not meta:
            continue
        if condition and meta.get("condition") != condition:
            continue
        if qc and meta.get("qc", "pending") != qc:
            continue
        items.append(_with_urls(meta))
    items.sort(key=lambda m: m.get("created_ms", 0))
    return items


def get_stimulus(stim_id: str) -> dict | None:
    pid, _, line = stim_id.partition(":")
    if not pid or line not in LINES:
        return None
    try:
        meta = read_meta(pid)
    except ValueError:
        return None
    if not meta:
        return None
    for s in stimuli_of(meta):
        if s["line"] == line:
            return s
    return None


def set_qc(pair_id: str, qc: str, note: str = "") -> dict | None:
    if qc not in ("pending", "ok", "reject"):
        raise ValueError("qc 只能是 pending / ok / reject")
    with _LOCK:
        meta = read_meta(pair_id)
        if not meta:
            return None
        meta["qc"] = qc
        meta["qc_note"] = note[:200]
        write_meta(pair_id, meta)
    return _with_urls(meta)


def delete_pair(pair_id: str) -> bool:
    d = pair_dir(pair_id)
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def append_rating(row: dict) -> None:
    with _LOCK:
        new = not RATINGS_PATH.exists()
        with open(RATINGS_PATH, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RATING_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)


def read_ratings() -> list:
    if not RATINGS_PATH.exists():
        return []
    with open(RATINGS_PATH, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
