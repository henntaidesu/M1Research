"""外部视频导入刺激清单（刺激重建方案 B.4 第 5 步 / 第 8 步）。

把一段外部视频（360° 等距柱状视频，或正前方平面播放的模仿片段）转成头显能直接播的素材，
写进 catalog/assets/，并生成 catalog/<id>.json 清单条目。导入后的条目一律 enabled=false、qc=pending：
要在网页「④ 实验流程 → 其他刺激」里人工质检、启用后才进实验。

处理链（全部用 imageio-ffmpeg 自带的 ffmpeg，没有单独的 ffprobe，探测靠解析 `ffmpeg -i` 的输出）：
    探测          分辨率 / 帧率 / 时长 / 有无音轨
    裁剪          --start / --duration（可选），在两遍响度测量和转码里用同一段
    skybox_video  等距柱状 3840×1920（必须 2:1，否则拒绝，--force 时强行拉伸）
    mimicry       最宽 1920，等比缩放，不放大
    视频编码      H.264 high、30 fps、yuv420p、CRF 20、faststart（Quest 硬解兼容，可边下边播）
    音频          两遍 loudnorm 到 −23 LUFS（I=-23:TP=-2:LRA=11），AAC 160k、48 kHz；
                  输入没有音轨、或音轨整段静音时，输出不带音轨（audio=false）
    低层特征      从输出视频按 2 fps 抽帧：亮度 / 对比度 / 色彩度 / 运动能量。
                  等距柱状按纬度 cos 加权（复用 postprocess.frame_stats）；平面片段不加权。
                  写进 params.lowlevel，供亮度匹配与条件间混淆检查用。

许可证（license）是必填项：Li 2017 只能实验室内部使用、CEAP-360VR 是 CC BY-NC 4.0，
来源与许可证不随条目记下来，数据集发布时就分不清哪些素材能分发。
"""
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from .. import config
from ..safe_path import is_safe_id
from . import catalog, conditions, postprocess

INGEST_KINDS = ("skybox_video", "mimicry")
EQUIRECT_W, EQUIRECT_H = 3840, 1920
MIMICRY_MAX_W = 1920
FPS = 30
CRF = 20
LOUDNORM = "I=-23:TP=-2:LRA=11"
AUDIO_BITRATE = "160k"
STATS_FPS = 2                 # 低层特征的抽帧率
STATS_W = 512                 # 抽帧缩到这个宽度再算（亮度类统计对分辨率不敏感）


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", *args], capture_output=True)
    r.stdout_text = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else r.stdout
    r.stderr_text = r.stderr.decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：\n" + "\n".join(r.stderr_text.strip().splitlines()[-12:]))
    return r


# ── 探测 ────────────────────────────────────────────────────────────
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID_RE = re.compile(r"Stream #\S+.*?: Video: (\w+).*?, (\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"([\d.]+)(k?) (?:fps|tbr)")


def probe(path) -> dict:
    """解析 `ffmpeg -i <file>` 的 stderr → {width, height, fps, duration_s, has_audio, vcodec}。"""
    r = _run(["-i", str(path)], check=False)          # 没有输出文件，返回码必然非 0
    txt = r.stderr_text
    vm = None
    for line in txt.splitlines():
        if "Video:" in line and "attached pic" not in line:   # 跳过封面图流
            vm = _VID_RE.search(line)
            if vm:
                fm = _FPS_RE.search(line)
                break
    if not vm:
        raise ValueError(f"{path}：找不到视频流（{txt.strip().splitlines()[-1] if txt.strip() else '无输出'}）")
    fps = 0.0
    if fm:
        fps = float(fm.group(1)) * (1000 if fm.group(2) else 1)
    dm = _DUR_RE.search(txt)
    dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)) if dm else 0.0
    has_audio = any(re.search(r"Stream #\S+.*?: Audio:", ln) for ln in txt.splitlines())
    return {"width": int(vm.group(2)), "height": int(vm.group(3)), "fps": fps, "duration_s": round(dur, 3),
            "has_audio": has_audio, "vcodec": vm.group(1)}


