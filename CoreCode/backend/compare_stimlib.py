"""命令行：图片线 vs 视频线的诱发效果对比（与网页「刺激库与播放 → 方案对比」同一套计算）。

用法（仓库根目录）：
    python CoreCode/backend/compare_stimlib.py
    python CoreCode/backend/compare_stimlib.py --include-manual --json out.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.use_stimlib import compare  # noqa: E402

METRICS = {"hit": "操纵命中率", "arousal": "唤醒度", "extremity": "效价极端度", "fea_expr": "面部表情反应"}


def _f(v, d=3):
    return "–" if v is None else f"{v:.{d}f}"


def main():
    ap = argparse.ArgumentParser(description="图片线 vs 视频线 诱发效果对比")
    ap.add_argument("--include-manual", action="store_true", help="包含手动播放时的评分（默认只用实验序列）")
    ap.add_argument("--json", help="把完整结果另存为 JSON")
    args = ap.parse_args()

    r = compare.compare(args.include_manual)
    li, lv = r["lines"]["image"], r["lines"]["video"]
    print(f"评分 {r['n_ratings']} 条 | 图片线 {li['n_presentations']} 次 / {li['n_subjects']} 人 | "
          f"视频线 {lv['n_presentations']} 次 / {lv['n_subjects']} 人\n")
    print(f"{'指标':<10}{'图片':>10}{'视频':>10}{'视频−图片':>12}{'对数':>6}{'p':>8}")
    for k, name in METRICS.items():
        p = r["paired"][k]
        print(f"{name:<10}{_f(li[k]):>10}{_f(lv[k]):>10}{_f(p['mean_diff']):>12}{p['n_pairs']:>6}{_f(p['p']):>8}")
    print("\n低层特征配对差（视频−图片，除 motion 外应接近 0）：")
    for k, d in r["lowlevel_diff"].items():
        print(f"  {k:<13}{_f(d['mean_diff'], 4):>10}  ({d['n_pairs']} 对)")
    print("\n结论：")
    for v in r["verdict"]:
        print("  " + v)
    if args.json:
        Path(args.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
