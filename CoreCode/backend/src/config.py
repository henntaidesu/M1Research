"""集中配置。支持通过环境变量覆盖关键项。"""
import os
from pathlib import Path


def _env_flag(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# ── 服务监听 ──────────────────────────────────────────────────────
HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
PORT = int(os.getenv("BACKEND_PORT", "9501"))

# ── 本服务的公网地址 ──────────────────────────────────────────────
# 头显和笔记本都会被带到外网使用，服务器留在家里：所以两边都是**主动连它**。
# 对外只开放 9500（网页端口），后端 9501 不暴露——Vite 把 /api、/ws、/health 反代过去，
# 而头显要的路径全在 /api 下。这个值要和 Unity 侧 BackendConfig.DefaultBaseUrl 一致。
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://nas.makurochan.com:9500")

# ── 网页登录 ──────────────────────────────────────────────────────
# 服务已经开在公网上，训练/评测/删模型/系统设置这些接口不能谁都能调。
# 口令**留空＝不启用登录**（本机调试）；实际值以 conf.ini [auth] 为准，改完即时生效。
# 换口令会让已发出的会话全部失效（签名密钥就是从用户名+口令派生的）。
# 注意站点是 http 明文：口令和会话 Cookie 在链路上都不加密，这挡的是顺手点进来
# 的人，不是能抓包的攻击者——要真防住得上 HTTPS。
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")
AUTH_SESSION_DAYS = float(os.getenv("AUTH_SESSION_DAYS", "7"))

# ── 头显视角 WebRTC 点对点回传 ────────────────────────────────────
# 画面走头显↔浏览器直连，后端只转交 SDP（几 KB），不经手任何像素。
# 外网场景两端多半各在 NAT 后：STUN 只能打通「非对称 NAT」，两边都是对称 NAT 时
# 必须有 TURN 中继，否则连不上。TURN_URL 留空＝只用 STUN。
# 这些值是 conf.ini [webrtc] 的初值，实际以 conf.ini 为准（设置页可改）。
STUN_URLS = os.getenv("STUN_URLS", "stun:stun.l.google.com:19302")
TURN_URL = os.getenv("TURN_URL", "")
TURN_USERNAME = os.getenv("TURN_USERNAME", "")
TURN_CREDENTIAL = os.getenv("TURN_CREDENTIAL", "")

# ── CUDA 设备 ─────────────────────────────────────────────────────
# 默认强制使用 CUDA：无可用 GPU 时直接报错退出。
# 设置环境变量 REQUIRE_CUDA=0 可关闭，改为在无 GPU 时回退到 CPU。
REQUIRE_CUDA = _env_flag("REQUIRE_CUDA", True)
# 指定使用的 GPU 序号
CUDA_DEVICE_INDEX = int(os.getenv("CUDA_DEVICE_INDEX", "0"))

# ── 模型 ──────────────────────────────────────────────────────────
# enet_b2_7：EfficientNet-B2，7 类情感（不含 contempt，与 DeepFace 标签一致）
EMOTION_MODEL = os.getenv("EMOTION_MODEL", "enet_b2_7")

# ── DeepSeek LLM（情感变化 → 图像生成提示词）─────────────────────────
# 把用户表情/情感随时间的「变化轨迹」发给 DeepSeek，由其生成对应的图像生成
# 提示词（见 use_llm/deepseek.py）。DeepSeek 为 OpenAI 兼容接口。
# 需设置环境变量 DEEPSEEK_API_KEY 才启用；未配置时 /api/prompt/* 报未配置。
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "30"))
# 创意类生成，温度偏高（DeepSeek 建议创意写作 ~1.5，取 1.3 兼顾稳定）
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "1.3"))

# ── MTCNN 检测参数（仅评测内置 HSEmotion 基线时加载）─────────────────
MIN_FACE_SIZE = 20
MTCNN_THRESHOLDS = [0.6, 0.7, 0.7]

# ── 推理线程池大小 ────────────────────────────────────────────────
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "2"))

# ── 路径 ──────────────────────────────────────────────────────────
# src/ → backend/ → CoreCode/ → 仓库根
ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = ROOT / "CoreCode" / "backend"
# 自采数据集（采集落盘、刺激图）与公开数据集（训练/评测用 ImageFolder）分开存放
MY_DATASET_DIR = Path(os.getenv("MY_DATASET_DIR", str(ROOT / "DataSet" / "MyDataSet")))
DATASET_DIR = Path(os.getenv("DATASET_DIR", str(ROOT / "DataSet" / "PublicDataSet")))
CHECKPOINT_DIR = Path(os.getenv("CHECKPOINT_DIR", str(BACKEND_DIR / "checkpoints")))
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
# 训练记录与模型按「每次训练一个文件夹」存放于此（含 metrics.csv / meta.json / 滚动权重）
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(BACKEND_DIR / "Model")))
MODEL_DIR.mkdir(parents=True, exist_ok=True)
# 模型评测结果按「每次评测一个文件夹」存放于此（含 result.json：混淆矩阵 + 逐类指标）
EVAL_DIR = Path(os.getenv("EVAL_DIR", str(BACKEND_DIR / "Eval")))
EVAL_DIR.mkdir(parents=True, exist_ok=True)
# 网页生成的刺激图按情感分目录存放于此（Stimulus/<emotion>/<时间戳>.png）
IMAGE_DIR = Path(os.getenv("IMAGE_DIR", str(MY_DATASET_DIR / "Stimulus")))
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# ── 刺激库：图片线 / 视频线成对生成 + 头显播放 + 方案对比（use_stimlib）──────
# 放在 IMAGE_DIR 下，复用 /api/stimulus/files 静态服务（头显下载路径已在登录放行清单里）。
# lib/<pair_id>/{pano.png, still.jpg, video.mp4, thumb.jpg, meta.json}
# lib/ratings.csv：每次呈现后的自评；lib/runs/<run_id>/：一次播放序列的事件与 FEA
STIMLIB_DIR = IMAGE_DIR / "lib"
STIMLIB_DIR.mkdir(parents=True, exist_ok=True)
# 后端直连 ComfyUI（批量生成在后端跑，不依赖浏览器开着）
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")

