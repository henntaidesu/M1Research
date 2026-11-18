"""后台批量生成：每个候选 = 一张全景（图片线）+ 由它生成的视频（视频线），成对落库。

为什么放后端而不是浏览器：一批几十对、每对几分钟，浏览器标签页一关或电脑睡眠就断了；
后端线程不依赖网页开着。网页只负责发起、看进度、停止。

生成顺序按条件交错（HVHA#1, HVLA#1, …, HVHA#2, …），中途停止时各条件数量也是均衡的。
"""
import logging
import random
import threading
import time

from . import comfy, conditions, library, postprocess, workflows

logger = logging.getLogger(__name__)

DEFAULTS = {
    "conditions": list(conditions.CONDITION_KEYS),
    "per_condition": 4,
    "seed": -1,                 # -1 = 随机起点；之后每个候选 +1，全部写进 meta 可复现
    "pano": {"width": 2048, "steps": 20, "cfg": 3.5},
    "video": {"enabled": True, "gen_width": 1280, "gen_height": 640, "pad": 160,
              "frames": 121, "fps": 24, "steps": 20, "cfg": 5.0, "shift": 8.0, "loop_k": 12},
    "output": {"width": 2048, "duration_s": 30, "crf": 20},
}

_LOCK = threading.Lock()
_stop = threading.Event()
_thread = None
_state = {"running": False, "total": 0, "done": 0, "failed": 0, "current": None,
          "log": [], "started_ms": 0, "finished_ms": 0, "avg_pair_s": None, "eta_s": None,
          "params": None, "error": ""}


def _log(msg: str) -> None:
    logger.info("[stimlib-gen] %s", msg)
    with _LOCK:
        _state["log"].append({"ts": int(time.time() * 1000), "msg": msg})
        del _state["log"][:-80]


def _set(**kw) -> None:
    with _LOCK:
        _state.update(kw)


def status() -> dict:
    with _LOCK:
        s = dict(_state)
        s["log"] = list(_state["log"])
        s["current"] = dict(_state["current"]) if _state["current"] else None
    return s


