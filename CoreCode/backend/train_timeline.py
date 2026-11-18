"""命令行训练情绪预测（时序）模型：读 prepare_timeline_dataset.py 切好的自采数据集。

用法（在仓库根目录运行）：
    python CoreCode/backend/train_timeline.py DataSet/MyDataSet/timeline_sliced
    python CoreCode/backend/train_timeline.py DataSet/MyDataSet/timeline_sliced --loso --epochs 20

受试者 <3 人时必须用 --loso（留一受试者交叉验证），见 src/use_predict/train_predict.py。
"""
import argparse
import json
import logging

from src.use_predict.train_predict import train_predict, train_predict_loso

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="训练 FEA 时序情绪预测模型（GRU）")
    ap.add_argument("dataset", help="切窗数据集目录（含 meta.json + *.npz）")
    ap.add_argument("--loso", action="store_true", help="留一受试者交叉验证（受试者 <3 人时必须）")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--layers", type=int, default=1)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    params = {"epochs": args.epochs, "batch_size": args.batch_size, "lr": args.lr,
              "hidden": args.hidden, "layers": args.layers}
    fn = train_predict_loso if args.loso else train_predict
    print(json.dumps(fn(args.dataset, params), ensure_ascii=False, indent=1))
