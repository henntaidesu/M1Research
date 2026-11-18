# 程序任务书：VR 微情感时序采集与情绪预测（闭环）

> 版本 v1.0 · 归属项目：webFaceEmotionRec
> 本文件是**软件开发的执行依据**（项目内部任务书，与代码库一同维护）。
> 关联：[研究路线图.md](研究路线图.md)、[任务书_QuestPro_VR表情数据集采集.md](任务书_QuestPro_VR表情数据集采集.md)、[任务书_微情感生成与采集.md](任务书_微情感生成与采集.md)。

---

## 0. 一句话目标

新增一套「**Quest Pro FEA 时序闭环**」程序：ComfyUI（网页）生成的 360° 全景经 **PC 后端实时下发**到头显 **Unity 原生 App** 呈现（Skybox）；用户观看时头显**高帧率同步采集** 70 维 FEA + 眼动/注视 + 刺激呈现事件，并在**每张图后自评**（7 类 + SAM valence/arousal）；四路信号经 **LAN 推送到 PC 后端**，后端**统一打时间戳**落盘为**时间轴数据集**；后端在线 `/api/predict` 用**过去数秒序列**预测**未来几秒的 7 类情绪走向**，预测结果可**反馈驱动下一张刺激**，形成闭环。

---

## 1. 范围与非目标

**本任务书包含：**
- 头显侧采集 App（Unity，改造现有 3 个脚本）。
- 后端会话记录器（SessionRecorder）+ **新的时间轴数据格式**。
- 在线预测服务 `/api/predict` + 预测模型的**训练/推理接口契约**（含闭环反馈）。
- 离线数据管线（滑窗切片 + 微表情标注，纯 Python）。

**本任务书不包含（非目标）：**
- ❌ 外接相机 / 下半脸图像模态（**已取消**，本系统为纯 FEA 时序）。
- ❌ 现有「VR 遮挡图像式 FER」训练主线（`backend/src/use_train` 那条线不动）。
- ❌ 预测模型的**精度承诺**——本任务书只交付可运行的基线与接口，算法调优属后续。

---

## 2. 关键约束（先理解，它决定整套方案）

1. **Quest Pro 隐私红线**：应用拿不到内向摄像头的原始人脸图，只有 **70 维 FEA blendshape**（`OVRPlugin.FaceExpression2`，0~1）。所以「采表情」= 采 FEA 时间序列。
2. **微表情靠帧率**：微表情持续 < 0.5s，取消图像后**只能靠 FEA 高帧率捕捉**。采集帧率 **≥30Hz（目标 60Hz）**，远高于现有 `QuestFeaCollector` 的 10Hz。
3. **7 类固定顺序**：`angry disgust fear happy neutral sad surprise`。三处真源必须一致：`backend/src/use_model/fea_emotion.py` 的 `_EN_ORDER`、`backend/src/config.py` 的 `TRAIN_CLASSES`、Unity `VREmotionSelectionUI.cs` 的 `EmotionKeys`。**改类别集须同步三处。**
4. **70 维顺序真源**：`fea_emotion.OVR_FACE_EXPRESSIONS`（官方 `OVRPlugin.FaceExpression2` 名，与 `DataSet/quest_pro_capture_spec.md`、`prepare_quest_pro.py` 对齐）。落盘的 `f0..f69` 必须严格按此序；旧版 63 列会话读入时末尾补 0。
5. **语言约定**：后端 / 数据 / 管线一律 **Python**；头显采集是 **C#**（Meta Face Tracking 只经 Unity/Meta XR SDK 暴露，属平台硬限制例外）。见 `研究路线图.md` B9。

---

## 3. 系统架构（数据流）

```
 ┌──────────────┐   生成360全景    ┌──────────────────────────┐
 │ ComfyUI (网页)│ ───────────────► │  PC 后端 (FastAPI:9501)   │
 └──────────────┘                  │  · 全景存储 + 实时下发     │
                                   │  · SessionRecorder 落盘    │
                                   │  · /api/predict 在线预测   │
                                   └───────┬───────────▲────────┘
                实时下发全景(HTTP)          │           │ 四路事件(带 timestamp_ms)
                          ┌────────────────▼──┐        │  POST /api/session/ingest
                          │ Quest Pro          │        │
                          │ Unity 原生 App     │────────┘
                          │ · Skybox 呈现全景  │
                          │ · 采 FEA(≥30Hz)    │  采集四路：
                          │ · 采 gaze          │   ① 70维FEA 时序    ③ 刺激呈现事件
                          │ · 记刺激事件        │   ② 眼动/注视 时序  ④ 自评(7类+SAM)
                          │ · 每张图后自评UI   │
                          └────────────────────┘
                 闭环：/api/predict 预测未来几秒情绪 ──► 选/生成下一张刺激 ──► 下发头显
```

