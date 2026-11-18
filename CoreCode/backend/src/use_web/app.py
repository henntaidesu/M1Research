"""FastAPI 应用：Quest Pro 数据采集（刺激下发 / FEA / 会话 / 自评）+ 模型训练与评测。"""
import asyncio
import base64
import binascii
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import torch
from fastapi import Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import config
from .. import settings_store
from ..use_capture import session_store
from ..use_model import labels
from ..use_model.device import select_device
from ..use_model.fea_emotion import classify_fea, normalize_blendshapes
from ..use_eval import eval_store, evaluation
from ..use_llm import deepseek
from ..use_store import csv_store
from ..use_train import train_store, training
from . import auth
from . import rtc_signal
from . import stimulus_control

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="人脸情感识别 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 登录闸门 ──────────────────────────────────────────────────────
# 服务已在公网上，网页用的接口一律要求会话 Cookie；头显 Unity 用的接口不带
# Cookie，按放行清单通过（清单与代价见 auth.py 顶部说明）。
@app.middleware("http")
async def require_login(request: Request, call_next):
    if auth.is_enabled() and not auth.is_public(request.method, request.url.path):
        if not auth.check_token(request.cookies.get(auth.COOKIE_NAME)):
            return JSONResponse(status_code=401, content={"ok": False, "error": "未登录"})
    return await call_next(request)


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """前端路由守卫用：要不要登录、当前是否已登录。"""
    authed = auth.check_token(request.cookies.get(auth.COOKIE_NAME))
    return {"required": auth.is_enabled(), "authenticated": authed or not auth.is_enabled()}


@app.post("/api/auth/login")
async def auth_login(body: dict = Body(default=None)):
    body = body or {}
    if not auth.is_enabled():
        return {"ok": True, "required": False}
    if not auth.verify(body.get("username"), body.get("password")):
        await asyncio.sleep(0.5)     # 稍微拖慢暴力猜口令
        return JSONResponse(status_code=401, content={"ok": False, "error": "用户名或口令错误"})
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(
        auth.COOKIE_NAME, auth.issue_token(),
        max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax", path="/",
    )
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


executor = ThreadPoolExecutor(max_workers=config.WORKER_THREADS)

# 启动即做 CUDA 设备选择（无 GPU 且强制 CUDA 时此处即报错）
device = select_device()

if not auth.is_enabled():
    logger.warning("网页登录未启用：conf.ini [auth] 的 password 为空，任何人都能访问后台接口")


@app.get("/health")
async def health_check():
    on_cuda = device.type == "cuda"
    return {
        "status": "ok",
        "message": "采集与训练服务运行中",
        "device": str(device),
        "gpu": torch.cuda.get_device_name(config.CUDA_DEVICE_INDEX) if on_cuda else None,
    }


# ── 模型训练（图像 FER）API ───────────────────────────────────────
@app.get("/api/train/datasets")
async def train_datasets():
    return training.list_datasets()


@app.get("/api/train/status")
async def train_status():
    return training.get_status()


@app.post("/api/train/start")
async def train_start(params: dict = Body(default=None)):
    try:
        return training.start_training(params or {})
    except RuntimeError as e:        # 已有任务在跑
        return JSONResponse(status_code=409, content={"ok": False, "error": str(e)})
    except ValueError as e:          # 参数非法
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/train/stop")
async def train_stop():
    return training.stop_training()


@app.get("/api/train/runs")
async def train_runs():
    """全部历史训练运行（供右侧面板下拉切换）。"""
    return train_store.list_runs()


@app.get("/api/train/runs/{run_id}")
async def train_run_detail(run_id: str):
    """某次训练的元数据与逐轮指标。"""
    run = train_store.get_run(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "训练记录不存在"})
    return run


# ── 模型评测（测试/验证集）API ────────────────────────────────────
@app.get("/api/eval/targets")
async def eval_targets():
    """可评测的模型与数据集（含 val/test 计数）。"""
    return evaluation.list_targets()


