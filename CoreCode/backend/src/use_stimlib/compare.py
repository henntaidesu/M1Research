"""图片线 vs 视频线：哪条线诱发效果更好。

比较单元是**刺激对**（同一全景的图片版与视频版）：每对先在各条线内对所有呈现取均值，
再看「视频 − 图片」的差，用符号翻转置换检验（配对、无分布假设、样本小也成立）。
平衡分组下同一受试者只看每对的一种版本，所以配对是跨受试者的，单位必须是刺激对而不是呈现。

指标：
  hit         自评落在该条件目标区域的比例（操纵检验，主指标）
  arousal     自评唤醒度（诱发强度）
  extremity   |效价 − 5|（效价偏离中性的程度）
  fea_expr    刺激期相对本试次注视基线的面部表情变化量（70 维去掉眼球朝向与舌头后的平均绝对偏移）
  sd_v / sd_a 同一刺激跨受试者的自评标准差（越小越一致）
低层特征（亮度、对比度、色彩度、运动）一并列出：两条线应只在运动上不同，否则比较有混淆。
"""
import csv
import itertools
from collections import defaultdict

import numpy as np

from .. import config
from ..use_model.fea_emotion import OVR_FACE_EXPRESSIONS
from . import conditions, library

# 表情通道：去掉眼球朝向（看哪里不是表情，视频会引起更多眼动）和舌头（Quest Pro 不稳定）
EXPR_IDX = [i for i, n in enumerate(OVR_FACE_EXPRESSIONS)
            if not n.startswith("Eyes_Look_") and not n.startswith("Tongue_")]
METRICS = ["hit", "arousal", "extremity", "fea_expr"]


def _perm_test(diffs: list, n_mc: int = 20000, seed: int = 0) -> float | None:
    """配对差的符号翻转置换检验，双侧 p。n ≤ 16 精确枚举。"""
    d = np.asarray([x for x in diffs if x is not None], dtype=float)
    n = len(d)
    if n < 2:
        return None
    obs = abs(d.mean())
    if n <= 16:
        signs = np.array(list(itertools.product((1, -1), repeat=n)), dtype=float)
    else:
        rng = np.random.default_rng(seed)
        signs = rng.choice((1.0, -1.0), size=(n_mc, n))
    null = np.abs((signs * d).mean(axis=1))
    return float((null >= obs - 1e-12).mean())