---

## 4. 模块划分与工作量

> 标注「改造」= 在现有脚本上扩展；「新增」= 全新代码。每模块给验收点见 §8。

### M-A 头显采集 App（Unity C#，改造现有 3 脚本）
现有：`Unity/2/webFaceEmotionRec2/Assets/Scripts/`（6.0 LTS，已选定，见 `研究路线图`/记忆 unity-version-vr-project）。

| 编号 | 任务 | 现状 → 目标 |
|---|---|---|
| A1 | **高帧率 + gaze 采集** | `QuestFeaCollector` 10Hz、只采 FEA → 采样率提到 **≥30Hz（目标 60Hz）**，同时采 **眼动/注视**（`OVREyeGaze` / `OVRFaceExpressions` 配套），带单调 `timestamp_ms`。 |
| A2 | **刺激实时下发** | `VREmotionSelectionUI`/`QuestFeaCollector` 从 `Resources/stimulus` **静态烘焙** → 运行时用 **`UnityWebRequestTexture`** 从后端拉全景 + 设 Skybox；记录**刺激呈现事件**（`image_id`、`target_emotion`、onset/offset `timestamp_ms`、dwell）。 |
| A3 | **自评加 SAM** | `VREmotionSelectionUI` 现只有 7 类单选 → 每张图后追加 **SAM 两条 1–9 刻度**（valence 愉悦度 / arousal 唤醒度），头显内世界空间 Canvas + 手柄射线点选（复用现有 laser / BoxCollider 交互）。 |
| A4 | **网络推送 + 同步事件** | 现为本地 CSV / 单机记录 → 四路（FEA 帧流、gaze、刺激事件、自评事件）**POST 到后端** `/api/session/ingest`；session 起始做一次**同步事件**（屏幕闪白 + 手柄按键 + 记 `timestamp_ms`）供对齐。 |

> 现有 `QuestProFaceCapture.CaptureSample(label)` 是「事件式单帧」，A 系列后它退化为「自评瞬间打一条 selfreport 事件」，连续 FEA 由 A1 的高频流承担。

### M-B 后端 SessionRecorder（Python，新增）
- **B1 路由**：`POST /api/session/start`（建 session，返回 `session_id`）、`POST /api/session/ingest`（收多路带时间戳事件，批量或单条）、`POST /api/session/stop`（收尾、写 `meta.json`）。
- **B2 落盘**：按 §5 的时间轴格式写盘；高频 FEA 建议追加写 CSV/Parquet，避免内存堆积。
- **B3 时间同步**：统一**单调时钟**基准；用 A4 的同步事件求头显↔后端 `sync_offset_ms`，写入 `meta.json`。
- 与现有 `POST /api/fea` 的关系：`/api/fea` 保留（微情感生成页在用），本模块是**独立的会话级时序通道**，不复用单帧最近一帧的 `_latest_fea`。

### M-C 在线预测服务（Python，新增，**含接口契约**）
- **C1 `/api/predict`**：输入过去 `window_s` 秒的四路滑窗序列 → 输出未来 **`horizon_s` 秒**（可配，默认建议 **3s**，中时）的 7 类分布。响应结构与图像识别路径同构（`dominant_en` / `dominant` / `emotions{中文:pct}`）+ 可选 `trajectory`。字段详见 §6。
- **C2 预测模型接口契约**：训练 `train_predict(dataset_dir, window_s, horizon_s) -> model`；推理器与现有 `model_registry` 风格一致（可加载/切换/缓存）。**首版给基线**（FEA 序列 + 轻量时序模型，如 **GRU / TCN**），接口稳定后可替换更强模型。
- **C3 闭环反馈（可选打开）**：`/api/predict` 结果驱动**下一张刺激的选择/生成**（预测将转向某情绪 → 下发对应/对冲情绪的全景），与 M-A 的实时下发对接。

