"""「情感偏好生成」的存储服务层（CSV 文件，替代原 Postgres/pgvector 的 pg_store）。

对外函数与 pg_store 同名同参，app.py 的 /api/affect/* 端点直接调用。落盘布局：

  <AFFECT_DATA_DIR>/<测试者名>/
      sessions.csv                   该测试者的采集会话（session_id, 起止, meta）
      <session_id>_fea.csv           FEA 时间轴：ts_ms, f0..f69, 7 类情绪概率, dominant, source
      <session_id>_selfreport.csv    受试者自评：ts_ms, image_id, label7, valence, arousal, source
      images.csv                     生成图偏好：7 维情绪情境 + 提示词 + 反应 + 是否喜欢
      seeds.csv                      种子档案：自填先验 + 闭环实测累计

写入一律「打开→追加→关闭」，不常驻句柄，进程崩溃也不丢已写数据；改写类操作
（stop_session / 种子删改）整文件重写。所有读写由 _LOCK 串行化。
未知会话 / 文件损坏等抛 RuntimeError，由路由层转 HTTP 错误（与 pg_store 一致）。
"""
import csv
import json
import logging
import math
import random
import re
import threading
import time

from .. import config

FEA_DIM = config.FEA_DIM
EMOTION_ORDER = config.TRAIN_CLASSES  # angry disgust fear happy neutral sad surprise（7）

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_session_subject: dict = {}   # session_id -> 测试者目录名（缓存，避免每帧扫目录）

_SESSION_HEADER = ["session_id", "subject_id", "started_ms", "stopped_ms", "meta"]
_FEA_HEADER = (["ts_ms"] + [f"f{i}" for i in range(FEA_DIM)]
               + [f"emo_{k}" for k in EMOTION_ORDER] + ["dominant", "source"])
_SELFREPORT_HEADER = ["ts_ms", "image_id", "label7", "valence", "arousal", "source"]
_IMAGE_HEADER = (["id", "session_id", "ts_ms", "dominant"] + [f"ctx_{k}" for k in EMOTION_ORDER]
                 + ["prompt", "negative", "scene", "reasoning", "image_url", "image_path",
                    "liked", "reaction"])
_SEED_HEADER = ["id", "subject_id", "scene", "expected", "expected_level",
                "obs_n", "obs_sum", "created_ms", "updated_ms"]