def _trim_args(start_s, duration_s) -> list:
    out = []
    if start_s:
        out += ["-ss", f"{float(start_s):.3f}"]
    if duration_s:
        out += ["-t", f"{float(duration_s):.3f}"]
    return out


# ── 音频：两遍 loudnorm ─────────────────────────────────────────────
def measure_loudness(path, start_s=None, duration_s=None) -> dict | None:
    """第一遍：测量输入的响度（loudnorm print_format=json）。整段静音时返回 None（不做响度归一）。"""
    r = _run([*_trim_args(start_s, None), "-i", str(path), *(["-t", f"{float(duration_s):.3f}"] if duration_s else []),
              "-vn", "-af", f"loudnorm={LOUDNORM}:print_format=json", "-f", "null", "-"])
    txt = r.stderr_text
    i = txt.rfind("{")
    j = txt.rfind("}")
    if i < 0 or j < i:
        raise RuntimeError("loudnorm 第一遍没有输出测量结果")
    m = json.loads(txt[i:j + 1])
    try:
        if not np.isfinite(float(m["input_i"])):
            return None
    except (TypeError, ValueError):
        return None
    return m


def _loudnorm_filter(m: dict) -> str:
    return (f"loudnorm={LOUDNORM}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']}:linear=true:print_format=json")


# ── 视频 ────────────────────────────────────────────────────────────
def video_filter(kind: str, info: dict, force: bool = False) -> str:
    """kind + 输入尺寸 → -vf。等距柱状必须 2:1（容差 1%），否则拒绝，除非 force。"""
    if kind == "skybox_video":
        ratio = info["width"] / max(1, info["height"])
        if abs(ratio - 2.0) > 0.02 and not force:
            raise ValueError(f"等距柱状视频必须是 2:1，输入是 {info['width']}×{info['height']}（{ratio:.3f}:1）。"
                             "上下 / 左右 3D 或 EAC 投影要先转成单目等距柱状；确认要强行拉伸时加 --force")
        scale = f"scale={EQUIRECT_W}:{EQUIRECT_H}:flags=lanczos"
    elif kind == "mimicry":
        scale = f"scale='trunc(min({MIMICRY_MAX_W},iw)/2)*2':-2:flags=lanczos"
    else:
        raise ValueError(f"只能导入 {' / '.join(INGEST_KINDS)}")
    return f"{scale},setsar=1,fps={FPS},format=yuv420p"


