"""把 AffectData/ 里的 FEA 时间轴（CSV）导出成 Capture/ 目录结构，接回既有切窗流水线。

背景：项目里有两条采集链路，落盘格式不同——
  链路 A（刺激页 / 情感偏好页）：头显 POST /api/fea → AffectData/<测试者>/<session>_fea.csv
  链路 B（Unity SessionUploader）：POST /api/session/ingest → Capture/*/*/fea.csv
而 prepare_timeline_dataset.py 只读链路 B 的格式，于是链路 A 采到的数据无法直接进入训练。
本脚本把 A 导成 B 的格式，让两条链路汇合。（替代旧版 Postgres 导出脚本 export_pg_timeline.py。）

输入（use_store/csv_store.py 的落盘布局）：
    <root>/<测试者>/sessions.csv, <session_id>_fea.csv, <session_id>_selfreport.csv, images.csv

导出结构（与 use_capture/session_store.py 落盘格式一致）：
    <out>/<subject_id>/<session_id>/
        meta.json      会话元信息（标注 source=affect_csv）
        fea.csv        timestamp_ms, f0..f69      ← <session>_fea.csv（仅 FEA 非空的行）
        stimulus.csv   onset_ms, image_id, target_emotion, offset_ms, dwell_ms
                                                  ← images.csv（供 block-level 分组）
        selfreport.csv timestamp_ms, image_id, label7, valence, arousal
                                                  ← <session>_selfreport.csv（正式训练的真值）

用法（在仓库根目录运行）：
    python DataSet/MyDataSet/export_affect_timeline.py
    python DataSet/MyDataSet/export_affect_timeline.py --require-selfreport
    python DataSet/MyDataSet/export_affect_timeline.py --subject p01 --dry-run

注意：没有自评的会话导出后 selfreport.csv 只有表头，这类数据只能配合
--label-source fea（与 X 同源的循环标签）使用，而那已被切窗脚本默认拒绝。
脚本会逐会话标出是否有自评，并可用 --require-selfreport 只导可训练的。
让头显在每张刺激图后调 POST /api/affect/selfreport 即可补上。
"""
import argparse
import csv
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "CoreCode"))

from backend.src import config                                    # noqa: E402

FEA_DIM = config.FEA_DIM
_FEA_HEADER = ["timestamp_ms"] + [f"f{i}" for i in range(FEA_DIM)]
_STIMULUS_HEADER = ["onset_ms", "image_id", "target_emotion", "offset_ms", "dwell_ms"]
_SELFREPORT_HEADER = ["timestamp_ms", "image_id", "label7", "valence", "arousal"]


def _safe(s, fallback):
    s = re.sub(r"[^A-Za-z0-9_-]", "", str(s or ""))
    return s or fallback


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def fetch_sessions(root, subject=None):
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        for r in _read(os.path.join(d, "sessions.csv")):
            if subject and r.get("subject_id") != subject:
                continue
            out.append({"dir": d, "session_id": r["session_id"], "subject_id": r.get("subject_id"),
                        "started_ms": _int(r.get("started_ms")), "stopped_ms": _int(r.get("stopped_ms"))})
    out.sort(key=lambda s: s["started_ms"] or 0)
    return out


def fetch_fea(sess):
    """该会话的逐帧 FEA（跳过 f0..f69 为空的行——那些只有情绪没有原始信号）。"""
    out, bad = [], 0
    for r in _read(os.path.join(sess["dir"], f"{sess['session_id']}_fea.csv")):
        ts = _int(r.get("ts_ms"))
        raw = [r.get(f"f{i}", "") for i in range(FEA_DIM)]
        if ts is None or any(v in ("", None) for v in raw):
            bad += 1
            continue
        try:
            out.append((ts, [float(v) for v in raw]))
        except ValueError:
            bad += 1
    if bad:
        print(f"  [提示] {sess['session_id']}: 跳过 {bad} 行无 FEA / 无法解析的帧")
    out.sort(key=lambda x: x[0])
    return out


def fetch_images(sess):
    rows = [r for r in _read(os.path.join(sess["dir"], "images.csv"))
            if r.get("session_id") == sess["session_id"] and _int(r.get("ts_ms")) is not None]
    out = [{"id": r.get("id"), "ts_ms": _int(r["ts_ms"]), "dominant": r.get("dominant")} for r in rows]
    out.sort(key=lambda x: x["ts_ms"])
    return out


def fetch_selfreport(sess):
    """自评行。没有自评的会话不能用于正式训练。"""
    out = []
    for r in _read(os.path.join(sess["dir"], f"{sess['session_id']}_selfreport.csv")):
        ts = _int(r.get("ts_ms"))
        if ts is None or not r.get("label7"):
            continue
        out.append([ts, r.get("image_id") or "", r["label7"], r.get("valence") or "", r.get("arousal") or ""])
    out.sort(key=lambda x: x[0])
    return out