@app.get("/api/eval/status")
async def eval_status():
    return evaluation.get_status()


@app.post("/api/eval/start")
async def eval_start(params: dict = Body(default=None)):
    try:
        return evaluation.start_eval(params or {})
    except RuntimeError as e:        # 已有任务在跑
        return JSONResponse(status_code=409, content={"ok": False, "error": str(e)})
    except ValueError as e:          # 参数非法
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/eval/stop")
async def eval_stop():
    return evaluation.stop_eval()


@app.get("/api/eval/runs")
async def eval_runs():
    """全部历史评测记录（供下拉切换）。"""
    return eval_store.list_evals()


@app.get("/api/eval/runs/{eval_id}")
async def eval_run_detail(eval_id: str):
    """某次评测的完整结果（含混淆矩阵与逐类指标）。"""
    run = eval_store.get_eval(eval_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "评测记录不存在"})
    return run


@app.delete("/api/eval/runs/{eval_id}")
async def eval_run_delete(eval_id: str):
    if eval_store.delete_eval(eval_id):
        return {"ok": True}
    return JSONResponse(status_code=404, content={"ok": False, "error": "评测记录不存在"})


# ── 网页生成的刺激图落盘（image/<emotion>/<时间戳>.png）────────────
_IMAGE_EXT_ALLOWED = {"png", "jpg", "jpeg", "webp", "bmp"}


@app.post("/api/stimulus/save")
async def stimulus_save(body: dict = Body(default=None)):
    """接收前端生成的刺激图，按情感分目录存入 image/，文件名用时间戳（精确到秒）。

    请求体：{"emotion": str, "image": data-url/base64, "ext"?: str, "folder"?: str}
    """
    body = body or {}
    emotion = str(body.get("emotion") or "").strip().lower()
    if emotion not in config.TRAIN_CLASSES:
        return JSONResponse(status_code=400, content={"ok": False, "error": "非法情感类别"})

    data_url = body.get("image")
    b64 = data_url.split(",", 1)[-1] if isinstance(data_url, str) else ""
    if not b64:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少图像数据"})
    try:
        raw = base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return JSONResponse(status_code=400, content={"ok": False, "error": "图像解码失败"})

    ext = re.sub(r"[^a-z0-9]", "", str(body.get("ext") or "png").lower()) or "png"
    if ext not in _IMAGE_EXT_ALLOWED:     # 只允许图片扩展名，禁 pt/json 等（防作为模型权重被 torch.load）
        ext = "png"
    folder = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("folder") or ""))
    dest_dir = config.IMAGE_DIR / folder / emotion if folder else config.IMAGE_DIR / emotion
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 精确到秒命名；同一秒内重复时追加序号避免覆盖
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = dest_dir / f"{stamp}.{ext}"
    n = 1
    while path.exists():
        path = dest_dir / f"{stamp}_{n}.{ext}"
        n += 1
    path.write_bytes(raw)

    if body.get("show"):
        _set_current_stimulus(path, emotion)

    return {"ok": True, "path": str(path.relative_to(config.ROOT)), "filename": path.name}


_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@app.get("/api/stimulus/images")
async def stimulus_images(emotion: str = "", limit: int = 1000):
    """列出 image/ 下已保存的历史刺激图（按修改时间倒序，最新在前）。

    每项：{"emotion", "filename", "path", "url", "mtime"}。
    url 指向 /api/stimulus/files/<相对路径> 静态服务。
    """
    emo_filter = emotion.strip().lower()

    def _scan():
        # 全量 rglob + stat 可能很慢；放线程池不阻塞事件循环。
        items = []
        for p in config.IMAGE_DIR.rglob("*"):
            try:
                if not p.is_file() or p.suffix.lower() not in _IMAGE_EXT:
                    continue
                emo = p.parent.name.lower()
                if emo not in config.TRAIN_CLASSES:
                    continue
                if emo_filter and emo != emo_filter:
                    continue
                rel = p.relative_to(config.IMAGE_DIR).as_posix()
                items.append({
                    "emotion": emo,
                    "filename": p.name,
                    "path": rel,
                    "url": f"/api/stimulus/files/{rel}",
                    "mtime": p.stat().st_mtime,
                })
            except OSError:      # 遍历中文件被并发删除等 → 跳过
                continue
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return items

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(executor, _scan)
    return {"images": items[:max(1, limit)], "total": len(items)}


