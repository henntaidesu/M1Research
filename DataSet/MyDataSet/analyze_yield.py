"""预实验有效率分析（刺激重建方案 B.4 第 9 步）：按刺激统计「这个刺激值不值得留」。

输入：<root>/<subject>/<session>/{meta.json, fea.csv, events.csv, stimulus.csv, selfreport.csv, posed.csv}
（use_stimlib/playback.py 落盘的 Capture 格式；没有 trial 列的旧会话跳过）。

每个试次算：
    rated        有没有同一 trial 的自评
    quality ok   fea_cov ≥ --min-cov（stimulus.csv 的 fea_cov；缺列时按帧数 / (时长 × --rate-hz) 重算）
    onset_src    headset / server / timeout
    rating_ms    自评用时
    V / A        自评效价 / 唤醒（1–9）
    hit          自评落在条件的目标区域：高 ≥ 6、低 ≤ 4（与 use_stimlib/conditions.hit 同阈值）；
                 NEU 要求效价 4–6 且唤醒 ≤ 4
    agree        自评 7 类 == 刺激的 target_emotion（从 --catalog 的 <id>.json 读；旧会话 target_emotion
                 列里若本来就是 7 类名也认）
    reactivity   面部反应度 = 刺激期每帧 Σ|FEA − 注视期均值|（70 维 L1）的均值。注视期取该试次最近一次
                 fixation 阶段开始到 onset；找不到时用 onset 前 --fix-s 秒
事件锁定（events.csv 里头显打点的 evt:*，complete / video_stall 除外）：
    evt_peak     事件后 0–3 s 内 L1(FEA − 事件前 1 s 均值) 的峰值（5 帧滑动平均后）减去事件前 1 s 内同一量的均值，
                 即扣掉噪声底；evt_latency_s 是峰值时刻
表演段（posed.csv）：每段 Σ|FEA − 静息期均值| 的均值，按提示类别汇总（表演能动到多大，作为自发反应的参照尺度）。

汇总：按刺激、按 kind / trial_class、按条件、按受试者。另给每种 kind 反应最大的 5 个 blendshape。
淘汰建议（n ≥ --min-n 的刺激）：hit 率 < --drop-hit，或反应度在同 trial_class 的下四分位
（同类刺激不足 4 个时与全部刺激比）。不同 trial_class 的刺激期长短和诱发方式不同，反应度不跨类比较。

用法：
    python DataSet/MyDataSet/analyze_yield.py --root DataSet/MyDataSet/Capture
    python DataSet/MyDataSet/analyze_yield.py --root DataSet/MyDataSet/Capture --subjects p01 p02 --out yield.json
    python DataSet/MyDataSet/analyze_yield.py --out yield.csv      # 每刺激一行；另写 yield_trials.csv（每试次一行）
"""
import argparse
import csv
import glob
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