def transcode(src, dst, kind: str, info: dict, start_s=None, duration_s=None, force: bool = False,
              audio: bool = True) -> dict:
    """转码到 dst（.mp4）。返回 {audio, loudness_in, loudness_out}。"""
    vf = video_filter(kind, info, force)
    use_audio = bool(audio and info["has_audio"])
    meas = measure_loudness(src, start_s, duration_s) if use_audio else None
    silent = use_audio and meas is None
    if silent:                                         # 音轨整段静音：当作没有音轨，头显不必开声音
        use_audio = False
    args = ["-y", *_trim_args(start_s, None), "-i", str(src)]
    if duration_s:
        args += ["-t", f"{float(duration_s):.3f}"]
    args += ["-map", "0:v:0", "-vf", vf, "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
             "-crf", str(CRF), "-pix_fmt", "yuv420p", "-g", str(FPS * 2), "-r", str(FPS)]
    if use_audio:
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000", "-ac", "2"]
        if meas:
            args += ["-af", _loudnorm_filter(meas)]
    else:
        args += ["-an"]
    args += ["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(dst)]
    r = _run(args)
    out_ln = None
    if meas:
        txt = r.stderr_text
        i, j = txt.rfind("{"), txt.rfind("}")
        if 0 <= i < j:
            try:
                o = json.loads(txt[i:j + 1])
                out_ln = {"i": float(o["output_i"]), "tp": float(o["output_tp"]), "lra": float(o["output_lra"])}
            except (ValueError, KeyError):
                out_ln = None
    return {"audio": use_audio,
            "loudness_in": ({"i": float(meas["input_i"]), "tp": float(meas["input_tp"]),
                             "lra": float(meas["input_lra"])} if meas else None),
            "loudness_out": out_ln, "silent": silent}


# ── 低层特征 ────────────────────────────────────────────────────────
def _read_frames(path, width: int, height: int, fps: float = STATS_FPS) -> list:
    r = _run(["-i", str(path), "-vf", f"fps={fps},scale={width}:{height}:flags=area", "-f", "rawvideo",
              "-pix_fmt", "rgb24", "-"])
    buf = np.frombuffer(r.stdout, np.uint8)
    n = buf.size // (width * height * 3)
    return [buf[k * width * height * 3:(k + 1) * width * height * 3].reshape(height, width, 3) for k in range(n)]


def _flat_stats(rgb: np.ndarray) -> dict:
    """平面片段：不加纬度权重的亮度 / 对比度 / 色彩度（Hasler–Süsstrunk）。"""
    f = rgb.astype(np.float64)
    y = (0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]) / 255.0
    rg = f[..., 0] - f[..., 1]
    yb = 0.5 * (f[..., 0] + f[..., 1]) - f[..., 2]
    cf = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    return {"luminance": float(y.mean()), "contrast": float(y.std()), "colorfulness": float(cf)}


def lowlevel(path, kind: str, out_info: dict) -> dict:
    """输出视频的低层特征（按 STATS_FPS 抽帧）。运动能量 = 相邻抽样帧亮度绝对差均值，和生成线
    （逐帧计算）不在同一尺度，只用于导入素材之间比较。"""
    w = STATS_W
    h = w // 2 if kind == "skybox_video" else max(2, int(round(w * out_info["height"] / max(1, out_info["width"]) / 2)) * 2)
    frames = _read_frames(path, w, h)
    if not frames:
        return {}
    equi = kind == "skybox_video"
    stats = [postprocess.frame_stats(f) if equi else _flat_stats(f) for f in frames]
    out = {k: round(float(np.mean([s[k] for s in stats])), 5) for k in stats[0]}
    lum = [s["luminance"] for s in stats]
    out["luminance_sd"] = round(float(np.std(lum)), 5)        # 亮度随时间的起伏（闪烁 / 场景切换）
    ys = [(0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]).astype(np.float64) / 255.0 for f in frames]
    if len(ys) > 1:
        if equi:
            wl = postprocess._lat_weights(h)
            mot = [postprocess._wmean(np.abs(a - b), wl) for a, b in zip(ys[1:], ys[:-1])]
        else:
            mot = [float(np.abs(a - b).mean()) for a, b in zip(ys[1:], ys[:-1])]
        out["motion"] = round(float(np.mean(mot)), 5)
    else:
        out["motion"] = 0.0
    out["weighting"] = "cos_latitude" if equi else "none"
    out["sample_fps"] = STATS_FPS
    out["n_frames"] = len(frames)
    return out


# ── 入口 ────────────────────────────────────────────────────────────
def _norms(norm_valence=None, norm_arousal=None, norm_sd_v=None, norm_sd_a=None, norm_n=None, norm_source="") -> dict:
    def f(v):
        return None if v in (None, "") else float(v)
    return {"valence_mean": f(norm_valence), "arousal_mean": f(norm_arousal), "valence_sd": f(norm_sd_v),
            "arousal_sd": f(norm_sd_a), "n": int(float(norm_n)) if norm_n not in (None, "") else 0,
            "source": str(norm_source or "")}


def _default_id(src: Path, kind: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem).strip("_")[:48] or "clip"
    return ("vid_" if kind == "skybox_video" else "mim_") + stem


