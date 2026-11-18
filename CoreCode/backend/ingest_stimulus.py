"""命令行：把外部视频导入刺激清单（Stimulus/catalog/），见 src/use_stimlib/ingest.py。

导入的条目一律 enabled=false、qc=pending，要在网页「④ 实验流程 → 其他刺激」里质检、启用后才进实验。
--license 必填（例如 "lab-internal only (Li 2017, no redistribution)" 或 "CC BY-NC 4.0"）。

用法（仓库根目录）：
    # 单个 360° 视频，取 10 s 起的 45 s
    python CoreCode/backend/ingest_stimulus.py D:/raw/abandoned.mp4 --kind skybox_video --id vid_li_abandoned \\
        --condition LVLA --start 10 --duration 45 --norm-valence 2.9 --norm-arousal 4.1 --norm-n 95 \\
        --norm-source "Li 2017" --source "Stanford VHIL 360 database" --license "lab-internal only"
    # 模仿片段
    python CoreCode/backend/ingest_stimulus.py D:/raw/f01_happy.mp4 --kind mimicry --condition HVHA \\
        --target-emotion happy --duration 8 --license "self-recorded, actor release signed"
    # 批量：CSV 列 file,id,kind,condition,target_emotion,start_s,duration_s,norm_valence,norm_arousal,
    #       norm_sd_v,norm_sd_a,norm_n,norm_source,source,license,ethics_tier,rating,name,habituation_group,note
    python CoreCode/backend/ingest_stimulus.py --manifest D:/raw/videos.csv
    # 只探测、不转码
    python CoreCode/backend/ingest_stimulus.py D:/raw/x.mp4 --kind skybox_video --license x --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.use_stimlib import ingest  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="外部视频 → 刺激清单（转码 + 响度归一 + 低层特征 + catalog/<id>.json）")
    ap.add_argument("file", nargs="?", help="输入视频（与 --manifest 二选一）")
    ap.add_argument("--manifest", help="批量清单 CSV（file 列相对 CSV 所在目录）")
    ap.add_argument("--kind", choices=ingest.INGEST_KINDS, help="skybox_video（360° 等距柱状）/ mimicry（平面片段）")
    ap.add_argument("--id", default="", help="条目 id（字母数字_-），默认按文件名生成 vid_* / mim_*")
    ap.add_argument("--name", default="")
    ap.add_argument("--condition", default="", help="HVHA / HVLA / LVHA / LVLA / NEU（默认 NEU）")
    ap.add_argument("--target-emotion", default="", help="7 类之一（模仿片段必填为宜）")
    ap.add_argument("--rating", default="", choices=["", "full", "quick", "none"],
                    help="默认 skybox_video=full、mimicry=quick")
    ap.add_argument("--start", type=float, help="裁剪起点（秒）")
    ap.add_argument("--duration", type=float, help="裁剪时长（秒）")
    ap.add_argument("--norm-valence", type=float)
    ap.add_argument("--norm-arousal", type=float)
    ap.add_argument("--norm-sd-v", type=float)
    ap.add_argument("--norm-sd-a", type=float)
    ap.add_argument("--norm-n", type=int)
    ap.add_argument("--norm-source", default="")
    ap.add_argument("--source", default="", help="素材出处（库名 / URL / 录制人）")
    ap.add_argument("--license", default="", help="许可证 / 使用范围（必填；批量时作为 CSV 空值的默认）")
    ap.add_argument("--ethics-tier", type=int, default=None, choices=[0, 1, 2])
    ap.add_argument("--habituation-group", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-audio", action="store_true", help="丢掉音轨（audio=false）")
    ap.add_argument("--force", action="store_true", help="非 2:1 的视频也强行拉伸成等距柱状")
    ap.add_argument("--overwrite", action="store_true", help="覆盖同 id 的已有条目和素材")
    ap.add_argument("--catalog-dir", help="写到别的 catalog 目录（测试用；素材进 <dir>/assets/）")
    ap.add_argument("--dry-run", action="store_true", help="只探测和校验，不转码、不写文件")
    args = ap.parse_args()

    if bool(args.file) == bool(args.manifest):
        ap.error("给一个输入文件，或用 --manifest 批量导入（二选一）")

    if args.manifest:
        defaults = {"license": args.license, "source": args.source, "kind": args.kind or "",
                    "condition": args.condition, "norm_source": args.norm_source,
                    "ethics_tier": "" if args.ethics_tier is None else args.ethics_tier}
        res = ingest.ingest_manifest(args.manifest, catalog_dir=args.catalog_dir, force=args.force,
                                     overwrite=args.overwrite, dry_run=args.dry_run, defaults=defaults)
        ok = sum(1 for r in res if r[1])
        print(f"\n[完成] {ok}/{len(res)} 条导入成功" + ("（dry-run，未写文件）" if args.dry_run else ""))
        raise SystemExit(0 if ok == len(res) and res else 1)

    if not args.kind:
        ap.error("单个导入必须给 --kind")
    if not args.license.strip():
        ap.error("--license 必填：素材的许可证 / 使用范围不明确的不导入")
    try:
        it = ingest.ingest(
            args.file, kind=args.kind, license=args.license, item_id=args.id, condition=args.condition or "NEU",
            target_emotion=args.target_emotion, start_s=args.start, duration_s=args.duration, rating=args.rating,
            name=args.name, source=args.source, ethics_tier=args.ethics_tier or 0,
            habituation_group=args.habituation_group, note=args.note,
            norms=ingest._norms(args.norm_valence, args.norm_arousal, args.norm_sd_v, args.norm_sd_a, args.norm_n,
                                args.norm_source),
            force=args.force, no_audio=args.no_audio, overwrite=args.overwrite, catalog_dir=args.catalog_dir,
            dry_run=args.dry_run)
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as e:
        print(f"[失败] {e}", file=sys.stderr)
        raise SystemExit(1)
    if args.dry_run:
        print(json.dumps(it, ensure_ascii=False, indent=1))
        return
    print(f"[完成] {it['id']}: {ingest.summary(it)}")
    print("  条目已停用（enabled=false, qc=pending），质检后在网页里启用。")


if __name__ == "__main__":
    main()