@app.delete("/api/stimulus/images")
async def stimulus_images_delete(path: str = "", emotion: str = ""):
    """删除历史刺激图：传 path 删单张；传 emotion 删该情感下全部。"""
    root = config.IMAGE_DIR.resolve()
    if path:
        target = (config.IMAGE_DIR / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return JSONResponse(status_code=400, content={"ok": False, "error": "非法路径"})
        try:
            target.unlink()
        except FileNotFoundError:      # 并发删除竞争 → 视为已删
            pass
        return {"ok": True, "deleted": 1}

    emo = emotion.strip().lower()
    if emo and emo in config.TRAIN_CLASSES:
        deleted = 0
        for p in config.IMAGE_DIR.rglob("*"):
            if p.is_file() and p.parent.name.lower() == emo and p.suffix.lower() in _IMAGE_EXT:
                try:
                    p.unlink()
                    deleted += 1
                except OSError:        # 并发删除竞争 → 跳过
                    continue
        return {"ok": True, "deleted": deleted}

    return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 path 或 emotion"})


# 静态服务已保存的刺激图（历史查看用）
app.mount(
    "/api/stimulus/files",
    StaticFiles(directory=str(config.IMAGE_DIR)),
    name="stimulus_files",
)


# ── 当前要在 VR 头显里显示的刺激图（webside 生成后即时推送）─────────────
# webside「VR 刺激图」面板生成并存图后，用 save 的 show=true 或 /api/stimulus/show
# 把某张图标记为「当前」；Quest Pro 上的 Unity 轮询 /api/stimulus/current，
# version 变大时下载该图贴到全景天空盒。
_current_stimulus: dict = {"version": 0, "path": None, "emotion": None, "url": None, "ts": 0}


def _set_current_stimulus(path, emotion: str) -> None:
    """把一张（已在 IMAGE_DIR 下的）图设为「当前」，version 自增。"""
    rel = path.relative_to(config.IMAGE_DIR).as_posix()
    _current_stimulus["version"] += 1
    _current_stimulus["path"] = rel
    _current_stimulus["emotion"] = emotion
    _current_stimulus["url"] = f"/api/stimulus/files/{rel}"
    _current_stimulus["ts"] = int(time.time() * 1000)


# 下一张图的生成进度：webside 闭环把 ComfyUI 的采样进度打到这里，
# 头显随 /api/stimulus/current 一起取回，显示「下一张生成中 x%」。
_stimulus_progress: dict = {"running": False, "current": 0, "max": 0, "step": 0, "note": "", "ts": 0}


# ── 刺激生成的共享控制状态（网页与 VR 头显都可读改）─────────────────────
# 生成本身跑在浏览器里，这里只持有「要不要跑 + 用什么参数」的意图；
# 头显按开始 → running=True → 网页轮询到后启动闭环。详见 stimulus_control 模块。
@app.get("/api/stimulus/options")
async def stimulus_options():
    """情绪 / 场景四要素 / 调制模式的选项表（zh+ja+en，两端共用一份真源）。"""
    return stimulus_control.options()


@app.get("/api/stimulus/control")
async def stimulus_control_get(client: str | None = None):
    """当前控制状态。头显带 client=vr，顺便当作在线心跳记下来。"""
    if client == "vr":
        stimulus_control.mark_seen()
    return stimulus_control.get_control()


@app.post("/api/stimulus/control")
async def stimulus_control_post(body: dict = Body(default=None)):
    """局部更新控制状态：只传要改的字段，非法值忽略。source 传 "web" 或 "vr"。"""
    body = body or {}
    if str(body.get("source") or "").lower() == "vr":
        stimulus_control.mark_seen()
    return stimulus_control.update_control(body)


@app.get("/api/stimulus/current")
async def stimulus_current():
    """Unity 头显轮询：返回当前应显示的刺激图 {version,url,path,emotion,ts} + progress + lang。
    version 从 0 开始，>0 表示有图；头显记录 version，变大时下载 url 贴天空盒。

    lang 顺带带上：自评面板每 0.5s 就在轮询这个接口，跟着它切语言不必再多发一次请求。"""
    return {**_current_stimulus, "progress": _stimulus_progress,
            "lang": stimulus_control.get_control()["lang"]}


@app.post("/api/stimulus/progress")
async def stimulus_progress_set(body: dict = Body(default=None)):
    """webside 闭环上报下一张图的生成进度 {running,current,max,step,note}。"""
    body = body or {}
    _stimulus_progress.update({
        "running": bool(body.get("running")),
        "current": int(body.get("current") or 0),
        "max": int(body.get("max") or 0),
        "step": int(body.get("step") or 0),
        "note": str(body.get("note") or "")[:120],
        "ts": int(time.time() * 1000),
    })
    return {"ok": True}


@app.get("/api/stimulus/progress")
async def stimulus_progress_get():
    return _stimulus_progress


@app.post("/api/stimulus/show")
async def stimulus_show(body: dict = Body(default=None)):
    """把一张已保存的刺激图标记为「当前」推给头显。
    请求体 {"path": "image/<emotion>/xxx.png" 或 "<emotion>/xxx.png"}。"""
    body = body or {}
    rel = str(body.get("path") or "").strip().replace("\\", "/")
    if not rel:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 path"})
    if rel.startswith("image/"):
        rel = rel[len("image/"):]
    target = (config.IMAGE_DIR / rel).resolve()
    if not target.is_relative_to(config.IMAGE_DIR.resolve()) or not target.is_file():
        return JSONResponse(status_code=400, content={"ok": False, "error": "非法路径"})
    _set_current_stimulus(target, target.parent.name.lower())
    return {"ok": True, "version": _current_stimulus["version"], "url": _current_stimulus["url"]}


# ── 头显在线状态（外网可用的唯一途径：应用自己的心跳）──────────────────
# 头显和笔记本都会被带到外网，服务器主动连不进它们的 NAT，只能靠头显应用自己的心跳。
@app.get("/api/headset/presence")
async def headset_presence():
    """头显应用在不在线，以及它该连的服务器地址（打包进 APK 的那个）。"""
    return {**stimulus_control.presence(), "base_url": config.PUBLIC_BASE_URL}


# ── 头显用户视角回传（WebRTC 点对点：头显 → 浏览器，画面不过后端）──────
# 后端在这里只转交 SDP，像素一个字节都不经手。握手流程见 rtc_signal 模块。
@app.get("/api/headset/rtc/config")
async def rtc_config():
    """两端共用的 ICE 服务器表（conf.ini [webrtc] 实时读）。"""
    return {"iceServers": rtc_signal.ice_servers()}


@app.get("/api/headset/rtc/watch")
async def rtc_watch():
    """头显轮询：网页那边有人在看吗？没人看就不建连接、不开编码。"""
    return rtc_signal.watching()


@app.post("/api/headset/rtc/offer")
async def rtc_offer_put(body: dict = Body(default=None)):
    """头显发布 SDP offer，开启新会话。"""
    sdp = str((body or {}).get("sdp") or "")
    if not sdp:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 sdp"})
    return rtc_signal.put_offer(sdp)


@app.get("/api/headset/rtc/offer")
async def rtc_offer_get():
    """网页取 offer（并登记「有人在看」）；暂无 offer 时返回 204。"""
    offer = rtc_signal.take_offer()
    if not offer:
        return Response(status_code=204)
    return offer


@app.post("/api/headset/rtc/answer")
async def rtc_answer_put(body: dict = Body(default=None)):
    """网页回 SDP answer。"""
    body = body or {}
    sdp = str(body.get("sdp") or "")
    if not sdp:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 sdp"})
    try:
        session = int(body.get("session"))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 session"})
    return rtc_signal.put_answer(session, sdp)


@app.get("/api/headset/rtc/answer")
async def rtc_answer_get(session: int):
    """头显取 answer；网页还没回时返回 204。"""
    sdp = rtc_signal.take_answer(session)
    if not sdp:
        return Response(status_code=204)
    return {"session": session, "sdp": sdp}


@app.post("/api/headset/rtc/bye")
async def rtc_bye():
    """网页离开页面：立刻作废会话，头显下次轮询即停止编码。"""
    return rtc_signal.bye()


# ── Quest Pro 头显 FEA（70 维混合形状）→ 情绪 ─────────────────────
# 机内摄像头不开放原始图像，头显侧（Unity/Meta XR Core SDK）把 70 维 FEA
# 推到这里；后端分类后，前端「微情感生成」页轮询 /api/fea/latest 取用。
_latest_fea: dict = {"data": None}
# 「情感偏好生成」页正在采集的活动会话；非空时每帧 FEA 同时写入 PG 时间轴。
_affect_active: dict = {
    "session_id": None, "subject_id": None,
    # 本次会话真正落库的 FEA 帧数与最后一帧时间，供网页实时显示采集是否在动
    "fea_rows": 0, "last_fea_ms": None,
}


def _emotions_en(result: dict) -> dict:
    """把 classify_fea 的中文情绪字典转成英文键（供 PG 存储/相似度检索）。"""
    zh = result.get("emotions") or {}
    return {en: zh.get(labels.to_zh(en), 0.0) for en in config.TRAIN_CLASSES}


@app.post("/api/fea")
async def fea_ingest(body: dict = Body(default=None)):
    """接收 Quest Pro 的一帧 FEA：{"blendshapes":[70], "timestamp_ms"?:int}（旧版 63 维补 0）。

    若「情感偏好生成」页有活动采集会话，则该帧（70 维 blendshape + 情绪 + 时间戳）
    同时写入 Postgres 时间轴，供后续训练情绪预测模型。
    """
    body = body or {}
    bs = body.get("blendshapes")
    try:
        result = classify_fea(bs)
    except (ValueError, TypeError) as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    ts = int(body.get("timestamp_ms") or time.time() * 1000)
    result["success"] = True
    result["timestamp_ms"] = ts
    _latest_fea["data"] = result

    sid = _affect_active["session_id"]
    if sid:
        row = {
            # 存归一化后的 70 维：旧版 APK 发的 63 维在此补 0。
            # 库里必须只有一种维度，否则切窗训练时长度不齐。
            "ts_ms": ts,
            "blendshapes": normalize_blendshapes(bs),
            "emotions": _emotions_en(result),
            "dominant": result.get("dominant_en"),
            "source": "quest_fea",
        }
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(executor, lambda: csv_store.insert_fea(sid, [row]))
            # 落库计数：网页据此显示「本次采集已入库 N 帧」。没有它就会重演
            # 「37 个会话、0 帧 FEA，一周后查库才发现」——采集期间必须当场看得见。
            _affect_active["fea_rows"] += 1
            _affect_active["last_fea_ms"] = ts
        except RuntimeError as e:
            logger.warning("FEA 写入 CSV 失败: %s", e)
    return result


def _capture_state() -> dict:
    """当前采集会话的落库情况。挂在每秒都被轮询的 /api/fea/latest 上，不额外加请求。"""
    return {
        "session_id": _affect_active["session_id"],
        "rows": _affect_active["fea_rows"],
        "last_ms": _affect_active["last_fea_ms"],
    }


@app.get("/api/fea/latest")
async def fea_latest():
    """前端取最近一帧 FEA 情绪；无数据时 success=False。附带本次采集的入库进度。"""
    data = _latest_fea["data"]
    if not data:
        return {"success": False, "faces": [], "capture": _capture_state()}
    # 结构对齐图像识别路径：faces:[{dominant_en,dominant,emotions}]
    return {"success": True, "timestamp_ms": data["timestamp_ms"], "faces": [
        {"dominant_en": data["dominant_en"], "dominant": data["dominant"], "emotions": data["emotions"]}
    ], "capture": _capture_state()}


# ── FEA 时序闭环采集：会话记录（头显 Unity 经 LAN 推四路时序）─────────
# 任务书 Docs/任务书_VR微情感时序采集与情绪预测.md 的 M-B。
@app.post("/api/session/start")
async def session_start(body: dict = Body(default=None)):
    """建立采集会话：{"subject_id", "session_id"?, "fea_rate_hz"?, "sync_offset_ms"?}。"""
    # 会话读写落盘，放线程池避免阻塞事件循环。
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, session_store.start_session, body or {})