def ingest(src, *, kind: str, license: str, item_id: str = "", condition: str = "NEU", target_emotion: str = "",
           start_s=None, duration_s=None, rating: str = "", name: str = "", source: str = "", ethics_tier: int = 0,
           habituation_group: str = "", note: str = "", norms: dict | None = None, force: bool = False,
           no_audio: bool = False, overwrite: bool = False, catalog_dir=None, dry_run: bool = False) -> dict:
    """导入一个视频，返回写入的清单条目。catalog_dir 默认 catalog.CATALOG_DIR（测试时指向临时目录）。"""
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"找不到输入文件 {src}")
    if not str(license or "").strip():
        raise ValueError("必须给出 license（素材许可证 / 使用范围），否则不导入")
    if kind not in INGEST_KINDS:
        raise ValueError(f"kind 只能是 {' / '.join(INGEST_KINDS)}")
    if condition not in conditions.CONDITION_KEYS:
        raise ValueError(f"condition 只能是 {', '.join(conditions.CONDITION_KEYS)}")
    if target_emotion and target_emotion not in config.TRAIN_CLASSES:
        raise ValueError("target_emotion 必须是 7 类之一或留空")
    item_id = item_id or _default_id(src, kind)
    if not is_safe_id(item_id):
        raise ValueError(f"非法 id {item_id!r}（只能是字母、数字、_、-）")
    cdir = Path(catalog_dir) if catalog_dir else catalog.CATALOG_DIR
    adir = cdir / "assets"
    jpath, mpath = cdir / f"{item_id}.json", adir / f"{item_id}.mp4"
    if (jpath.exists() or mpath.exists()) and not overwrite:
        raise FileExistsError(f"{item_id} 已存在（{jpath.name} / assets/{mpath.name}），覆盖请加 --overwrite")

    info = probe(src)
    if start_s and info["duration_s"] and float(start_s) >= info["duration_s"]:
        raise ValueError(f"--start {start_s} 超出视频时长 {info['duration_s']} s")
    video_filter(kind, info, force)                    # 先校验宽高比，避免白跑响度测量
    trial_class = "immersive" if kind == "skybox_video" else "mimicry"
    it = {
        "id": item_id, "name": name or item_id, "kind": kind, "trial_class": trial_class,
        "rating": rating or ("full" if trial_class == "immersive" else "quick"),
        "condition": condition, "target_emotion": target_emotion or "",
        "file": mpath.name, "source": str(source or ""), "license": str(license).strip(),
        "ethics_tier": int(ethics_tier or 0), "habituation_group": habituation_group or "",
        "norms": norms or _norms(), "enabled": False, "qc": "pending", "note": str(note or "")[:200],
    }
    _check(it)
    if dry_run:
        return {**it, "_probe": info}

    adir.mkdir(parents=True, exist_ok=True)
    tmp = adir / f".{item_id}.part.mp4"
    try:
        au = transcode(src, tmp, kind, info, start_s, duration_s, force, audio=not no_audio)
        out_info = probe(tmp)
        ll = lowlevel(tmp, kind, out_info)
        tmp.replace(mpath)
    finally:
        if tmp.exists():
            tmp.unlink()
    it["audio"] = au["audio"]
    it["duration_s"] = round(out_info["duration_s"], 3)
    it["params"] = {
        "lowlevel": ll,
        "ingest": {"src_name": src.name, "src_width": info["width"], "src_height": info["height"],
                   "src_fps": info["fps"], "src_duration_s": info["duration_s"], "src_has_audio": info["has_audio"],
                   "start_s": float(start_s or 0), "duration_s": float(duration_s or 0) or None,
                   "width": out_info["width"], "height": out_info["height"], "fps": FPS, "crf": CRF,
                   "forced_aspect": bool(force and kind == "skybox_video"
                                         and abs(info["width"] / max(1, info["height"]) - 2.0) > 0.02),
                   "loudnorm": LOUDNORM if au["loudness_in"] else "", "silent_audio": au["silent"],
                   "loudness_in": au["loudness_in"], "loudness_out": au["loudness_out"]},
    }
    it = _check(it)
    cdir.mkdir(parents=True, exist_ok=True)
    tj = jpath.with_suffix(".json.tmp")
    tj.write_text(json.dumps(it, ensure_ascii=False, indent=1), encoding="utf-8")
    tj.replace(jpath)
    return it


