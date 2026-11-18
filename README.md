# M1Research — Quest Pro 情绪数据集采集与训练

受试者佩戴 Meta Quest Pro 观看 ComfyUI 生成的 360° 情绪诱导全景，头显采集 70 维面部表情（FEA）时序 + 自评，
后端落盘为自采数据集，并在其上训练 FEA 时序情绪预测模型；公开图像 FER 数据集用于对照训练与评测。

由 `D:\AiProject\webFaceEmotionRec` 重构而来（原项目保留未动），重构说明见 [Docs/重构报告_2026-11-18.md](Docs/重构报告_2026-11-18.md)。

## 目录

| 目录 | 内容 |
|---|---|
| `comfyui/` | 打包的 ComfyUI 运行时（绘世启动器）+ 工作流 `qwen_image_txt2img_fixed.json` |
| `QuestProScene/webFaceEmotionRec2/` | 头显采集 App（Unity 6 + Meta XR SDK）：刺激呈现、FEA 采集、自评、头显视角回传 |
| `CoreCode/backend/` | FastAPI 后端（9501）：刺激下发、FEA/会话/自评落盘、图像 FER 训练评测、时序模型训练 CLI |
| `CoreCode/frontend/` | Vue 3 网页（9500）：VR 刺激图、情感偏好生成、模型训练、模型评测、系统设置 |
| `DataSet/MyDataSet/` | 自采数据（AffectData / Capture / Stimulus）+ 处理与训练前流水线脚本 |
| `DataSet/PublicDataSet/` | FER2013 / FER+ / RAF-DB / AffectNet + 准备脚本 |
| `Docs/` | 任务书、连接与打包指南、研究路线图、本次重构报告 |

## 启动

```powershell
.\start_comfyUI.bat     # ComfyUI（127.0.0.1:8188，--listen）
.\start.bat             # 后端 + 前端（conda 环境 webFaceEmotionRec，首次自动装依赖）
```

浏览器打开 `http://localhost:9500/cn`（日文 `/jp`）。运行时设置在根目录 `conf.ini`（DeepSeek、WebRTC、登录口令）。

## 训练

- 自采数据集（FEA 时序）：流程见 [DataSet/README.md](DataSet/README.md)，最终 `python CoreCode/backend/train_timeline.py <切窗目录> [--loso]`。
- 公开数据集（图像 FER，模拟 VR 遮挡）：网页「模型训练」「模型评测」页。