@app.post("/api/session/ingest")
async def session_ingest(body: dict = Body(default=None)):
    """追加一批四路事件：{"session_id", "fea":[...], "gaze":[...], "stimulus":[...], "selfreport":[...]}。"""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, session_store.ingest, body or {})
    except KeyError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "会话不存在或未 start"})


@app.post("/api/session/stop")
async def session_stop(body: dict = Body(default=None)):
    """收尾并写 meta.json：{"session_id", "sync_offset_ms"?}。"""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, session_store.stop_session, body or {})
    except KeyError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "会话不存在或已 stop"})


@app.get("/api/session/list")
async def session_list():
    """列出已落盘会话（含进行中的活动会话）。"""
    return session_store.list_sessions()


# ── 系统设置（根目录 conf.ini 持久化，供「系统设置」页读写）─────────────
@app.get("/api/settings")
async def settings_get():
    """返回全部系统设置 {section: {key: value}}（供设置页回填表单）。"""
    return settings_store.all_settings()


@app.post("/api/settings")
async def settings_update(body: dict = Body(default=None)):
    """按 section 更新设置并落盘 conf.ini。请求体 {section: {key: value, ...}, ...}。"""
    body = body or {}
    try:
        for section, values in body.items():
            if isinstance(values, dict):
                settings_store.update_section(section, values)
    except KeyError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"未知设置分组 {e}"})
    except (ValueError, TypeError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"设置值非法: {e}"})
    return {"ok": True, "settings": settings_store.all_settings()}