def _segments(events_path) -> list:
    """events.csv → [(trial, phase, start_ms, end_ms)]。刺激段从头显确认（ack）算起。"""
    rows = []
    with open(events_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    marks = [(int(r["ts_ms"]), str(r["trial"]), r["phase"]) for r in rows if r["event"] == "phase"]
    acks = {str(r["trial"]): int(r["ts_ms"]) for r in rows if r["event"] in ("ack", "ack_timeout")}
    ends = [int(r["ts_ms"]) for r in rows if r["event"] in ("done", "stopped")]
    segs = []
    for i, (ts, trial, phase) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else (ends[0] if ends else ts)
        if phase == "stimulus" and trial in acks:
            ts = acks[trial]
        segs.append((trial, phase, ts, end))
    return segs


def _fea_expr_by_trial() -> dict:
    """(session_id, trial) → 刺激期表情变化量。基线 = 同一试次注视阶段均值。"""
    out = {}
    if not config.CAPTURE_DIR.exists():
        return out
    for meta_path in config.CAPTURE_DIR.glob("*/*/meta.json"):
        d = meta_path.parent
        if not (d / "events.csv").exists() or not (d / "fea.csv").exists():
            continue
        segs = [s for s in _segments(d / "events.csv") if s[1] in ("fixation", "stimulus")]
        if not segs:
            continue
        starts = np.array([s[2] for s in segs])
        frames = defaultdict(list)
        with open(d / "fea.csv", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) < 71:
                    continue
                ts = int(float(row[0]))
                k = int(np.searchsorted(starts, ts, side="right")) - 1
                if k < 0 or ts >= segs[k][3]:
                    continue
                frames[(segs[k][0], segs[k][1])].append(np.asarray(row[1:71], dtype=float)[EXPR_IDX])
        for trial in {s[0] for s in segs}:
            base, stim = frames.get((trial, "fixation"), []), frames.get((trial, "stimulus"), [])
            if len(stim) < 10 or len(base) < 5:
                continue
            out[(d.name, trial)] = float(np.mean(np.abs(np.asarray(stim) - np.mean(base, axis=0))))
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def compare(include_manual: bool = False) -> dict:
    pairs = {m["pair_id"]: m for m in library.list_pairs()}
    fea = _fea_expr_by_trial()

    pres = []        # 每次呈现一行
    for r in library.read_ratings():
        if r.get("run_id") == "manual" and not include_manual:
            continue
        cond = r.get("condition")
        if cond not in conditions.CONDITION_KEYS or r.get("line") not in library.LINES:
            continue
        v, a = int(r["valence"]), int(r["arousal"])
        pres.append({
            "pair_id": r["pair_id"], "line": r["line"], "condition": cond, "subject": r["subject_id"],
            "stimulus_id": r["stimulus_id"], "valence": v, "arousal": a,
            "hit": 1.0 if conditions.hit(cond, v, a) else 0.0, "extremity": abs(v - 5),
            "fea_expr": fea.get((r["run_id"], str(r.get("trial") or ""))),
            "scene_id": r.get("scene_id") or "",
        })

    # ── 各条线汇总 ──
    lines = {}
    for line in library.LINES:
        ps = [p for p in pres if p["line"] == line]
        by_stim = defaultdict(list)
        for p in ps:
            by_stim[p["stimulus_id"]].append(p)
        multi = [v for v in by_stim.values() if len(v) >= 2]
        feats = [pairs[pid]["features"].get(line) for pid in pairs
                 if pairs[pid].get("features", {}).get(line)]
        lines[line] = {
            "n_presentations": len(ps),
            "n_stimuli": len(by_stim),
            "n_subjects": len({p["subject"] for p in ps}),
            "hit": _mean([p["hit"] for p in ps]),
            "arousal": _mean([p["arousal"] for p in ps]),
            "extremity": _mean([p["extremity"] for p in ps]),
            "fea_expr": _mean([p["fea_expr"] for p in ps]),
            "n_fea": sum(1 for p in ps if p["fea_expr"] is not None),
            "sd_v": _mean([float(np.std([x["valence"] for x in v], ddof=1)) for v in multi]),
            "sd_a": _mean([float(np.std([x["arousal"] for x in v], ddof=1)) for v in multi]),
            "lowlevel": {k: _mean([f[k] for f in feats]) for k in
                         ("luminance", "contrast", "colorfulness", "motion")} if feats else None,
            "by_condition": {
                c: {"n": len(cp), "hit": _mean([p["hit"] for p in cp]),
                    "valence": _mean([p["valence"] for p in cp]), "arousal": _mean([p["arousal"] for p in cp])}
                for c in conditions.CONDITION_KEYS
                for cp in [[p for p in ps if p["condition"] == c]]
            },
        }

    # ── 配对比较（单位 = 刺激对）──
    per_pair = defaultdict(lambda: {"image": [], "video": []})
    for p in pres:
        per_pair[p["pair_id"]][p["line"]].append(p)
    paired = {}
    for m in METRICS:
        diffs = []
        for d in per_pair.values():
            iv = _mean([p[m] for p in d["image"]])
            vv = _mean([p[m] for p in d["video"]])
            if iv is not None and vv is not None:
                diffs.append(vv - iv)
        paired[m] = {"n_pairs": len(diffs), "mean_diff": _mean(diffs), "p": _perm_test(diffs)}

    # 低层特征的配对差：除运动外应当接近 0
    ll_diff = {}
    for k in ("luminance", "contrast", "colorfulness", "motion"):
        ds = [m["features"]["video"][k] - m["features"]["image"][k] for m in pairs.values()
              if m.get("features", {}).get("video") and m.get("features", {}).get("image")]
        ll_diff[k] = {"n_pairs": len(ds), "mean_diff": _mean(ds)}

    # 各场景的评分汇总（⑥ 数据页：哪些场景诱发得好、哪些该换）
    by_scene = {}
    for p in pres:
        sid = p.get("scene_id") or ""
        if sid:
            by_scene.setdefault(sid, []).append(p)
    scene_summary = {
        sid: {"n": len(ps), "valence": _mean([p["valence"] for p in ps]), "arousal": _mean([p["arousal"] for p in ps]),
              "hit": _mean([p["hit"] for p in ps]), "fea_expr": _mean([p["fea_expr"] for p in ps]),
              "sd_v": float(np.std([p["valence"] for p in ps], ddof=1)) if len(ps) > 1 else None,
              "sd_a": float(np.std([p["arousal"] for p in ps], ddof=1)) if len(ps) > 1 else None}
        for sid, ps in by_scene.items()
    }

    return {"lines": lines, "paired": paired, "lowlevel_diff": ll_diff, "by_scene": scene_summary,
            "n_ratings": len(pres), "verdict": _verdict(lines, paired)}


def _verdict(lines: dict, paired: dict) -> list:
    msgs = []
    n_pairs = paired["hit"]["n_pairs"]
    n_subj = max(lines["image"]["n_subjects"], lines["video"]["n_subjects"])
    if n_pairs < 8 or n_subj < 4:
        msgs.append(f"样本不足：两条线都被评过的刺激对 {n_pairs} 对、受试者 {n_subj} 人。"
                    "建议至少 10 对（每条件 2 对以上）、8 名受试者（A/B 组各 4 人）再下结论。")
    names = {"hit": "操纵命中率", "arousal": "唤醒度", "extremity": "效价极端度", "fea_expr": "面部表情反应"}
    for m in METRICS:
        r = paired[m]
        if r["mean_diff"] is None or r["p"] is None:
            continue
        better = "视频线" if r["mean_diff"] > 0 else "图片线"
        sig = "显著" if r["p"] < 0.05 else "不显著"
        msgs.append(f"{names[m]}：视频−图片 = {r['mean_diff']:+.3f}（{r['n_pairs']} 对，p = {r['p']:.3f}，{sig}）"
                    + (f" → {better}更好" if r["p"] < 0.05 else ""))
    h = paired["hit"]
    if h["p"] is not None and h["p"] < 0.05 and n_pairs >= 8:
        winner = "视频线" if h["mean_diff"] > 0 else "图片线"
        msgs.append(f"结论：以操纵命中率为主指标，{winner}的诱发效果显著更好。")
    elif h["p"] is not None and n_pairs >= 8:
        msgs.append("结论：两条线的操纵命中率没有显著差别；可按唤醒度、面部反应与制作成本取舍"
                    "（图片线生成快、无晕动风险）。")
    return msgs