# 每个训练运行保留的最近权重文件数
KEEP_CHECKPOINTS = int(os.getenv("KEEP_CHECKPOINTS", "10"))

# ── 训练（图像 FER 模型）默认超参与可选数据集 ─────────────────────────
# 训练用主干：timm efficientnet_b2（预训练微调）或 vr_cnn（自定义 CNN，
# 面向 VR 头显遮挡场景，见 use_train/cnn_model.py）
TRAIN_BACKBONE = os.getenv("TRAIN_BACKBONE", "efficientnet_b2")
TRAIN_BACKBONE_CHOICES = ["efficientnet_b2", "vr_cnn"]
# 7 类，顺序与各数据集 ImageFolder 字母序一致
TRAIN_CLASSES = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
# 可训练/可评测的数据集白名单 → 相对 DATASET_DIR 的目录名
TRAIN_DATASETS = ["fer2013", "fer2013_plus", "rafdb", "affectnet"]

TRAIN_DEFAULTS = {
    "backbone": TRAIN_BACKBONE,
    "vr_occlusion": True,   # 训练时模拟 VR 头显遮挡上半脸（验证集按固定遮挡评估）
    "epochs": 20,
    "batch_size": 128,
    "lr": 2e-4,
    "weight_decay": 1e-4,
    "freeze_epochs": 3,
    "img_size": 224,
    "num_workers": 8,  # 多进程并行解码喂 GPU；Windows 下如遇兼容问题可调回 0
}

# ── FEA 时序闭环采集 + 情绪预测（任务书_VR微情感时序采集与情绪预测.md）──────
# Quest Pro 70 维 FEA blendshape 维度（Meta XR Core SDK OVRPlugin.FaceExpression2 顺序，
# 真源见 use_model/fea_emotion.OVR_FACE_EXPRESSIONS；旧版 63 维数据入库前补 0 到 70）
FEA_DIM = 70
# 头显 Unity 经 LAN 推来的会话时序按 <subject_id>/<session_id>/ 落盘于此
CAPTURE_DIR = Path(os.getenv("CAPTURE_DIR", str(MY_DATASET_DIR / "Capture")))
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
# 训练出的情绪预测（时序）模型：<id>.pt + <id>.json，独立于图像 FER 的 CHECKPOINT_DIR
PREDICT_CHECKPOINT_DIR = Path(os.getenv("PREDICT_CHECKPOINT_DIR", str(BACKEND_DIR / "predict_checkpoints")))
PREDICT_CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

# ── 「情感偏好生成」采集数据的 CSV 存储 ──────────────────────────────────
# <AFFECT_DATA_DIR>/<测试者名>/ 下
# sessions.csv / <session_id>_fea.csv / <session_id>_selfreport.csv / images.csv / seeds.csv
AFFECT_DATA_DIR = Path(os.getenv("AFFECT_DATA_DIR", str(MY_DATASET_DIR / "AffectData")))

# ── 情感偏好生成闭环默认参数 ──────────────────────────────────────────
# 生成一张图后，测量随后 reaction_window_s 秒内的 FEA 情绪反应；正向情绪
# （happy/surprise 减去 sad/angry/fear/disgust）均值 ≥ like_threshold 记为「喜欢」。
AFFECT_REACTION_WINDOW_S = float(os.getenv("AFFECT_REACTION_WINDOW_S", "6"))
AFFECT_LIKE_THRESHOLD = float(os.getenv("AFFECT_LIKE_THRESHOLD", "0.15"))
# 两次自动生成之间的冷却秒数（防抖）
AFFECT_COOLDOWN_S = float(os.getenv("AFFECT_COOLDOWN_S", "10"))
# 偏好检索的 ε-探索比例：这一比例的推荐名额从全库随机取，其余取最近邻。
# 全部走最近邻会让检索只在「喜欢过」的集合里打转（探索崩溃），也就无法证明
# 偏好模型优于随机——留出探索名额是做 A/B 的前提。
AFFECT_EPSILON = float(os.getenv("AFFECT_EPSILON", "0.2"))