@app.post("/api/settings/deepseek/test")
async def settings_deepseek_test():
    """用当前保存的 DeepSeek 设置做一次最小连通性测试。"""
    if not deepseek.is_configured():
        return JSONResponse(status_code=503, content={"ok": False, "error": "DEEPSEEK_API_KEY 未配置"})
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(executor, deepseek.test_connection)
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    return {"ok": True, **result}


# ── DeepSeek：情感变化 → 图像生成提示词 ─────────────────────────────
# 前端「微情感生成」页把最近的情感变化轨迹推来，DeepSeek 据此生成图像提示词，
# 再喂给 ComfyUI 生成图（见 use_llm/deepseek.py）。
@app.get("/api/prompt/status")
async def prompt_status():
    """前端探测该功能是否可用（是否配置了 DEEPSEEK_API_KEY）。"""
    return {"configured": deepseek.is_configured(), "model": config.DEEPSEEK_MODEL}


@app.post("/api/prompt/generate")
async def prompt_generate(body: dict = Body(default=None)):
    """输入情感变化轨迹 → 输出 {positive, negative, scene, reasoning}。

    请求体：{"trajectory":[{"dom","ago_s","probs"}...], "current":{"dominant_en","probs"}, "locale"?}。
    """
    body = body or {}
    if not deepseek.is_configured():
        return JSONResponse(status_code=503, content={"ok": False, "error": "DEEPSEEK_API_KEY 未配置"})
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            executor,
            lambda: deepseek.generate_image_prompt(
                body.get("trajectory") or [],
                body.get("current") or {},
                body.get("locale") or "zh",
            ),
        )
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    return {"ok": True, **result}


