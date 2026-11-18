# DataSet

两类数据分开存放：

| 目录 | 内容 | 用途 |
|---|---|---|
| `MyDataSet/` | 自采 Quest Pro 数据（70 维 FEA 时序 + 自评 + 刺激图） | 本研究的主数据集，训练 FEA 时序情绪预测模型 |
| `PublicDataSet/` | FER2013 / FER+ / RAF-DB / AffectNet（7 类 ImageFolder） | 图像 FER 训练与评测（网页「模型训练」「模型评测」页） |

7 类固定顺序：`angry disgust fear happy neutral sad surprise`。

## MyDataSet

后端运行时直接写入这里（路径见 `CoreCode/backend/src/config.py`）：

```
MyDataSet/
  AffectData/<受试者>/          链路 A：头显 POST /api/fea + 网页「情感偏好生成」会话
      sessions.csv, <session>_fea.csv, <session>_selfreport.csv, images.csv, seeds.csv
  Capture/<受试者>/<session>/   链路 B：Unity SessionUploader → /api/session/*（训练脚本读这个格式）
      meta.json, fea.csv, gaze.csv, stimulus.csv, selfreport.csv
  Stimulus/<emotion>/*.png      网页生成并推送到头显的 360° 刺激图
  timeline_sliced/              prepare_timeline_dataset.py 的输出（切窗后的 npz）
```

从采集到训练（在仓库根目录、conda 环境 `webFaceEmotionRec` 中运行）：

```powershell
# 1. 链路 A 的数据导成 Capture 格式（链路 B 已经是，跳过）
python DataSet/MyDataSet/export_affect_timeline.py --require-selfreport
# 2.（可选）自动标注微表情事件 / 校准规则分类器
python DataSet/MyDataSet/label_microevents.py --root DataSet/MyDataSet/Capture
python DataSet/MyDataSet/calibrate_classify_fea.py --root DataSet/MyDataSet/Capture
# 3. 按受试者切滑窗（标签默认用自评）
python DataSet/MyDataSet/prepare_timeline_dataset.py --root DataSet/MyDataSet/Capture --out DataSet/MyDataSet/timeline_sliced
# 4.（建议）可预测性预检
python DataSet/MyDataSet/predictability_check.py --dataset DataSet/MyDataSet/timeline_sliced
# 5. 训练（受试者 <3 人时加 --loso）
python CoreCode/backend/train_timeline.py DataSet/MyDataSet/timeline_sliced --loso
```

`check_rule_parity.py`：改动 FEA→情绪规则表后运行，确认离线标签与线上 `classify_fea` 一致。

## PublicDataSet

已准备好的 `train/val[/test]/<emotion>/` 树（原始下载 `_raw/` 未迁移，需要重建时再下载）。
重新生成：`prepare_fer2013.py` → `apply_ferplus.py`、`prepare_rafdb.py`、`prepare_affectnet.py`（各脚本顶部有来源说明）。
RAF-DB / AffectNet 为非官方镜像，许可属灰色地带，仅供个人研究。VR 相关公开数据集的获取方式见 `VR_datasets_access_guide.md`。
