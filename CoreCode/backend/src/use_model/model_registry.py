"""图像 FER 模型注册表：列出可评测的模型、加载自训模型。

可用模型 = 内置 HSEmotion（enet_b2_7，评测基线） + 每次网页训练保存的自训模型。
自训模型以 `<id>.pt`（权重）+ `<id>.json`（元数据 sidecar）存于 config.CHECKPOINT_DIR。

TrainedEmotionRecognizer 与 hsemotion 的 HSEmotionRecognizer 接口兼容：
  - 属性 idx_to_class: {idx: 类名}
  - 方法 predict_emotions(rgb_ndarray, logits=False) -> (类名, scores)
"""
import json
import logging
import os

import numpy as np

from .. import config

logger = logging.getLogger(__name__)

BUILTIN_ID = "hsemotion"
BUILTIN_NAME = "HSEmotion（内置 enet_b2_7）"


# ── 自训模型识别器（接口对齐 HSEmotionRecognizer）─────────────────
class TrainedEmotionRecognizer:
    def __init__(self, ckpt_path: str, device):
        import torch
        from torchvision import transforms

        from ..use_train.cnn_model import create_model

        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        self.classes = ckpt["classes"]
        self.idx_to_class = {i: c for i, c in enumerate(self.classes)}
        img_size = int(ckpt.get("params", {}).get("img_size", 224))
        backbone = ckpt.get("backbone", config.TRAIN_BACKBONE)

        self._device = device
        self._torch = torch
        # vr_cnn → 自定义 CNN；其余 → timm（与训练端建模逻辑一致）
        self.model = create_model(backbone, len(self.classes), pretrained=False)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval().to(device)

        self._tf = transforms.Compose([
            transforms.Resize((img_size, img_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

    def predict_emotions(self, face_rgb: np.ndarray, logits: bool = False):
        """face_rgb: RGB uint8 ndarray（人脸裁剪）。"""
        from PIL import Image
        torch = self._torch
        x = self._tf(Image.fromarray(face_rgb)).unsqueeze(0).to(self._device)
        with torch.no_grad():
            out = self.model(x)[0]
            scores = out if logits else torch.softmax(out, dim=0)
        scores = scores.cpu().numpy()
        return self.classes[int(scores.argmax())], scores


# ── sidecar 读写 ──────────────────────────────────────────────────
def _sidecar_path(model_id: str) -> str:
    return str(config.CHECKPOINT_DIR / f"{model_id}.json")


def register_trained(meta: dict) -> None:
    """训练保存最佳权重后写入元数据 sidecar（供列表展示与加载）。"""
    with open(_sidecar_path(meta["id"]), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def _load_sidecars() -> list:
    out = []
    if not config.CHECKPOINT_DIR.is_dir():
        return out
    for entry in os.scandir(config.CHECKPOINT_DIR):
        if entry.name.endswith(".json"):
            try:
                with open(entry.path, encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception as e:  # noqa: BLE001
                logger.warning("读取模型元数据失败 %s: %s", entry.name, e)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


# ── 列表 ─────────────────────────────────────────────────
def list_models() -> dict:
    models = [{
        "id": BUILTIN_ID, "name": BUILTIN_NAME, "type": "builtin",
        "datasets": [], "val_acc": None, "macro_f1": None, "created_at": None,
    }]
    for m in _load_sidecars():
        models.append({
            "id": m["id"], "name": m.get("name", m["id"]), "type": "trained",
            "datasets": m.get("datasets", []),
            "val_acc": m.get("val_acc"), "macro_f1": m.get("macro_f1"),
            "created_at": m.get("created_at"),
        })
    return {"models": models}