@app.post("/api/prompt/scene")
async def prompt_scene(body: dict = Body(default=None)):
    """场景驱动的动态提示词（刺激页闭环用）。

    请求体：{"emotion":str, "scene":str, "directive":str, "locale"?}。
    directive 由前端按调制模式（递进/目标带/剂量阶梯/随机）+ 目标情绪实时强度算好。
    """
    body = body or {}
    if not deepseek.is_configured():
        return JSONResponse(status_code=503, content={"ok": False, "error": "DEEPSEEK_API_KEY 未配置"})
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            executor,
            lambda: deepseek.generate_scene_prompt(
                body.get("emotion") or "",
                body.get("scene") or "",
                body.get("directive") or "",
                body.get("locale") or "zh",
            ),
        )
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    return {"ok": True, **result}


# ── 情感偏好生成闭环：采集会话 + FEA 时间轴 + 生成图偏好（Postgres/pgvector）──
# 前端「情感偏好生成」页：点开始→建会话并开始采集 FEA 时间轴；每生成一张图，
# 测量随后的情绪反应判定是否「喜欢」，连同生成时的情绪情境向量写入 PG；据此
# 相似度检索用户在相似情绪下喜欢过的图，闭环推动后续生成。
@app.get("/api/affect/status")
async def affect_status():
    """前端探测：PG 是否配置、DeepSeek 是否配置、当前是否有活动采集会话。"""
    return {
        "pg_configured": csv_store.is_configured(),
        "deepseek_configured": deepseek.is_configured(),
        "active_session": _affect_active["session_id"],
        "reaction_window_s": config.AFFECT_REACTION_WINDOW_S,
        "like_threshold": config.AFFECT_LIKE_THRESHOLD,
        "cooldown_s": config.AFFECT_COOLDOWN_S,
    }