# ── 文件工具 ──────────────────────────────────────────────────────────
def _subject_dir_name(subject_id) -> str:
    """测试者名 → 目录名。允许中文等任意字符，只去掉路径/Windows 非法字符，杜绝 ../ 逃逸。"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(subject_id or "")).strip().strip(".")
    return s or "anon"


def _root():
    config.AFFECT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.AFFECT_DATA_DIR


def _subject_path(subject_id):
    p = _root() / _subject_dir_name(subject_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _append(path, header, rows) -> None:
    new = not path.exists()
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def _read(path) -> list:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _rewrite(path, header, dict_rows) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(dict_rows)
    tmp.replace(path)


def _subject_dirs():
    return [p for p in _root().iterdir() if p.is_dir()]


def _find_subject(session_id: str):
    """session_id → 测试者目录。先查缓存，再扫各测试者的 sessions.csv。"""
    if session_id in _session_subject:
        return _root() / _session_subject[session_id]
    for d in _subject_dirs():
        if any(r["session_id"] == session_id for r in _read(d / "sessions.csv")):
            _session_subject[session_id] = d.name
            return d
    return None


def _session_dir(session_id: str):
    d = _find_subject(session_id)
    if d is None:
        raise RuntimeError(f"会话 {session_id} 不存在")
    return d


def _count_rows(path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)


def _num(v):
    return "" if v is None else v


def is_configured() -> bool:
    return True   # 本地文件存储，无需配置


def _context_from_emotions(emotions) -> list | None:
    if emotions is None:
        return None
    if isinstance(emotions, dict):
        return [float(emotions.get(k, 0.0)) for k in EMOTION_ORDER]
    if isinstance(emotions, (list, tuple)) and len(emotions) == len(EMOTION_ORDER):
        return [float(x) for x in emotions]
    return None


# ── 会话生命周期 ──────────────────────────────────────────────────────
def start_session(subject_id: str, meta: dict | None = None) -> dict:
    with _LOCK:
        d = _subject_path(subject_id)
        session_id = "s" + time.strftime("%Y%m%d_%H%M%S")
        base, n = session_id, 1
        while _find_subject(session_id) is not None:
            session_id = f"{base}_{n}"
            n += 1
        started = int(time.time() * 1000)
        _append(d / "sessions.csv", _SESSION_HEADER, [[
            session_id, subject_id or "anon", started, "",
            json.dumps(meta or {}, ensure_ascii=False)]])
        _session_subject[session_id] = d.name
    return {"ok": True, "session_id": session_id, "started_ms": started,
            "dir": str(d.relative_to(config.ROOT)) if d.is_relative_to(config.ROOT) else str(d)}


def stop_session(session_id: str) -> dict:
    with _LOCK:
        d = _session_dir(session_id)
        rows = _read(d / "sessions.csv")
        for r in rows:
            if r["session_id"] == session_id:
                r["stopped_ms"] = int(time.time() * 1000)
        _rewrite(d / "sessions.csv", _SESSION_HEADER, rows)
        fea_n = _count_rows(d / f"{session_id}_fea.csv")
        img_n = sum(1 for r in _read(d / "images.csv") if r["session_id"] == session_id)
    return {"ok": True, "session_id": session_id, "fea_count": fea_n, "image_count": img_n}


# ── 写入 ──────────────────────────────────────────────────────────────
def insert_fea(session_id: str, rows: list) -> int:
    """批量写 FEA 时间轴。rows: [{ts_ms, blendshapes?[70], emotions?, dominant?, source?}]。"""
    now = int(time.time() * 1000)
    prepared = []
    skipped = 0
    for r in rows or []:
        bs = r.get("blendshapes")
        if bs is not None and len(bs) != FEA_DIM:
            skipped += 1        # 错维 FEA：跳过而非写空列损坏时间轴
            continue
        emo = _context_from_emotions(r.get("emotions"))
        prepared.append(
            [int(r.get("ts_ms") or now)]
            + ([round(float(x), 5) for x in bs] if bs is not None else [""] * FEA_DIM)
            + ([round(x, 5) for x in emo] if emo is not None else [""] * len(EMOTION_ORDER))
            + [_num(r.get("dominant")), _num(r.get("source"))])
    if skipped:
        logger.warning("insert_fea: 跳过 %d 行错误维度 FEA（期望 %d 维）", skipped, FEA_DIM)
    if not prepared:
        return 0
    with _LOCK:
        d = _session_dir(session_id)
        _append(d / f"{session_id}_fea.csv", _FEA_HEADER, prepared)
    return len(prepared)


def insert_selfreport(session_id: str, rows: list) -> int:
    """批量写受试者自评。rows: [{ts_ms, image_id?, label7, valence?, arousal?, source?}]。

    自评是**唯一独立于 FEA 的真值来源**：规则式 classify_fea 与 FEA 同源，不能既当输入
    又当标签。label7 非法的行拒绝写入，避免污染标签空间。
    """
    now = int(time.time() * 1000)
    prepared, bad = [], 0

    def _clamp(v):
        """SAM 取 1–9；越界或非数字一律记空，不硬塞。"""
        if v is None or v == "":
            return ""
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return ""
        return iv if 1 <= iv <= 9 else ""

    for r in rows or []:
        label = str(r.get("label7") or "").strip().lower()
        if label and label not in EMOTION_ORDER:
            bad += 1
            continue
        prepared.append([
            int(r.get("ts_ms") or now),
            str(r["image_id"]) if r.get("image_id") is not None else "",
            label, _clamp(r.get("valence")), _clamp(r.get("arousal")), _num(r.get("source")),
        ])
    if bad:
        logger.warning("insert_selfreport: 跳过 %d 行非法 label7（须为 %s 之一）",
                       bad, "/".join(EMOTION_ORDER))
    if not prepared:
        return 0
    with _LOCK:
        d = _session_dir(session_id)
        _append(d / f"{session_id}_selfreport.csv", _SELFREPORT_HEADER, prepared)
    return len(prepared)


def _all_images(subject_dirs=None) -> list:
    out = []
    for d in subject_dirs if subject_dirs is not None else _subject_dirs():
        out.extend(_read(d / "images.csv"))
    return out


def insert_image(rec: dict) -> dict:
    """写一条生成图偏好记录。无会话的记录归到 anon 测试者目录。"""
    context = _context_from_emotions(rec.get("context") or rec.get("emotions"))
    liked = rec.get("liked")
    sid = rec.get("session_id")
    with _LOCK:
        d = (_find_subject(sid) if sid else None) or _subject_path("anon")
        new_id = max((int(r["id"]) for r in _all_images() if r.get("id")), default=0) + 1
        _append(d / "images.csv", _IMAGE_HEADER, [
            [new_id, _num(sid), int(rec.get("ts_ms") or time.time() * 1000), _num(rec.get("dominant"))]
            + (context if context is not None else [""] * len(EMOTION_ORDER))
            + [_num(rec.get(k)) for k in ("prompt", "negative", "scene", "reasoning",
                                          "image_url", "image_path")]
            + ["" if liked is None else bool(liked),
               json.dumps(rec.get("reaction"), ensure_ascii=False) if rec.get("reaction") is not None else ""]])
    return {"ok": True, "id": new_id}


# ── 检索（偏好闭环）───────────────────────────────────────────────────
def _cos_dist(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - sum(x * y for x, y in zip(a, b)) / (na * nb)


def recommend(emotions, k: int = 5, subject_id: str | None = None,
              epsilon: float | None = None) -> dict:
    """按当前情绪情境检索相似的生成图。

    - **距离用 cosine**：context 是 7 类情绪概率，位于单纯形上，L2 会把「强度」当成差异。
    - **保留 ε-探索**：默认 20% 的名额从全库随机取（不限于 liked），否则检索只在
      「喜欢过」的集合里打转，且无法与随机基线做 A/B。liked 池为空时退化为纯随机。

    每项带 `source`: exploit=最近邻 / explore=随机。
    """
    context = _context_from_emotions(emotions)
    if context is None:
        return {"items": []}
    eps = config.AFFECT_EPSILON if epsilon is None else float(epsilon)
    eps = min(max(eps, 0.0), 1.0)
    k = max(0, int(k))
    n_explore = int(round(k * eps))
    n_exploit = k - n_explore

    with _LOCK:
        dirs = None
        if subject_id:
            p = _root() / _subject_dir_name(subject_id)
            dirs = [p] if p.is_dir() else []
        rows = _all_images(dirs)

    pool = []
    for r in rows:
        try:
            ctx = [float(r[f"ctx_{e}"]) for e in EMOTION_ORDER]
        except (KeyError, ValueError):
            continue   # 无情绪情境的记录不参与检索
        pool.append({
            "id": int(r["id"]), "dominant": r["dominant"] or None, "prompt": r["prompt"] or None,
            "negative": r["negative"] or None, "scene": r["scene"] or None,
            "image_url": r["image_url"] or None, "dist": _cos_dist(context, ctx),
            "liked": r["liked"] == "True",
        })

    items, seen = [], set()
    if n_exploit:
        for d in sorted((p for p in pool if p["liked"]), key=lambda p: p["dist"])[:n_exploit]:
            items.append({**d, "source": "exploit"})
            seen.add(d["id"])

    # 探索名额（含 exploit 未取满时的补位）从全库随机取，保证不与已选重复
    rest = [p for p in pool if p["id"] not in seen]
    random.shuffle(rest)
    for d in rest[:k - len(items)]:
        items.append({**d, "source": "explore"})
    for it in items:
        it.pop("liked", None)
    return {"items": items, "epsilon": eps, "metric": "cosine"}


def list_sessions(limit: int = 100) -> dict:
    sessions = []
    with _LOCK:
        for d in _subject_dirs():
            images = _read(d / "images.csv")
            for r in _read(d / "sessions.csv"):
                sid = r["session_id"]
                sessions.append({
                    "session_id": sid, "subject_id": r["subject_id"],
                    "started_ms": int(r["started_ms"]),
                    "stopped_ms": int(r["stopped_ms"]) if r["stopped_ms"] else None,
                    "fea_count": _count_rows(d / f"{sid}_fea.csv"),
                    "image_count": sum(1 for i in images if i["session_id"] == sid),
                    "selfreport_count": _count_rows(d / f"{sid}_selfreport.csv"),
                })
    sessions.sort(key=lambda s: s["started_ms"], reverse=True)
    sessions = sessions[:limit]
    # 没有自评的会话只能配循环标签，不能用于正式训练——显式标出来，别让人以为能用
    for s in sessions:
        s["trainable"] = bool(s["selfreport_count"])
    return {"sessions": sessions}


# ── 受试者种子档案（自填先验 + 实测反应 → 预测）──────────────────────
# 先验折合成 SEED_PRIOR_K 次「虚拟观测」：实测次数少时以受试者自填为主，
# 越测越多就越由实测说话（即 Dirichlet 均值式的平滑）。
SEED_PRIOR_K = 2.0


def _seed_prior(expected, level) -> dict:
    """自填预期 → 7 类先验分布：预期情绪占 0.3（强度 1）… 0.9（强度 5），其余均分。"""
    n = len(EMOTION_ORDER)
    if expected not in EMOTION_ORDER:
        return {k: 1.0 / n for k in EMOTION_ORDER}
    lv = min(max(int(level or 3), 1), 5)
    p_e = 0.15 + 0.15 * lv
    rest = (1.0 - p_e) / (n - 1)
    return {k: (p_e if k == expected else rest) for k in EMOTION_ORDER}


def _seed_dict(m: dict) -> dict:
    n = int(m["obs_n"] or 0)
    sums = json.loads(m["obs_sum"]) if m["obs_sum"] else {}
    expected = m["expected"] or None
    level = int(m["expected_level"]) if m["expected_level"] else None
    prior = _seed_prior(expected, level)
    pred = {k: (SEED_PRIOR_K * prior[k] + float(sums.get(k, 0.0))) / (SEED_PRIOR_K + n)
            for k in EMOTION_ORDER}
    return {
        "id": int(m["id"]), "subject_id": m["subject_id"], "scene": m["scene"],
        "expected": expected, "expected_level": level,
        "obs_n": n,
        "observed": ({k: float(sums.get(k, 0.0)) / n for k in EMOTION_ORDER} if n else None),
        "pred": pred,
        "created_ms": int(m["created_ms"]),
        "updated_ms": int(m["updated_ms"]) if m["updated_ms"] else None,
    }


def list_seeds(subject_id: str) -> dict:
    with _LOCK:
        p = _root() / _subject_dir_name(subject_id) / "seeds.csv"
        rows = _read(p)
    rows.sort(key=lambda m: int(m["id"]))
    return {"items": [_seed_dict(m) for m in rows], "prior_k": SEED_PRIOR_K}


def add_seed(subject_id: str, scene: str, expected: str | None, level) -> dict:
    scene = (scene or "").strip()
    if not scene:
        raise ValueError("场景不能为空")
    expected = (expected or "").strip().lower() or None
    if expected is not None and expected not in EMOTION_ORDER:
        raise ValueError(f"预期情绪须为 {'/'.join(EMOTION_ORDER)} 之一")
    now = int(time.time() * 1000)
    with _LOCK:
        # 种子 id 全局唯一（删除/上报端点只带 id，不带测试者）
        new_id = max((int(r["id"]) for d in _subject_dirs() for r in _read(d / "seeds.csv")),
                     default=0) + 1
        m = {
            "id": new_id, "subject_id": subject_id or "anon", "scene": scene,
            "expected": expected or "", "expected_level": min(max(int(level or 3), 1), 5),
            "obs_n": 0, "obs_sum": "{}", "created_ms": now, "updated_ms": now,
        }
        _append(_subject_path(subject_id) / "seeds.csv", _SEED_HEADER,
                [[m[h] for h in _SEED_HEADER]])
    return _seed_dict(m)


def _find_seed(seed_id: int):
    """→ (seeds.csv 路径, 全部行, 目标行)。"""
    for d in _subject_dirs():
        p = d / "seeds.csv"
        rows = _read(p)
        for r in rows:
            if int(r["id"]) == int(seed_id):
                return p, rows, r
    raise LookupError(f"种子 {seed_id} 不存在")


def delete_seed(seed_id: int) -> dict:
    with _LOCK:
        p, rows, target = _find_seed(seed_id)
        _rewrite(p, _SEED_HEADER, [r for r in rows if r is not target])
    return {"ok": True}


def observe_seed(seed_id: int, emotions) -> dict:
    """累加一次实测反应（测量窗内 7 类情绪概率的均值）。"""
    ctx = _context_from_emotions(emotions)
    if ctx is None:
        raise ValueError("emotions 须为 7 类情绪字典")
    with _LOCK:
        p, rows, m = _find_seed(seed_id)
        sums = json.loads(m["obs_sum"]) if m["obs_sum"] else {}
        for k, v in zip(EMOTION_ORDER, ctx):
            sums[k] = float(sums.get(k, 0.0)) + v
        m["obs_sum"] = json.dumps(sums)
        m["obs_n"] = int(m["obs_n"] or 0) + 1
        m["updated_ms"] = int(time.time() * 1000)
        _rewrite(p, _SEED_HEADER, rows)
    return _seed_dict(m)