### M-D 离线数据管线（Python，新增）
- **D1 滑窗样本化**：时间轴数据集 → `(window_s 序列, 未来 horizon_s 标签)` 训练样本；**按受试者划分** train/val/test（禁止同人跨 split）。
- **D2 微表情标注**：从高频 FEA 序列**自动检测 onset / apex / offset**（基于 blendshape 强度上升-峰值-回落阈值），产出 `microevents.csv`；保留人工校验字段。可复用 `fea_emotion.classify_fea` 把每帧映射到 7 类做辅助标注。

---

## 5. 时间轴数据格式（新格式）

> 现有 `DataSet/quest_pro_capture_spec.md` 是「**图像 + 单样本**」式（每个 `sample_id` 一个 json + jpg），**不适用**于本任务的连续多路时序。本格式与它**并存、不复用**。

按 `<subject_id>/<session_id>/` 组织，多路时间序列 + 事件流分文件：

```
<capture_root>/
  <subject_id>/
    <session_id>/
      meta.json          # 见下
      fea.csv            # 高频：timestamp_ms, f0..f69   （OVRPlugin.FaceExpression2 顺序，值 0~1）
      gaze.csv           # timestamp_ms, gaze_dir_x/y/z, [pupil/confidence 可选]
      stimulus.csv       # onset_ms, image_id, target_emotion, offset_ms, dwell_ms
      selfreport.csv     # timestamp_ms, image_id, label7, valence(1-9), arousal(1-9)
      microevents.csv    # (D2 生成) onset_ms, apex_ms, offset_ms, peak_au, emotion, verified
  splits.json            # {"train":[subj...], "val":[...], "test":[...]}  按受试者划分
```

`meta.json` 关键字段：
```json
{
  "subject_id": "p07",
  "session_id": "s01",
  "created_utc": "...",
  "sync_offset_ms": 0,
  "fea_rate_hz": 60,
  "gaze_rate_hz": 60,
  "emotion_order": ["angry","disgust","fear","happy","neutral","sad","surprise"],
  "fea_dim": 63,
  "fea_names": ["BrowLowererL", "...", "UpperLipRaiserR"],
  "device": "Meta Quest Pro",
  "app_build": "..."
}
```

- **划分**：按受试者，禁止同人跨 split（防身份泄漏，沿用既有惯例）。
- **数据不入库**：`.gitignore` 排除 `<capture_root>`，仅提交采集/处理脚本（沿用 `DataSet/` 惯例）。

---

## 6. 预测接口契约（详）

**`POST /api/predict`**

请求：
```json
{
  "window_s": 5,
  "horizon_s": 3,
  "window": {
    "fea":        [[timestamp_ms, f0, ..., f62], ...],
    "gaze":       [[timestamp_ms, gx, gy, gz], ...],
    "stimulus":   [{"onset_ms":..., "image_id":"...", "target_emotion":"happy"}, ...],
    "selfreport": [{"timestamp_ms":..., "label7":"happy", "valence":7, "arousal":6}, ...]
  }
}
```

响应：
```json
{
  "success": true,
  "horizon_s": 3,
  "pred_time_ms": 1234567890,          // 预测所指向的未来时刻
  "dominant_en": "sad",
  "dominant": "悲伤",
  "emotions": {"愤怒": 5.1, "厌恶": 2.0, "恐惧": 3.3, "开心": 10.2, "平静": 20.0, "悲伤": 55.4, "惊讶": 4.0},
  "trajectory": [                       // 可选：逐步的未来分布
    {"t_ms": ..., "emotions": {"...": ...}}
  ]
}
```

- **与 `fea_emotion.classify_fea` 的差异**：`classify_fea` 是「**单帧 → 当前情绪**」（规则式）；`/api/predict` 是「**过去序列 → 未来情绪**」（时序模型）。二者响应结构刻意同构，前端复用同一套颜色/emoji 映射。
- **训练接口**：`train_predict(dataset_dir, window_s, horizon_s) -> model`，读 §5 数据集出 D1 滑窗样本；部署后 `/api/predict` 加载该模型。
- **输入信号**（本轮确认）：FEA 时序 + gaze 时序 + 刺激事件 + 自评序列，四路都作为模型输入上下文。

---

## 7. 里程碑（里程碑驱动，无严格截止）

