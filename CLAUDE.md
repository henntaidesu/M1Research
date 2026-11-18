# CLAUDE.md

## Overview

M1 research project: collect a Meta Quest Pro facial-expression dataset and train on it. Subjects wear a Quest Pro and watch ComfyUI-generated 360° emotion-eliciting panoramas; the headset streams 70-dim FEA blendshapes (+ gaze, stimulus events, self-reports) to a FastAPI backend that writes them to `DataSet/MyDataSet/`. Models trained on that data predict emotion from FEA time series. Public image-FER datasets (`DataSet/PublicDataSet/`) remain for comparison training/evaluation.

Refactored on 2026-11-18 from `D:\AiProject\webFaceEmotionRec` (kept intact). The webcam real-time detection page, research log, about page, Postgres store, adb USB control and online `/api/predict` were dropped — see `Docs/重构报告_2026-11-18.md`.

## Layout

- `comfyui/` — bundled ComfyUI runtime (66 GB, not project source; ignore when searching). Launch with `start_comfyUI.bat`.
- `QuestProScene/webFaceEmotionRec2/` — Unity 6 headset app. `Assets/Scripts/BackendConfig.cs` holds the server address (`http://nas.makurochan.com:9500`, must match `config.PUBLIC_BASE_URL`). `Library/` is not copied; Unity rebuilds it on first open.
- `CoreCode/backend/` — FastAPI on port 9501. `main.py` serves; `train_timeline.py` is the CLI for training the FEA time-series model.
- `CoreCode/frontend/` — Vue 3 + Vite on port 9500; proxies `/api`, `/health` to 9501 and `/comfyui` to ComfyUI.
- `DataSet/MyDataSet/` — own data (`AffectData/`, `Capture/`, `Stimulus/`) and its pipeline scripts. `DataSet/PublicDataSet/` — FER2013/FER+/RAF-DB/AffectNet ImageFolders + prepare scripts.
- `Docs/` — task specs (任务书), Quest Pro connection/APK guides, roadmap.

## Commands

All Python runs in the conda env `webFaceEmotionRec` (torch + CUDA). Use the env's python directly if `conda run` output is noisy: `C:\Users\49879\miniconda3\envs\webFaceEmotionRec\python.exe`.

```powershell
.\start.bat                                   # backend + frontend
cd CoreCode/backend; python main.py           # backend only (CUDA required unless REQUIRE_CUDA=0)
cd CoreCode/frontend; npm run dev             # frontend only; npm run build to check it compiles
python CoreCode/backend/train_timeline.py DataSet/MyDataSet/timeline_sliced --loso
```

No test suite or linter.

## Key facts

- `config.ROOT` is the repo root (`src` → `backend` → `CoreCode` → root). Data paths: `DATASET_DIR` = PublicDataSet, `MY_DATASET_DIR` = MyDataSet, `CAPTURE_DIR`/`AFFECT_DATA_DIR`/`IMAGE_DIR` under it. Training outputs (`checkpoints/`, `Model/`, `Eval/`, `predict_checkpoints/`) live in `CoreCode/backend/`. `conf.ini` is at the repo root.
- **Two collection paths, one trainer format.** Path A: headset `POST /api/fea` during a web "情感偏好生成" session → `AffectData/<subject>/<session>_fea.csv` (`use_store/csv_store.py`). Path B: Unity `SessionUploader` → `/api/session/*` → `Capture/<subject>/<session>/` (`use_capture/session_store.py`). `prepare_timeline_dataset.py` reads only the Capture format; `export_affect_timeline.py` converts A → B.
- **Self-reports are the only label independent of FEA.** Rule labels from `classify_fea` are computed from the same FEA (circular), so slicing defaults to `--label-source selfreport`. Fewer than 3 subjects ⇒ no val split; use LOSO.
- 7 classes, fixed order `angry disgust fear happy neutral sad surprise`: `config.TRAIN_CLASSES`, `use_model/fea_emotion.py` `_EN_ORDER`, Unity `VREmotionSelectionUI.cs` `EmotionKeys` must agree. 70-dim FEA order is `fea_emotion.OVR_FACE_EXPRESSIONS`; legacy 63-dim input is zero-padded. `prepare_timeline_dataset.py` duplicates the rule table — run `check_rule_parity.py` after changing either.
- **Deployment:** headset and laptop go to external networks and dial out to the home server's public address on port 9500 (only 9500 is open; Vite proxies `/api` to 9501). The server cannot reach the headset; liveness is the headset's `/api/stimulus/control?client=vr` heartbeat. Headset view is peer-to-peer WebRTC (backend only relays SDP); over WAN configure TURN in `conf.ini [webrtc]`.
- **Auth:** `conf.ini [auth] password` empty ⇒ login disabled. Endpoints the Unity app calls are allowlisted in `use_web/auth.py` `_PUBLIC_EXACT` (Unity sends no cookie). Site is plain http.
- **Stimulus library (`use_stimlib`, page `/cn/comfyui/stimlib`):** generates image/video stimulus *pairs* from one panorama (Qwen-Image 360 → Wan2.2 TI2V-5B I2V), so the two lines differ only in motion. Backend talks to ComfyUI directly (`config.COMFYUI_URL`); outputs in `Stimulus/lib/<pair_id>/`, ratings in `Stimulus/lib/ratings.csv`, sequence runs (events + FEA) in `Stimulus/lib/runs/`. Headset side is `StimulusPlayer.cs` (auto-bootstrapped, touches the skybox only while playback is active). The backend owns the clock; the stimulus phase starts timing only after the headset reports `playing`/`shown`. Compare lines with `python CoreCode/backend/compare_stimlib.py`. Guide: `Docs/刺激库_图片线与视频线_使用说明.md`.
- Image FER: `use_train` (efficientnet_b2 / vr_cnn, VR eye-band occlusion augmentation) and `use_eval`. The built-in HSEmotion model (`use_model/models.py`, MTCNN + enet_b2_7) is loaded lazily only as an evaluation baseline; `torch_patch.py` must stay imported before `facenet_pytorch`/`hsemotion`.
- Heavy deps (torch/timm/torchvision) are imported lazily inside training functions to keep backend startup fast.