def write_session(out_root, sess, fea_rows, images, reports, dry_run=False):
    subject = _safe(sess["subject_id"], "anon")
    session_id = _safe(sess["session_id"], "s0")
    sess_dir = os.path.join(out_root, subject, session_id)
    if dry_run:
        return sess_dir, len(fea_rows), len(images)

    os.makedirs(sess_dir, exist_ok=True)
    with open(os.path.join(sess_dir, "fea.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_FEA_HEADER)
        for ts, vec in fea_rows:
            w.writerow([ts] + [round(float(x), 5) for x in vec])

    # 刺激块：一张图的展示区间 = 本条 ts → 下一条 ts
    with open(os.path.join(sess_dir, "stimulus.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_STIMULUS_HEADER)
        for i, im in enumerate(images):
            nxt = images[i + 1]["ts_ms"] if i + 1 < len(images) else None
            w.writerow([im["ts_ms"], f"img{im['id']}", im["dominant"] or "",
                        nxt if nxt is not None else "",
                        (nxt - im["ts_ms"]) if nxt is not None else ""])

    with open(os.path.join(sess_dir, "selfreport.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_SELFREPORT_HEADER)
        w.writerows(reports)

    meta = {
        "subject_id": subject,
        "session_id": session_id,
        "created_ms": sess.get("started_ms"),
        "ended_ms": sess.get("stopped_ms"),
        "source": "affect_csv",     # 标明这批来自链路 A（AffectData），不是头显直采的会话流
        "fea_dim": FEA_DIM,
        "emotion_order": config.TRAIN_CLASSES,
        "counts": {"fea": len(fea_rows), "stimulus": len(images), "selfreport": len(reports)},
        "trainable": bool(reports),  # 无自评 → 只能配循环标签，不可用于正式训练
        "note": "由 DataSet/MyDataSet/export_affect_timeline.py 从 AffectData CSV 导出",
    }
    with open(os.path.join(sess_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return sess_dir, len(fea_rows), len(images)


def main():
    ap = argparse.ArgumentParser(description="AffectData FEA 时间轴 → Capture/ 目录结构")
    ap.add_argument("--root", default=str(config.AFFECT_DATA_DIR),
                    help="链路 A 数据根目录（默认 config.AFFECT_DATA_DIR）")
    ap.add_argument("--out", default=str(config.CAPTURE_DIR),
                    help="导出根目录（默认 config.CAPTURE_DIR）")
    ap.add_argument("--subject", default=None, help="只导出该受试者")
    ap.add_argument("--min-frames", type=int, default=100,
                    help="FEA 帧数少于该值的会话跳过（默认 100）")
    ap.add_argument("--require-selfreport", action="store_true",
                    help="只导出有自评的会话（无自评的时间轴无法用于正式训练）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    args = ap.parse_args()

    sessions = fetch_sessions(args.root, args.subject)
    if not sessions:
        print(f"[错误] {args.root} 下没有会话" + (f"（subject={args.subject}）" if args.subject else ""))
        raise SystemExit(2)

    total_f = total_i = total_r = written = skipped = no_report = 0
    for sess in sessions:
        fea_rows = fetch_fea(sess)
        if len(fea_rows) < args.min_frames:
            print(f"[跳过] {sess['subject_id']}/{sess['session_id']}：仅 {len(fea_rows)} 帧 FEA")
            skipped += 1
            continue
        images = fetch_images(sess)
        reports = fetch_selfreport(sess)
        if not reports:
            no_report += 1
        if args.require_selfreport and not reports:
            print(f"[跳过] {sess['subject_id']}/{sess['session_id']}：无自评（--require-selfreport）")
            skipped += 1
            continue
        d, nf, ni = write_session(args.out, sess, fea_rows, images, reports, args.dry_run)
        span = (fea_rows[-1][0] - fea_rows[0][0]) / 1000.0
        rate = (len(fea_rows) - 1) / span if span > 0 else 0
        flag = "" if reports else "  ← 无自评，不可用于正式训练"
        print(f"[{'预览' if args.dry_run else '导出'}] {d}：{nf} 帧 FEA "
              f"（{span:.0f}s，约 {rate:.1f}Hz）, {ni} 张刺激图, {len(reports)} 条自评{flag}")
        total_f += nf
        total_i += ni
        total_r += len(reports)
        written += 1

    print(f"\n[完成] {written} 个会话，{total_f} 帧 FEA，{total_i} 张刺激图，{total_r} 条自评"
          + ("（dry-run，未写盘）" if args.dry_run else f" → {args.out}"))
    if skipped:
        print(f"[提示] 跳过 {skipped} 个会话")
    if no_report:
        print(f"\n[警告] {no_report} 个会话没有自评。自评是唯一独立于 FEA 的真值来源；")
        print("       没有它，这批时间轴只能配 --label-source fea（与 X 同源的循环标签），")
        print("       而那已被 prepare_timeline_dataset.py 默认拒绝。")
        print("       请让头显在每张刺激图后上报 POST /api/affect/selfreport。")
    if written and total_r:
        print(f"\n下一步：python DataSet/MyDataSet/prepare_timeline_dataset.py --root {args.out} \\")
        print("            --out DataSet/MyDataSet/timeline_sliced --label-source selfreport")
    raise SystemExit(0 if written else 1)


if __name__ == "__main__":
    main()