def _require_pg():
    if not csv_store.is_configured():
        return JSONResponse(status_code=503, content={"ok": False, "error": "Postgres DSN 未配置"})
    return None


@app.post("/api/affect/session/start")
async def affect_session_start(body: dict = Body(default=None)):
    """建立采集会话并置为活动。请求体 {"subject_id"?, "meta"?}。"""
    if (err := _require_pg()) is not None:
        return err
    body = body or {}
    loop = asyncio.get_event_loop()
    try:
        r = await loop.run_in_executor(
            executor, lambda: csv_store.start_session(str(body.get("subject_id") or "anon"), body.get("meta")))
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    _affect_active["session_id"] = r["session_id"]
    _affect_active["subject_id"] = str(body.get("subject_id") or "anon")
    _affect_active["fea_rows"] = 0          # 新会话重新计数
    _affect_active["last_fea_ms"] = None
    return r


@app.post("/api/affect/session/stop")
async def affect_session_stop(body: dict = Body(default=None)):
    """收尾采集会话，清空活动会话。请求体 {"session_id"?}（缺省用当前活动会话）。"""
    if (err := _require_pg()) is not None:
        return err
    sid = (body or {}).get("session_id") or _affect_active["session_id"]
    if not sid:
        return JSONResponse(status_code=400, content={"ok": False, "error": "无活动会话"})
    loop = asyncio.get_event_loop()
    try:
        r = await loop.run_in_executor(executor, lambda: csv_store.stop_session(sid))
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    if _affect_active["session_id"] == sid:
        _affect_active["session_id"] = None
        _affect_active["subject_id"] = None
    return r


