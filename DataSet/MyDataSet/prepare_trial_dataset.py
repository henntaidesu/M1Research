"""把实验会话（Capture 格式，use_stimlib/playback.py 落盘）切成**试次级**训练样本。

研究设计 §4/§5：样本单位 = 一个试次（看一个刺激的一次反应），标签按 trial 绑定，不按时间前向填充。

输入：<root>/<subject>/<session>/{meta.json, fea.csv, events.csv, stimulus.csv, selfreport.csv, posed.csv}
输出 <out>/：
  trials.npz   每行一个试次
      X (N, T, 70) f32  刺激呈现期 [onset, onset+T) 的 FEA，按 --rate-hz 重采样；超出试次长度或采集空洞处为 0
      mask (N, T) bool  该帧是否有真实观测（空洞 > --max-gap-s 不插值）
      fix_mean (N, 70)  该试次注视期（刺激前 --fix-s 秒）的均值，用于试次内基线校准
      valence, arousal (N,) i64（1–9）；label7 (N,) i64（-1 = 未选）；intensity (N,) i64（0 = 未问）
      subj, session, stimulus_id, condition (N,) str；trial, attempt (N,) i64；fea_cov (N,) f32
      kind, trial_class, rating_mode, onset_src (N,) str   刺激类型 / 试次模板 / 快评或完整 SAM / onset 来源
                        （旧会话没有这些列时为空串；按区块分层报告、rating_mode 作协变量用）
  posed.npz    每行一段表演校准：X, mask, emotion (i64), rep, subj
  events.npz   事件锁定窗口（头显打点的 evt:<key>，如巨响时刻；complete / video_stall 不算刺激事件）：
      X (M, E, 70) f32  [事件 − --evt-pre-s, 事件 + --evt-post-s) 的 FEA；mask (M, E) bool
      subj, session, stimulus_id, key (M,) str；trial (M,) i64
  subjects.json 每名受试者静息期的 70 维均值 / 标准差（个人 z 分数用）
  meta.json

质量过滤（预先定好，见研究设计 §3.7）：stimulus.csv 的 quality=bad、或重算的有效帧比例 < --min-valid 的试次丢弃；
有效试次 < --min-trials 的受试者整人丢弃。默认只保留 attempt=1（重看的试次有熟悉化），--keep-repeats 放行。

用法：
    python DataSet/MyDataSet/prepare_trial_dataset.py --root DataSet/MyDataSet/Capture --out DataSet/MyDataSet/trial_dataset
"""
import argparse
import csv
import glob
import json
import os

import numpy as np

CLASS_NAMES = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
FEA_DIM = 70
NON_STIM_EVENTS = {"complete", "video_stall"}     # 头显打点里不是刺激事件的（片段播完、视频卡顿）


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_fea(path):
    ts, feats = [], []
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 64:
                continue
            bs = [float(x) for x in row[1:1 + FEA_DIM]]
            ts.append(int(float(row[0])))
            feats.append(bs + [0.0] * (FEA_DIM - len(bs)))
    if not ts:
        return None, None
    ts, feats = np.array(ts, dtype="int64"), np.array(feats, dtype="float32")
    o = np.argsort(ts, kind="stable")
    return ts[o], feats[o]


def grid_window(ts, feats, start_ms, n, rate_hz, max_gap_ms):
    """[start, start + n/rate) 的等间隔网格；离最近真实帧超过 max_gap/2 的网格点视为空洞。"""
    grid = start_ms + np.arange(n) * (1000.0 / rate_hz)
    out = np.zeros((n, FEA_DIM), dtype="float32")
    mask = np.zeros(n, dtype=bool)
    if ts is None or len(ts) < 2:
        return out, mask
    lo, hi = np.searchsorted(ts, grid[0] - max_gap_ms), np.searchsorted(ts, grid[-1] + max_gap_ms)
    t, f = ts[lo:hi], feats[lo:hi]
    if len(t) < 2:
        return out, mask
    idx = np.clip(np.searchsorted(t, grid), 1, len(t) - 1)
    near = np.minimum(np.abs(t[idx] - grid), np.abs(t[idx - 1] - grid))
    mask = (near <= max_gap_ms / 2) & (grid >= t[0]) & (grid <= t[-1])
    for k in range(FEA_DIM):
        out[:, k] = np.interp(grid, t, f[:, k])
    out[~mask] = 0.0
    return out, mask


def mean_between(ts, feats, a, b):
    if ts is None:
        return None
    m = (ts >= a) & (ts < b)
    return feats[m].mean(axis=0) if m.sum() >= 5 else None


def phase_spans(events, phase):
    """events.csv 里某阶段每次开始的时刻 → [(start_ms, next_event_ms)]。"""
    rows = [e for e in events if e.get("event") == "phase"]
    spans = []
    for i, e in enumerate(rows):
        if e["phase"] == phase:
            end = int(rows[i + 1]["ts_ms"]) if i + 1 < len(rows) else int(e["ts_ms"])
            spans.append((int(e["ts_ms"]), end))
    return spans