CLASS_NAMES = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
CONDITION_KEYS = ["HVHA", "HVLA", "LVHA", "LVLA", "NEU"]
FEA_DIM = 70
NON_STIM_EVENTS = {"complete", "video_stall"}
# 与 prepare_timeline_dataset.py 的 OVR_FACE_EXPRESSIONS 同序（OVRPlugin.FaceExpression2）
OVR_FACE_EXPRESSIONS = [
    "Brow_Lowerer_L", "Brow_Lowerer_R", "Cheek_Puff_L", "Cheek_Puff_R", "Cheek_Raiser_L",
    "Cheek_Raiser_R", "Cheek_Suck_L", "Cheek_Suck_R", "Chin_Raiser_B", "Chin_Raiser_T", "Dimpler_L",
    "Dimpler_R", "Eyes_Closed_L", "Eyes_Closed_R", "Eyes_Look_Down_L", "Eyes_Look_Down_R",
    "Eyes_Look_Left_L", "Eyes_Look_Left_R", "Eyes_Look_Right_L", "Eyes_Look_Right_R",
    "Eyes_Look_Up_L", "Eyes_Look_Up_R", "Inner_Brow_Raiser_L", "Inner_Brow_Raiser_R", "Jaw_Drop",
    "Jaw_Sideways_Left", "Jaw_Sideways_Right", "Jaw_Thrust", "Lid_Tightener_L", "Lid_Tightener_R",
    "Lip_Corner_Depressor_L", "Lip_Corner_Depressor_R", "Lip_Corner_Puller_L",
    "Lip_Corner_Puller_R", "Lip_Funneler_LB", "Lip_Funneler_LT", "Lip_Funneler_RB",
    "Lip_Funneler_RT", "Lip_Pressor_L", "Lip_Pressor_R", "Lip_Pucker_L", "Lip_Pucker_R",
    "Lip_Stretcher_L", "Lip_Stretcher_R", "Lip_Suck_LB", "Lip_Suck_LT", "Lip_Suck_RB",
    "Lip_Suck_RT", "Lip_Tightener_L", "Lip_Tightener_R", "Lips_Toward", "Lower_Lip_Depressor_L",
    "Lower_Lip_Depressor_R", "Mouth_Left", "Mouth_Right", "Nose_Wrinkler_L", "Nose_Wrinkler_R",
    "Outer_Brow_Raiser_L", "Outer_Brow_Raiser_R", "Upper_Lid_Raiser_L", "Upper_Lid_Raiser_R",
    "Upper_Lip_Raiser_L", "Upper_Lip_Raiser_R", "Tongue_Tip_Interdental", "Tongue_Tip_Alveolar",
    "Tongue_Front_Dorsal_Palate", "Tongue_Mid_Dorsal_Palate", "Tongue_Back_Dorsal_Velar",
    "Tongue_Out", "Tongue_Retreat",
]
assert len(OVR_FACE_EXPRESSIONS) == FEA_DIM
MIN_FRAMES = 5


# ── 读取 ────────────────────────────────────────────────────────────
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_fea(path):
    """fea.csv → (ts int64, feats (N,70) f32)，按时间排序；63 维旧数据补零。"""
    ts, feats = [], []
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 64:
                continue
            try:
                bs = [float(x) for x in row[1:1 + FEA_DIM]]
                t = int(float(row[0]))
            except ValueError:
                continue
            ts.append(t)
            feats.append(bs + [0.0] * (FEA_DIM - len(bs)))
    if not ts:
        return None, None
    ts, feats = np.array(ts, dtype="int64"), np.array(feats, dtype="float32")
    o = np.argsort(ts, kind="stable")
    return ts[o], feats[o]


def window(ts, feats, a, b):
    if ts is None:
        return None, None
    lo, hi = np.searchsorted(ts, a), np.searchsorted(ts, b)
    return ts[lo:hi], feats[lo:hi]