@app.post("/api/affect/selfreport")
async def affect_selfreport(body: dict = Body(default=None)):
    """写受试者自评（7 类 + SAM）到 PG 时间轴。

    请求体 {"session_id"?, "rows":[{ts_ms, image_id?, label7, valence?, arousal?, source?}]}
    也接受单条形式 {"label7": ..., ...}（自动包成一行），方便头显直接上报。

    这是 PG 链路唯一独立于 FEA 的真值来源：没有它，采到的时间轴只能配与 X 同源的
    规则标签，无法用于训练情绪预测模型。
    """
    if (err := _require_pg()) is not None:
        return err
    body = body or {}
    sid = body.get("session_id") or _affect_active["session_id"]
    if not sid:
        return JSONResponse(status_code=400, content={"ok": False, "error": "无活动会话"})
    rows = body.get("rows")
    if not rows and body.get("label7"):
        rows = [body]                     # 单条上报
    if not rows:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 rows 或 label7"})
    loop = asyncio.get_event_loop()
    try:
        n = await loop.run_in_executor(executor, lambda: csv_store.insert_selfreport(sid, rows))
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    if n == 0:
        return JSONResponse(status_code=400, content={
            "ok": False, "error": f"没有合法自评写入（label7 须为 {'/'.join(config.TRAIN_CLASSES)} 之一）"})
    return {"ok": True, "written": n, "session_id": sid}


@app.post("/api/affect/image")
async def affect_image(body: dict = Body(default=None)):
    """写一条生成图偏好记录（含情绪情境向量、提示词、反应、是否喜欢）。"""
    if (err := _require_pg()) is not None:
        return err
    body = body or {}
    if _affect_active["session_id"] and not body.get("session_id"):
        body["session_id"] = _affect_active["session_id"]
    loop = asyncio.get_event_loop()
    try:
        r = await loop.run_in_executor(executor, lambda: csv_store.insert_image(body))
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    return r


@app.post("/api/affect/recommend")
async def affect_recommend(body: dict = Body(default=None)):
    """按当前情绪情境检索用户喜欢过的相似图。请求体 {"emotions":{en:值}, "k"?, "subject_id"?}。"""
    if (err := _require_pg()) is not None:
        return err
    body = body or {}
    try:
        k = int(body.get("k") or 5)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "error": "k 非法"})
    loop = asyncio.get_event_loop()
    try:
        r = await loop.run_in_executor(
            executor,
            # epsilon 可选：留出 ε-探索名额，便于把「偏好检索 vs 随机」做成 A/B
            lambda: csv_store.recommend(body.get("emotions"), k, body.get("subject_id"),
                                       body.get("epsilon")),
        )
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    return {"ok": True, **r}


# ── 受试者种子档案：受试者自填「什么场景让我产生什么情绪」→ 预测 → 闭环实测修正 ──
async def _seed_call(fn):
    if (err := _require_pg()) is not None:
        return err
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(executor, fn)
    except (ValueError, TypeError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    except LookupError as e:
        return JSONResponse(status_code=404, content={"ok": False, "error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})


@app.get("/api/affect/seeds")
async def affect_seeds_list(subject_id: str = "anon"):
    """某受试者的种子档案，每条带 pred（7 类预测分布 = 自填先验 + 实测反应）。"""
    return await _seed_call(lambda: csv_store.list_seeds(subject_id))


@app.post("/api/affect/seeds")
async def affect_seeds_add(body: dict = Body(default=None)):
    """新增种子。请求体 {"subject_id", "scene", "expected"?, "expected_level"? (1–5)}。"""
    body = body or {}
    return await _seed_call(lambda: csv_store.add_seed(
        str(body.get("subject_id") or "anon"), body.get("scene"),
        body.get("expected"), body.get("expected_level")))


@app.delete("/api/affect/seeds/{seed_id}")
async def affect_seeds_delete(seed_id: int):
    return await _seed_call(lambda: csv_store.delete_seed(seed_id))


@app.post("/api/affect/seeds/{seed_id}/observe")
async def affect_seeds_observe(seed_id: int, body: dict = Body(default=None)):
    """闭环在测量窗结束后上报一次实测反应。请求体 {"emotions": {en: 0–1}}。"""
    return await _seed_call(lambda: csv_store.observe_seed(seed_id, (body or {}).get("emotions")))


@app.get("/api/affect/sessions")
async def affect_sessions():
    if (err := _require_pg()) is not None:
        return err
    loop = asyncio.get_event_loop()
    try:
        r = await loop.run_in_executor(executor, csv_store.list_sessions)
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})
    return r