| 阶段 | 交付物 | 依赖 | 状态 |
|---|---|---|---|
| **M1** | 后端 SessionRecorder + 时间轴格式（dummy 事件可端到端落盘） | 纯软件，离线可测 | ✅ 已实现并冒烟通过 |
| **M2** | Unity 采集 App（A1–A4：帧率/gaze/SAM/网络推送/实时下发） | 需 Unity 工程 | ✅ 脚本就绪，待编辑器编译/真机 |
| **M3** | 头显↔后端整链联调 + 回放对齐（同步偏移 < 1 帧） | **需真机** | ⏳ 待真机 |
| **M4** | 数据管线（D1 滑窗样本 + D2 微表情标注） | 依赖 M1 格式 | ✅ 已实现并冒烟通过 |
| **M5** | 在线预测 `/api/predict`（规则式基线 + GRU 训练接口）+ 闭环反馈 | 闭环**需真机** | ✅ 规则式基线可用/训练接口就绪；闭环⏳待真机 |

> **本轮已实现的文件**（离线部分经 `scratchpad/smoke_fea_pipeline.py` 全链冒烟：会话落盘→predict→D1→D2 均通过）：
> - 后端：[../backend/src/use_capture/session_store.py](../backend/src/use_capture/session_store.py)（M-B）、
>   [../backend/src/use_predict/predictor.py](../backend/src/use_predict/predictor.py) +
>   [seq_model.py](../backend/src/use_predict/seq_model.py) +
>   [train_predict.py](../backend/src/use_predict/train_predict.py)（M-C）、
>   [app.py](../backend/src/use_web/app.py) 挂 `/api/session/*` 与 `/api/predict/*`、`config.py` 增采集/预测配置。
> - 数据管线：[../DataSet/prepare_timeline_dataset.py](../DataSet/prepare_timeline_dataset.py)（D1）、
>   [../DataSet/label_microevents.py](../DataSet/label_microevents.py)（D2）。
> - Unity：`Unity/2/webFaceEmotionRec2/Assets/Scripts/SessionUploader.cs`（网络客户端）+ `VRSessionCollector.cs`（A1–A4 编排）。
>   头显侧无法离线编译，需在 Unity 6.0 LTS 工程里挂到 `OVRCameraRig` 场景后 Build 到真机验证（M3）。

---

## 8. 验收

- **每模块可验证**：
  - M-B/M-C/M-D：后端 **dummy 事件冒烟**（无头显即可跑通落盘/预测/切片）。
  - M-A：Unity **编辑器模拟**（鼠标代手柄，见 `VREmotionSelectionUI` 已有的编辑器分支）。
  - M-A↔M-B：**真机联调**，四路整链落盘后可**完整回放对齐**。
- **格式正确**：`fea.csv` 为 70 维且按 `OVR_FACE_EXPRESSIONS` 顺序；`meta.json.emotion_order` 与三处真源一致。
- **预测接口**：`/api/predict` 返回结构符合 §6，`horizon_s` 可配，响应与 `classify_fea` 同构。
- **无泄漏**：`splits.json` 按受试者划分，同人不跨 split。

---

## 9. 与现有代码 / 资产对接点（勿重复造）

- **Unity 三脚本**：`Unity/2/webFaceEmotionRec2/Assets/Scripts/{QuestFeaCollector,QuestProFaceCapture,VREmotionSelectionUI}.cs`（6.0 LTS 版，Meta XR SDK 203）。
- **后端**：`backend/src/use_web/app.py`（已有 `POST /api/fea` / `GET /api/fea/latest`，本任务在其上加 `/api/session/*` 与 `/api/predict`）。
- **FEA 规则式基线**：`backend/src/use_model/fea_emotion.py`（`classify_fea`、`OVR_FACE_EXPRESSIONS`、`_EN_ORDER`）——可复用为单帧特征 / 辅助标注。
- **7 类真源**：`fea_emotion._EN_ORDER` / `config.TRAIN_CLASSES` / `VREmotionSelectionUI.EmotionKeys`（三处一致）。
- **刺激素材**：「VR 刺激图」面板 + `qwen360_pano` 工作流 + `public/vr_stimulus_prompts.csv`（生成 360 全景，供实时下发）。
- **语言约定**：后端/数据/管线 Python；Unity C# 属平台限制例外（见 `研究路线图` B9）。

---

*信息来源：项目代码库核对（`app.py` FEA 路由、`fea_emotion.py` 63 维顺序与 7 类顺序、Unity 三脚本现状）+ 本轮需求确认。*