def main():
    ap = argparse.ArgumentParser(description="实验会话 → 试次级训练样本（标签按 trial 绑定）")
    ap.add_argument("--root", default="DataSet/MyDataSet/Capture")
    ap.add_argument("--out", default="DataSet/MyDataSet/trial_dataset")
    ap.add_argument("--rate-hz", type=float, default=30.0)
    ap.add_argument("--max-s", type=float, default=45.0, help="每个试次最多取刺激开始后多少秒")
    ap.add_argument("--fix-s", type=float, default=3.0, help="试次基线：刺激前多少秒的注视期")
    ap.add_argument("--max-gap-s", type=float, default=0.5)
    ap.add_argument("--min-valid", type=float, default=0.8, help="刺激期有效帧比例下限")
    ap.add_argument("--min-trials", type=int, default=14, help="受试者有效试次下限（研究设计 §3.7）")
    ap.add_argument("--keep-repeats", action="store_true", help="保留 attempt>1 的重看试次")
    ap.add_argument("--evt-pre-s", type=float, default=1.0, help="事件锁定窗口：事件前多少秒")
    ap.add_argument("--evt-post-s", type=float, default=4.0, help="事件锁定窗口：事件后多少秒")
    args = ap.parse_args()

    T = int(round(args.max_s * args.rate_hz))
    gap_ms = args.max_gap_s * 1000
    trials = {k: [] for k in ("X", "mask", "fix_mean", "valence", "arousal", "label7", "intensity", "subj",
                              "session", "stimulus_id", "condition", "trial", "attempt", "fea_cov",
                              "kind", "trial_class", "rating_mode", "onset_src")}
    posed = {k: [] for k in ("X", "mask", "emotion", "rep", "subj")}
    evts = {k: [] for k in ("X", "mask", "subj", "session", "stimulus_id", "key", "trial")}
    E = int(round((args.evt_pre_s + args.evt_post_s) * args.rate_hz))
    subj_stats, dropped = {}, {"bad": 0, "invalid": 0, "repeat": 0, "no_rating": 0, "evt_invalid": 0}

    for meta_path in sorted(glob.glob(os.path.join(args.root, "*", "*", "meta.json"))):
        sdir = os.path.dirname(meta_path)
        subject = os.path.basename(os.path.dirname(sdir))
        session = os.path.basename(sdir)
        stim = read_csv(os.path.join(sdir, "stimulus.csv"))
        if not stim or "trial" not in stim[0] or not os.path.exists(os.path.join(sdir, "fea.csv")):
            continue                                   # 旧格式会话不进试次数据集
        ts, feats = load_fea(os.path.join(sdir, "fea.csv"))
        events = read_csv(os.path.join(sdir, "events.csv"))
        reports = {r["trial"]: r for r in read_csv(os.path.join(sdir, "selfreport.csv"))}

        for a, b in phase_spans(events, "rest"):
            m = (ts >= a) & (ts < b) if ts is not None else None
            if m is not None and m.sum() > 30:
                st = subj_stats.setdefault(subject, [])
                st.append(feats[m])

        for r in stim:
            if r.get("quality") == "bad":
                dropped["bad"] += 1
                continue
            attempt = int(r.get("attempt") or 1)
            if attempt > 1 and not args.keep_repeats:
                dropped["repeat"] += 1
                continue
            rep = reports.get(r["trial"])
            if not rep:
                dropped["no_rating"] += 1
                continue
            on, off = int(float(r["onset_ms"])), int(float(r["offset_ms"]))
            n = min(T, int((off - on) / 1000 * args.rate_hz))
            x, mk = grid_window(ts, feats, on, T, args.rate_hz, gap_ms)
            mk[n:] = False
            x[n:] = 0.0
            if n == 0 or mk[:n].mean() < args.min_valid:
                dropped["invalid"] += 1
                continue
            fm = mean_between(ts, feats, on - args.fix_s * 1000, on)
            trials["X"].append(x)
            trials["mask"].append(mk)
            trials["fix_mean"].append(fm if fm is not None else np.zeros(FEA_DIM, dtype="float32"))
            trials["valence"].append(int(rep["valence"]))
            trials["arousal"].append(int(rep["arousal"]))
            trials["label7"].append(CLASS_NAMES.index(rep["label7"]) if rep.get("label7") in CLASS_NAMES else -1)
            trials["intensity"].append(int(rep.get("intensity") or 0))
            trials["subj"].append(subject)
            trials["session"].append(session)
            trials["stimulus_id"].append(r["image_id"])
            trials["condition"].append(r.get("target_emotion", ""))
            trials["trial"].append(int(r["trial"]))
            trials["attempt"].append(attempt)
            trials["fea_cov"].append(float(r.get("fea_cov") or "nan"))
            trials["kind"].append(r.get("kind") or "")
            trials["trial_class"].append(r.get("trial_class") or "")
            trials["rating_mode"].append(rep.get("rating_mode") or "")
            trials["onset_src"].append(r.get("onset_src") or "")

        for e in events:
            ev = e.get("event") or ""
            if not ev.startswith("evt:") or e.get("src") != "headset" or ev[4:] in NON_STIM_EVENTS:
                continue
            t0 = int(float(e["ts_ms"])) - int(args.evt_pre_s * 1000)
            x, mk = grid_window(ts, feats, t0, E, args.rate_hz, gap_ms)
            if mk.mean() < args.min_valid:
                dropped["evt_invalid"] += 1
                continue
            evts["X"].append(x)
            evts["mask"].append(mk)
            evts["subj"].append(subject)
            evts["session"].append(session)
            evts["stimulus_id"].append(e.get("stimulus_id") or "")
            evts["key"].append(ev[4:])
            evts["trial"].append(int(e.get("trial") or 0))

        for p in read_csv(os.path.join(sdir, "posed.csv")):
            on, off = int(p["onset_ms"]), int(p["offset_ms"])
            n = max(1, int((off - on) / 1000 * args.rate_hz))
            x, mk = grid_window(ts, feats, on, n, args.rate_hz, gap_ms)
            if mk.mean() < args.min_valid or p["emotion"] not in CLASS_NAMES:
                continue
            posed["X"].append(x)
            posed["mask"].append(mk)
            posed["emotion"].append(CLASS_NAMES.index(p["emotion"]))
            posed["rep"].append(int(p["rep"]))
            posed["subj"].append(subject)

    # 有效试次太少的受试者整人剔除
    subs, counts = np.unique(np.asarray(trials["subj"]), return_counts=True)
    keep_subj = {s for s, c in zip(subs, counts) if c >= args.min_trials}
    excluded = sorted(set(subs) - keep_subj)
    sel = [i for i, s in enumerate(trials["subj"]) if s in keep_subj]

    os.makedirs(args.out, exist_ok=True)
    if sel:
        out = {}
        for k, v in trials.items():
            arr = np.asarray(v)
            out[k] = arr[sel]
        np.savez_compressed(os.path.join(args.out, "trials.npz"), **out)
    if posed["X"]:
        # 表演段长短不一 → 补零到最长，mask 标出有效帧
        L = max(len(x) for x in posed["X"])
        X = np.zeros((len(posed["X"]), L, FEA_DIM), dtype="float32")
        M = np.zeros((len(posed["X"]), L), dtype=bool)
        for i, (x, m) in enumerate(zip(posed["X"], posed["mask"])):
            X[i, :len(x)], M[i, :len(m)] = x, m
        np.savez_compressed(os.path.join(args.out, "posed.npz"), X=X, mask=M, emotion=np.asarray(posed["emotion"]),
                            rep=np.asarray(posed["rep"]), subj=np.asarray(posed["subj"]))
    if evts["X"]:
        np.savez_compressed(os.path.join(args.out, "events.npz"), X=np.stack(evts["X"]), mask=np.stack(evts["mask"]),
                            **{k: np.asarray(evts[k]) for k in ("subj", "session", "stimulus_id", "key", "trial")})
    stats = {s: {"mean": np.concatenate(v).mean(0).round(5).tolist(),
                 "std": np.concatenate(v).std(0).round(5).tolist()} for s, v in subj_stats.items()}
    with open(os.path.join(args.out, "subjects.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f)
    meta = {"classes": CLASS_NAMES, "rate_hz": args.rate_hz, "max_s": args.max_s, "seq_len": T,
            "fix_s": args.fix_s, "max_gap_s": args.max_gap_s, "min_valid": args.min_valid,
            "min_trials": args.min_trials, "keep_repeats": args.keep_repeats,
            "evt_pre_s": args.evt_pre_s, "evt_post_s": args.evt_post_s, "n_events": len(evts["X"]),
            "n_trials": len(sel), "n_posed": len(posed["X"]), "subjects": sorted(keep_subj),
            "excluded_subjects": excluded, "dropped": dropped}
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"[完成] {len(sel)} 个试次（{len(keep_subj)} 名受试者），{len(posed['X'])} 段表演，"
          f"{len(evts['X'])} 个事件窗口 → {args.out}")
    print(f"  丢弃：{dropped}")
    if excluded:
        print(f"  有效试次 < {args.min_trials} 被剔除的受试者：{excluded}")
    if sel:
        lab = np.asarray(trials["label7"])[sel]
        print("  7 类自评分布：" + ", ".join(f"{c}={int((lab == i).sum())}" for i, c in enumerate(CLASS_NAMES))
              + f", 未选={int((lab < 0).sum())}")
    raise SystemExit(0 if sel or posed["X"] or evts["X"] else 1)


if __name__ == "__main__":
    main()