def _check(it: dict) -> dict:
    """借 catalog 的规整与校验，保证写出的条目网页 / 播放端都认。"""
    n = catalog._normalize({k: v for k, v in it.items() if k in catalog._DEFAULTS}, it["id"])
    catalog._validate(n)
    return n


# ── 批量 ────────────────────────────────────────────────────────────
MANIFEST_COLS = ("file", "id", "kind", "condition", "target_emotion", "start_s", "duration_s", "norm_valence",
                 "norm_arousal", "norm_sd_v", "norm_sd_a", "norm_n", "norm_source", "source", "license",
                 "ethics_tier", "rating", "name", "habituation_group", "note")


def ingest_manifest(csv_path, *, catalog_dir=None, force: bool = False, overwrite: bool = False,
                    dry_run: bool = False, defaults: dict | None = None, log=print) -> list:
    """按清单 CSV 逐行导入；file 是相对 CSV 所在目录的路径。单行失败不影响其余行。
    defaults：CSV 某列为空时用的值（CLI 的同名参数，如 license / source / kind）。返回 [(id 或 file, ok, 信息)]。"""
    import csv
    csv_path = Path(csv_path)
    defaults = defaults or {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if rows and "file" not in rows[0]:
        raise ValueError(f"清单缺少 file 列；可用列：{', '.join(MANIFEST_COLS)}")
    results = []
    for k, row in enumerate(rows, 2):
        row = {c: (row.get(c) or "").strip() or str(defaults.get(c) or "") for c in MANIFEST_COLS}
        label = row["id"] or row["file"]
        try:
            src = Path(row["file"])
            if not src.is_absolute():
                src = csv_path.parent / src
            it = ingest(src, kind=row["kind"] or "skybox_video", license=row["license"], item_id=row["id"],
                        condition=row["condition"] or "NEU", target_emotion=row["target_emotion"],
                        start_s=float(row["start_s"]) if row["start_s"] else None,
                        duration_s=float(row["duration_s"]) if row["duration_s"] else None,
                        rating=row["rating"], name=row["name"], source=row["source"],
                        ethics_tier=int(row["ethics_tier"] or 0), habituation_group=row["habituation_group"],
                        note=row["note"],
                        norms=_norms(row["norm_valence"], row["norm_arousal"], row["norm_sd_v"], row["norm_sd_a"],
                                     row["norm_n"], row["norm_source"]),
                        force=force, overwrite=overwrite, catalog_dir=catalog_dir, dry_run=dry_run)
            results.append((it["id"], True, summary(it)))
            log(f"[ok]   第 {k} 行 {it['id']}: {summary(it)}")
        except Exception as e:  # noqa: BLE001  逐行容错，汇总后统一报告
            results.append((label, False, str(e)))
            log(f"[fail] 第 {k} 行 {label}: {e}")
    return results


def summary(it: dict) -> str:
    ing = (it.get("params") or {}).get("ingest") or {}
    ll = (it.get("params") or {}).get("lowlevel") or {}
    parts = [it["kind"], it["condition"], it.get("target_emotion") or "-"]
    if ing:
        parts += [f"{ing['width']}x{ing['height']}", f"{it['duration_s']:.1f}s",
                  "audio" if it["audio"] else "no-audio"]
        lo = ing.get("loudness_out")
        if lo:
            parts.append(f"{lo['i']:.1f} LUFS")
    if ll:
        parts.append(f"lum={ll.get('luminance', 0):.3f}")
    return " ".join(parts)