def load_catalog(cat_dir):
    """catalog/<id>.json → {id: {target_emotion, kind, trial_class, condition}}（只读）。"""
    out = {}
    for p in glob.glob(os.path.join(cat_dir or "", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        out[os.path.splitext(os.path.basename(p))[0]] = d
    return out


def _int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _float(v, default=None):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except (TypeError, ValueError):
        return default


# ── 判定 ────────────────────────────────────────────────────────────
def hit(cond, v, a):
    """自评是否落在条件目标区域。高 ≥ 6、低 ≤ 4；NEU：效价 4–6 且唤醒 ≤ 4（与 conditions.hit 一致）。条件未知返回 None。"""
    if cond not in CONDITION_KEYS or v is None or a is None:
        return None
    if cond == "NEU":
        return 4 <= v <= 6 and a <= 4
    v_ok = v >= 6 if cond[0] == "H" else v <= 4
    a_ok = a >= 6 if cond[2] == "H" else a <= 4
    return v_ok and a_ok


def fixation_start(phase_rows, onset):
    """onset 之前最近一次 fixation 阶段的开始时刻（30 s 以内），没有返回 None。"""
    best = None
    for t, ph in phase_rows:
        if t > onset:
            break
        if ph == "fixation" and onset - t <= 30000:
            best = t
    return best


def smooth(x, k=5):
    if len(x) < k:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


# ── 每个会话 ────────────────────────────────────────────────────────
def analyze_session(sdir, subject, cat, args):
    session = os.path.basename(sdir)
    stim = read_csv(os.path.join(sdir, "stimulus.csv"))
    if not stim or "trial" not in stim[0]:
        return None
    ts, feats = load_fea(os.path.join(sdir, "fea.csv"))
    events = read_csv(os.path.join(sdir, "events.csv"))
    reports = {}
    for r in read_csv(os.path.join(sdir, "selfreport.csv")):
        reports[r.get("trial")] = r                    # 同一 trial 多次提交时取最后一次
    phase_rows = sorted((_int(e["ts_ms"], 0), e.get("phase") or "") for e in events if e.get("event") == "phase")
    phase_ev = [(t, ph) for t, ph in phase_rows]

    trials = []
    for r in stim:
        sid = r.get("image_id") or ""
        on, off = _int(r.get("onset_ms")), _int(r.get("offset_ms"))
        if on is None or off is None:
            continue
        raw_cond = r.get("target_emotion") or ""           # 历史原因：这一列存的是条件
        c = cat.get(sid) or {}
        cond = raw_cond if raw_cond in CONDITION_KEYS else (c.get("condition") or "")
        target = c.get("target_emotion") or (raw_cond if raw_cond in CLASS_NAMES else "")
        kind = r.get("kind") or c.get("kind") or ("skybox_image" if ":image" in sid else
                                                  "skybox_video" if ":video" in sid else "")
        tclass = r.get("trial_class") or c.get("trial_class") or "immersive"
        wt, wf = window(ts, feats, on, off)
        n_frames = 0 if wt is None else len(wt)
        cov = _float(r.get("fea_cov"))
        if cov is None:
            cov = n_frames / max(1e-6, (off - on) / 1000 * args.rate_hz)
        fs = fixation_start(phase_ev, on)
        if fs is None or on - fs < 200:
            fs = on - int(args.fix_s * 1000)
        _, bf = window(ts, feats, fs, on)
        react, react_dims = None, None
        if wf is not None and len(wf) >= MIN_FRAMES and bf is not None and len(bf) >= MIN_FRAMES:
            dev = np.abs(wf - bf.mean(axis=0))
            react = float(dev.sum(axis=1).mean())
            react_dims = dev.mean(axis=0)
        rep = reports.get(r.get("trial"))
        v = a = None
        l7 = ""
        rms = None
        mode = ""
        if rep:
            v, a = _int(rep.get("valence")), _int(rep.get("arousal"))
            l7 = rep.get("label7") or ""
            rms = _float(rep.get("rating_ms"))
            mode = rep.get("rating_mode") or ""
        trials.append({
            "subject": subject, "session": session, "trial": _int(r.get("trial"), 0),
            "attempt": _int(r.get("attempt"), 1), "stimulus_id": sid, "kind": kind, "trial_class": tclass,
            "condition": cond, "target_emotion": target, "onset_src": r.get("onset_src") or "?",
            "onset_ms": on, "dur_s": round((off - on) / 1000, 3), "fea_cov": round(float(cov), 3),
            "quality_ok": bool(cov >= args.min_cov), "rated": rep is not None, "valence": v, "arousal": a,
            "label7": l7, "rating_ms": rms, "rating_mode": mode,
            "hit": hit(cond, v, a) if rep else None,
            "agree": (l7 == target) if (rep and target and l7 in CLASS_NAMES) else None,
            "reactivity": None if react is None else round(react, 5),
            "_dims": react_dims, "evt": [],
        })

    # 事件锁定
    by_trial = {(t["trial"]): t for t in trials}
    evts = []
    for e in events:
        ev = e.get("event") or ""
        if not ev.startswith("evt:") or ev[4:] in NON_STIM_EVENTS or (e.get("src") or "headset") != "headset":
            continue
        t0 = _int(e.get("ts_ms"))
        if t0 is None:
            continue
        _, pf = window(ts, feats, t0 - int(args.evt_pre_s * 1000), t0)
        qt, qf = window(ts, feats, t0, t0 + int(args.evt_post_s * 1000))
        rec = {"subject": subject, "session": session, "trial": _int(e.get("trial"), 0),
               "stimulus_id": e.get("stimulus_id") or "", "key": ev[4:], "peak": None, "latency_s": None}
        if pf is not None and len(pf) >= MIN_FRAMES and qf is not None and len(qf) >= MIN_FRAMES:
            base = pf.mean(axis=0)
            floor = float(np.abs(pf - base).sum(axis=1).mean())
            post = smooth(np.abs(qf - base).sum(axis=1))
            k = int(np.argmax(post))
            rec["peak"] = round(float(post[k]) - floor, 5)
            rec["latency_s"] = round((int(qt[k]) - t0) / 1000, 3)
        evts.append(rec)
        tr = by_trial.get(rec["trial"])
        if tr is not None and tr["stimulus_id"] == rec["stimulus_id"]:
            tr["evt"].append(rec)

    # 表演段：相对静息期均值
    rest = [(t, nxt) for (t, ph), (nxt, _) in zip(phase_rows, phase_rows[1:] + [(None, None)]) if ph == "rest"]
    rest_mean = None
    for a0, b0 in rest:
        _, rf = window(ts, feats, a0, b0 if b0 is not None else a0 + 60000)
        if rf is not None and len(rf) >= MIN_FRAMES:
            rest_mean = rf.mean(axis=0)
            break
    if rest_mean is None and feats is not None:
        rest_mean = np.median(feats, axis=0)
    posed = []
    for p in read_csv(os.path.join(sdir, "posed.csv")):
        on, off = _int(p.get("onset_ms")), _int(p.get("offset_ms"))
        if on is None or off is None:
            continue
        _, pf = window(ts, feats, on, off)
        val = None
        if pf is not None and len(pf) >= MIN_FRAMES and rest_mean is not None:
            val = round(float(np.abs(pf - rest_mean).sum(axis=1).mean()), 5)
        posed.append({"subject": subject, "session": session, "emotion": p.get("emotion") or "",
                      "rep": _int(p.get("rep"), 0), "fea_cov": _float(p.get("fea_cov")), "reactivity": val})
    return trials, evts, posed


# ── 汇总 ────────────────────────────────────────────────────────────
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def _sd(xs):
    xs = [x for x in xs if x is not None]
    return float(np.std(xs, ddof=1)) if len(xs) > 1 else None


def _rate(xs):
    xs = [x for x in xs if x is not None]
    return (sum(1 for x in xs if x) / len(xs)) if xs else None


def summarize(ts):
    rated = [t for t in ts if t["rated"]]
    ok = [t for t in ts if t["quality_ok"]]
    evp = [e["peak"] for t in ts for e in t["evt"]]
    return {
        "n": len(ts), "n_subjects": len({t["subject"] for t in ts}),
        "rated_pct": len(rated) / len(ts) if ts else None,
        "ok_pct": len(ok) / len(ts) if ts else None,
        "onset_src": dict(Counter(t["onset_src"] for t in ts)),
        "rating_s": None if _mean([t["rating_ms"] for t in rated]) is None
        else _mean([t["rating_ms"] for t in rated]) / 1000,
        "v_mean": _mean([t["valence"] for t in rated]), "v_sd": _sd([t["valence"] for t in rated]),
        "a_mean": _mean([t["arousal"] for t in rated]), "a_sd": _sd([t["arousal"] for t in rated]),
        "hit": _rate([t["hit"] for t in rated]), "n_hit_eval": sum(1 for t in rated if t["hit"] is not None),
        "agree": _rate([t["agree"] for t in rated]), "n_agree_eval": sum(1 for t in rated if t["agree"] is not None),
        "reactivity": _mean([t["reactivity"] for t in ok]),
        "reactivity_sd": _sd([t["reactivity"] for t in ok]),
        "evt_peak": _mean(evp), "n_evt": sum(1 for x in evp if x is not None),
    }


def group(trials, key):
    g = defaultdict(list)
    for t in trials:
        g[key(t)].append(t)
    return {k: summarize(v) for k, v in sorted(g.items())}


def top_dims(trials, k=5):
    out = {}
    g = defaultdict(list)
    for t in trials:
        if t["quality_ok"] and t["_dims"] is not None:
            g[t["kind"] or "?"].append(t["_dims"])
    for kind, ds in sorted(g.items()):
        m = np.mean(ds, axis=0)
        idx = np.argsort(m)[::-1][:k]
        out[kind] = [(OVR_FACE_EXPRESSIONS[i], round(float(m[i]), 4)) for i in idx]
    return out


def flag_drops(per_stim, stim_class, min_n, drop_hit):
    """n ≥ min_n 的刺激：hit 率 < drop_hit，或反应度在同 trial_class 下四分位（同类 < 4 个时与全部比）。"""
    elig = {s: r for s, r in per_stim.items() if r["n"] >= min_n}
    reac_all = [r["reactivity"] for r in elig.values() if r["reactivity"] is not None]
    by_cls = defaultdict(list)
    for s, r in elig.items():
        if r["reactivity"] is not None:
            by_cls[stim_class[s]].append(r["reactivity"])
    for s, r in per_stim.items():
        r["drop"], r["drop_reason"] = False, ""
        if s not in elig:
            r["drop_reason"] = f"n<{min_n}"
            continue
        why = []
        if r["hit"] is not None and r["hit"] < drop_hit:
            why.append(f"hit {r['hit']:.2f}<{drop_hit}")
        ref = by_cls[stim_class[s]] if len(by_cls[stim_class[s]]) >= 4 else reac_all
        if r["reactivity"] is not None and len(ref) >= 2:
            q1 = float(np.percentile(ref, 25))
            if r["reactivity"] <= q1:
                why.append(f"reactivity≤Q1 {q1:.3f}" + ("" if ref is not reac_all else " (all)"))
        r["drop"] = bool(why)
        r["drop_reason"] = "; ".join(why)
    return per_stim


# ── 输出 ────────────────────────────────────────────────────────────
def _f(v, d=2, pct=False):
    if v is None:
        return "–"
    return f"{v * 100:.0f}%" if pct else f"{v:.{d}f}"


def _pm(m, s):
    return "–" if m is None else f"{m:.1f}±{_f(s, 1)}"


def _src(d):
    return " ".join(f"{k}:{v}" for k, v in sorted(d.items()))


SUM_COLS = [("n", lambda r: str(r["n"])), ("subj", lambda r: str(r["n_subjects"])),
            ("rated", lambda r: _f(r["rated_pct"], pct=True)), ("ok", lambda r: _f(r["ok_pct"], pct=True)),
            ("onset_src", lambda r: _src(r["onset_src"])), ("rate_s", lambda r: _f(r["rating_s"], 1)),
            ("V", lambda r: _pm(r["v_mean"], r["v_sd"])), ("A", lambda r: _pm(r["a_mean"], r["a_sd"])),
            ("hit", lambda r: _f(r["hit"], pct=True)), ("agree7", lambda r: _f(r["agree"], pct=True)),
            ("react", lambda r: _f(r["reactivity"], 3)), ("evt_peak", lambda r: _f(r["evt_peak"], 3))]


def md_table(title, rows, first, extra=()):
    cols = [first] + [c for c, _ in SUM_COLS] + [c for c, _ in extra]
    lines = [f"\n### {title}\n", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for k, r in rows.items():
        cells = [str(k) or "(空)"] + [fn(r) for _, fn in SUM_COLS] + [fn(r) for _, fn in extra]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="预实验有效率：按刺激 / 类型 / 条件 / 受试者统计诱发效果与数据质量")
    ap.add_argument("--root", default="DataSet/MyDataSet/Capture")
    ap.add_argument("--catalog", default="DataSet/MyDataSet/Stimulus/catalog",
                    help="刺激清单目录（读 target_emotion；只读）")
    ap.add_argument("--subjects", nargs="*", help="只分析这些受试者")
    ap.add_argument("--min-cov", type=float, default=0.8, help="quality ok 的 fea_cov 下限")
    ap.add_argument("--rate-hz", type=float, default=60.0, help="缺 fea_cov 列时重算覆盖率用的名义帧率")
    ap.add_argument("--fix-s", type=float, default=3.0, help="找不到注视阶段时，用 onset 前多少秒作基线")
    ap.add_argument("--evt-pre-s", type=float, default=1.0)
    ap.add_argument("--evt-post-s", type=float, default=3.0)
    ap.add_argument("--first-attempt-only", action="store_true", help="只统计 attempt=1（默认重看试次也算一次呈现）")
    ap.add_argument("--min-n", type=int, default=3, help="刺激至少呈现几次才给淘汰建议")
    ap.add_argument("--drop-hit", type=float, default=0.5, help="hit 率低于此值建议淘汰")
    ap.add_argument("--out", help="另存结果：.json = 全部汇总 + 每试次；.csv = 每刺激一行（另写 <stem>_trials.csv）")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    cat = load_catalog(args.catalog)
    trials, evts, posed, sessions = [], [], [], 0
    for meta_path in sorted(glob.glob(os.path.join(args.root, "*", "*", "meta.json"))):
        sdir = os.path.dirname(meta_path)
        subject = os.path.basename(os.path.dirname(sdir))
        if args.subjects and subject not in args.subjects:
            continue
        res = analyze_session(sdir, subject, cat, args)
        if res is None:
            continue
        sessions += 1
        trials += res[0]
        evts += res[1]
        posed += res[2]
    if args.first_attempt_only:
        trials = [t for t in trials if t["attempt"] == 1]
    if not trials:
        print(f"[无数据] {args.root} 下没有带 trial 列的会话", file=sys.stderr)
        raise SystemExit(1)

    per_stim = group(trials, lambda t: t["stimulus_id"])
    stim_class = {t["stimulus_id"]: t["trial_class"] for t in trials}
    stim_kind = {t["stimulus_id"]: t["kind"] for t in trials}
    stim_cond = {t["stimulus_id"]: t["condition"] for t in trials}
    stim_target = {t["stimulus_id"]: t["target_emotion"] for t in trials}
    flag_drops(per_stim, stim_class, args.min_n, args.drop_hit)
    for s, r in per_stim.items():
        r.update(kind=stim_kind[s], trial_class=stim_class[s], condition=stim_cond[s], target_emotion=stim_target[s])
    per_kind = group(trials, lambda t: f"{t['kind']} / {t['trial_class']}")
    per_cond = group(trials, lambda t: t["condition"])
    per_subj = group(trials, lambda t: t["subject"])
    tops = top_dims(trials)
    evt_g = defaultdict(list)
    for e in evts:
        evt_g[(e["stimulus_id"], e["key"])].append(e)
    per_evt = {f"{s} / {k}": {"n": len(v), "peak": _mean([e["peak"] for e in v]),
                              "peak_sd": _sd([e["peak"] for e in v]),
                              "latency_s": _mean([e["latency_s"] for e in v])} for (s, k), v in sorted(evt_g.items())}
    pos_g = defaultdict(list)
    for p in posed:
        pos_g[p["emotion"]].append(p["reactivity"])
    per_posed = {e: {"n": len(v), "reactivity": _mean(v), "sd": _sd(v)}
                 for e, v in sorted(pos_g.items(), key=lambda kv: CLASS_NAMES.index(kv[0])
                                    if kv[0] in CLASS_NAMES else 99)}

    # ── markdown ──
    subs = sorted({t["subject"] for t in trials})
    out = [f"# 预实验有效率报告\n",
           f"- 数据：`{args.root}`，{len(subs)} 名受试者 / {sessions} 场 / {len(trials)} 次呈现"
           f"（{'仅 attempt=1' if args.first_attempt_only else '含重看'}）",
           f"- 质量阈值 fea_cov ≥ {args.min_cov}；hit：高 ≥ 6、低 ≤ 4，NEU 为 V 4–6 且 A ≤ 4；"
           f"反应度 = 刺激期 Σ|FEA − 注视基线|（70 维 L1）均值，只算 quality ok 的试次",
           f"- 淘汰建议：n ≥ {args.min_n} 且（hit < {args.drop_hit:.0%} 或反应度 ≤ 同 trial_class 下四分位）"]
    drops = [s for s, r in per_stim.items() if r["drop"]]
    out.append(f"- **建议淘汰 {len(drops)} 个**：" + (", ".join(f"`{s}`（{per_stim[s]['drop_reason']}）"
                                                       for s in drops) if drops else "无"))
    out.append(md_table("按刺激", per_stim, "stimulus",
                        extra=[("kind", lambda r: r["kind"]), ("cond", lambda r: r["condition"] or "–"),
                               ("target", lambda r: r["target_emotion"] or "–"),
                               ("drop", lambda r: ("**DROP** " + r["drop_reason"]) if r["drop"]
                                else r["drop_reason"])]))
    out.append(md_table("按 kind / trial_class", per_kind, "kind / class"))
    out.append(md_table("按条件", per_cond, "condition"))
    out.append(md_table("按受试者", per_subj, "subject"))
    out.append("\n### 各 kind 反应最大的 5 个 blendshape（刺激期 |FEA − 注视基线| 均值）\n")
    for kind, lst in tops.items():
        out.append(f"- {kind}: " + ", ".join(f"{n} {v:.3f}" for n, v in lst))
    out.append("\n### 事件锁定反应（事件后 0–3 s 峰值 − 事件前 1 s 噪声底）\n")
    if per_evt:
        out += ["| stimulus / key | n | peak | latency_s |", "|---|---|---|---|"]
        out += [f"| {k} | {r['n']} | {_pm(r['peak'], r['peak_sd']) if r['peak'] is not None else '–'} "
                f"| {_f(r['latency_s'])} |" for k, r in per_evt.items()]
    else:
        out.append("（没有头显打点的 evt:* 事件）")
    out.append("\n### 表演校准（posed.csv，相对静息期均值）\n")
    if per_posed:
        out += ["| emotion | n | reactivity |", "|---|---|---|"]
        out += [f"| {e} | {r['n']} | {_pm(r['reactivity'], r['sd']) if r['reactivity'] is not None else '–'} |"
                for e, r in per_posed.items()]
        sp = _mean([t["reactivity"] for t in trials if t["quality_ok"]])
        pr = _mean([p["reactivity"] for p in posed if p["emotion"] != "neutral"])
        if sp is not None and pr:
            out.append(f"\n自发 / 表演（非 neutral）反应度之比：{sp / pr:.2f}（注意两者基线不同：注视期 vs 静息期）")
    else:
        out.append("（没有 posed.csv）")
    print("\n".join(out))

    if args.out:
        rows = [{k: v for k, v in t.items() if k not in ("_dims", "evt")} | {
            "evt_peak": _mean([e["peak"] for e in t["evt"]])} for t in trials]
        if args.out.lower().endswith(".json"):
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({"params": vars(args), "per_stimulus": per_stim, "per_kind": per_kind,
                           "per_condition": per_cond, "per_subject": per_subj, "top_blendshapes": tops,
                           "events": per_evt, "posed": per_posed, "trials": rows, "event_trials": evts},
                          f, ensure_ascii=False, indent=1)
        else:
            cols = ["stimulus_id", "kind", "trial_class", "condition", "target_emotion", "n", "n_subjects",
                    "rated_pct", "ok_pct", "onset_src", "rating_s", "v_mean", "v_sd", "a_mean", "a_sd", "hit",
                    "n_hit_eval", "agree", "n_agree_eval", "reactivity", "reactivity_sd", "evt_peak", "n_evt",
                    "drop", "drop_reason"]
            with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for s, r in per_stim.items():
                    r = dict(r, stimulus_id=s, onset_src=_src(r["onset_src"]))
                    w.writerow(["" if r.get(c) is None else (round(r[c], 4) if isinstance(r[c], float) else r[c])
                                for c in cols])
            tpath = os.path.splitext(args.out)[0] + "_trials.csv"
            with open(tpath, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        print(f"\n[已保存] {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