def _merge(params: dict) -> dict:
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in (params or {}).items():
        if k in p and isinstance(p[k], dict) and isinstance(v, dict):
            p[k].update({kk: vv for kk, vv in v.items() if kk in p[k]})
        elif k in p:
            p[k] = v
    p["conditions"] = [c for c in p["conditions"] if c in conditions.CONDITION_KEYS]
    if not p["conditions"]:
        raise ValueError("至少选择一个条件")
    p["per_condition"] = max(1, min(50, int(p["per_condition"])))
    v, o = p["video"], p["output"]
    # Wan 的宽高步长 32、帧数须为 4k+1；输出宽须为 16 的倍数（H.264 宏块）且高 = 宽/2
    v["gen_width"] = max(256, int(v["gen_width"]) // 32 * 32)
    v["gen_height"] = max(128, int(v["gen_height"]) // 32 * 32)
    v["pad"] = max(0, int(v["pad"]) // 32 * 32)
    v["frames"] = max(17, (int(v["frames"]) - 1) // 4 * 4 + 1)
    o["width"] = max(512, int(o["width"]) // 32 * 32)
    o["duration_s"] = max(2.0, min(180.0, float(o["duration_s"])))
    p["pano"]["width"] = max(512, int(p["pano"]["width"]) // 32 * 32)
    return p


def start(params: dict) -> dict:
    global _thread
    p = _merge(params)
    with _LOCK:
        if _state["running"]:
            raise RuntimeError("已有生成任务在运行")
    if not comfy.online():
        raise RuntimeError("ComfyUI 未连接（检查 start_comfyUI.bat 是否已启动）")
    missing = comfy.missing_models(workflows.pano_required())
    if p["video"]["enabled"]:
        missing += comfy.missing_models(workflows.video_required())
    if missing:
        raise RuntimeError("ComfyUI 缺少模型文件：" + ", ".join(sorted(set(missing))))

    total = len(p["conditions"]) * p["per_condition"]
    _stop.clear()
    _set(running=True, total=total, done=0, failed=0, current=None, log=[], error="",
         started_ms=int(time.time() * 1000), finished_ms=0, avg_pair_s=None, eta_s=None, params=p)
    _thread = threading.Thread(target=_run, args=(p,), name="stimlib-gen", daemon=True)
    _thread.start()
    return status()


def stop() -> dict:
    _stop.set()
    comfy.interrupt()
    _log("收到停止请求，当前这一对结束后退出")
    return status()


def _run(p: dict) -> None:
    base_seed = p["seed"] if int(p["seed"]) >= 0 else random.randint(0, 2 ** 31 - 1)
    plan = []
    for i in range(p["per_condition"]):
        for c in p["conditions"]:
            plan.append((c, i))
    durations = []
    try:
        for n, (cond, i) in enumerate(plan):
            if _stop.is_set():
                break
            scenes = conditions.SCENES[cond]
            scene_idx = i % len(scenes)
            seed = base_seed + n
            pair_id = library.new_pair_id(cond, n)
            t0 = time.time()
            _set(current={"pair_id": pair_id, "condition": cond, "scene": scenes[scene_idx]["zh"],
                          "step": "pano", "index": n + 1, "started_ms": int(t0 * 1000)})
            try:
                _make_pair(pair_id, cond, scene_idx, seed, p)
                with _LOCK:
                    _state["done"] += 1
                _log(f"完成 {pair_id}（{scenes[scene_idx]['zh']}，{time.time() - t0:.0f}s）")
            except comfy.ComfyError as e:
                if _stop.is_set():
                    _log(f"{pair_id} 已取消")
                    break
                with _LOCK:
                    _state["failed"] += 1
                _log(f"失败 {pair_id}：{e}")
            except Exception as e:      # noqa: BLE001 —— 单对失败不应中断整批
                logger.exception("stimlib pair failed")
                with _LOCK:
                    _state["failed"] += 1
                _log(f"失败 {pair_id}：{type(e).__name__}: {e}")
            durations.append(time.time() - t0)
            avg = sum(durations) / len(durations)
            _set(avg_pair_s=round(avg, 1), eta_s=round(avg * (len(plan) - n - 1)))
    finally:
        _set(running=False, current=None, finished_ms=int(time.time() * 1000), eta_s=0)
        _log("任务结束" + ("（已停止）" if _stop.is_set() else ""))


def _step(name: str) -> None:
    with _LOCK:
        if _state["current"]:
            _state["current"]["step"] = name


def _make_pair(pair_id: str, cond: str, scene_idx: int, seed: int, p: dict) -> None:
    pp, vp, op = p["pano"], p["video"], p["output"]
    out_w, out_h = op["width"], op["width"] // 2
    d = library.pair_dir(pair_id)
    d.mkdir(parents=True, exist_ok=True)

    pano_pos = conditions.pano_prompt(cond, scene_idx)
    video_pos = conditions.video_prompt(cond, scene_idx)
    meta = {
        "pair_id": pair_id, "created_ms": int(time.time() * 1000), "condition": cond,
        "scene_idx": scene_idx, "scene": conditions.SCENES[cond][scene_idx]["zh"],
        "prompts": {"pano": pano_pos, "pano_negative": conditions.PANO_NEGATIVE,
                    "video": video_pos, "video_negative": conditions.VIDEO_NEGATIVE},
        "seeds": {"pano": seed, "video": seed},
        "models": {"pano": dict(workflows.PANO_MODELS), "video": dict(workflows.VIDEO_MODELS)},
        "params": p, "files": {}, "sha": {}, "features": {}, "status": "pano",
        "matched": False, "qc": "pending", "qc_note": "",
    }
    library.write_meta(pair_id, meta)

    # ① 全景
    wf = workflows.pano_workflow(pano_pos, conditions.PANO_NEGATIVE, seed,
                                 pp["width"], pp["width"] // 2, pp["steps"], pp["cfg"])
    pid = comfy.queue(wf)
    outs = comfy.output_images(comfy.wait(pid, timeout_s=900, should_stop=_stop.is_set))
    if not outs:
        raise comfy.ComfyError("全景没有输出")
    pano = postprocess.decode_png(comfy.fetch(*outs[0]))
    (d / "pano.png").write_bytes(postprocess.encode_png(pano))
    meta["files"]["pano"] = "pano.png"

    still = None
    if vp["enabled"]:
        # ② 视频：左右环绕补边 → I2V → 去补边并淡化接缝 → 交叉淡化循环 → 放大编码
        _step("video")
        W, H, pad = vp["gen_width"], vp["gen_height"], vp["pad"]
        src = postprocess.wrap_pad(pano, W, H, pad)
        name = comfy.upload_image(postprocess.encode_png(src), f"stimlib_{pair_id}.png")
        wf = workflows.video_workflow(name, video_pos, conditions.VIDEO_NEGATIVE, seed, W + pad, H,
                                      vp["frames"], vp["steps"], vp["cfg"], vp["shift"])
        pid = comfy.queue(wf)
        outs = comfy.output_images(comfy.wait(pid, timeout_s=3600, should_stop=_stop.is_set))
        if len(outs) < 8:
            raise comfy.ComfyError(f"视频帧数异常：{len(outs)}")
        _step("encode")
        frames = [postprocess.unwrap(postprocess.decode_png(comfy.fetch(*o)), W, pad) for o in outs]
        loop = postprocess.crossfade_loop(frames, vp["loop_k"])
        postprocess.encode_mp4(loop, d / "video.mp4", out_w, out_h, vp["fps"], op["duration_s"], op["crf"])
        meta["files"]["video"] = "video.mp4"
        meta["sha"]["video"] = postprocess.file_md5(d / "video.mp4")
        meta["features"]["video"] = postprocess.video_stats(loop)
        meta["video_info"] = {"gen_frames": len(frames), "loop_frames": len(loop), "fps": vp["fps"],
                              "duration_s": op["duration_s"], "width": out_w, "height": out_h}
        # 图片线 = 同一处理链的第 0 帧（与视频同分辨率、同放大、同接缝处理）
        still = postprocess.resize(frames[0], out_w, out_h)
        meta["matched"] = True
    if still is None:
        still = postprocess.resize(pano, out_w, out_h)

    _step("finalize")
    postprocess.save_jpg(still, d / "still.jpg")
    postprocess.save_jpg(postprocess.resize(still, 512, 256), d / "thumb.jpg", quality=85)
    meta["files"]["still"] = "still.jpg"
    meta["files"]["thumb"] = "thumb.jpg"
    meta["sha"]["still"] = postprocess.file_md5(d / "still.jpg")
    meta["features"]["image"] = postprocess.still_stats(still)
    meta["status"] = "ok" if meta["files"].get("video") else "image_only"
    library.write_meta(pair_id, meta)
