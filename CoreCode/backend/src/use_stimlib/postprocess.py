"""视频后处理与低层视觉特征。

接缝：视频模型不知道等距全景左右边缘是相连的，直接生成会在 0°/360° 处出现一条错位缝。
做法是生成前把全景最左边 pad 列复制接到右边（宽度 W+pad），模型在这段重叠区里看到的是
连续内容；生成后把右侧重叠区与左侧同一位置交叉淡化回填，左右边缘就自然衔接了。

循环：Wan 一次只出约 5 秒。刺激需要几十秒，用交叉淡化循环（尾部 K 帧淡入头部 K 帧）
拼成无跳变的循环再重复。不用正放-倒放：雨、瀑布倒着动一眼就看得出来。

图片线的静态图取自**同一条处理链**的第 0 帧（同分辨率、同放大、同接缝处理），
保证两条线的唯一差别是「动不动」，而不是清晰度或色彩。

低层特征（亮度 / 对比度 / 色彩度 / 运动能量）按纬度 cos 加权：等距投影越靠近两极
像素越被拉伸，不加权会让天空和地面的权重被高估。
"""
import hashlib

import cv2
import numpy as np


def decode_png(data: bytes) -> np.ndarray:
    """PNG 字节 → RGB uint8。"""
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("图像解码失败")
    return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def encode_png(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError("PNG 编码失败")
    return buf.tobytes()


def save_jpg(rgb: np.ndarray, path, quality: int = 95) -> None:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPG 编码失败")
    path.write_bytes(buf.tobytes())


def resize(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    interp = cv2.INTER_AREA if width < w else cv2.INTER_LANCZOS4
    return cv2.resize(rgb, (width, height), interpolation=interp)


def wrap_pad(rgb: np.ndarray, width: int, height: int, pad: int) -> np.ndarray:
    """缩放到 width×height 后把最左 pad 列接到右边 → (height, width+pad)。"""
    small = resize(rgb, width, height)
    return np.concatenate([small, small[:, :pad]], axis=1)


def unwrap(frame: np.ndarray, width: int, pad: int) -> np.ndarray:
    """wrap_pad 的逆：去掉右侧重叠区，并把它与左侧交叉淡化，消除 0°/360° 接缝。

    第 0 列取重叠区（它在生成时紧挨着第 width-1 列，所以与右边缘连续），
    向右逐渐过渡到原左侧内容。
    """
    out = frame[:, :width].astype(np.float32)
    if pad > 0:
        a = np.linspace(0.0, 1.0, pad, dtype=np.float32)[None, :, None]
        out[:, :pad] = (1.0 - a) * frame[:, width:width + pad].astype(np.float32) + a * out[:, :pad]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def crossfade_loop(frames: list, k: int) -> list:
    """n 帧 → n-k 帧的无缝循环：前 k 帧由尾部 k 帧淡入，末帧之后接回首帧时内容连续。"""
    n = len(frames)
    k = max(0, min(k, n // 3))
    if k == 0:
        return list(frames)
    loop = list(frames[:n - k])
    for i in range(k):
        a = i / k
        loop[i] = np.clip((1 - a) * frames[n - k + i].astype(np.float32)
                          + a * frames[i].astype(np.float32) + 0.5, 0, 255).astype(np.uint8)
    return loop


def encode_mp4(loop: list, path, width: int, height: int, fps: int, duration_s: float, crf: int = 20) -> None:
    """循环帧重复到 duration_s 秒，放大到 width×height，编码 H.264（Quest 硬解兼容）。"""
    import imageio_ffmpeg

    total = max(1, int(round(duration_s * fps)))
    writer = imageio_ffmpeg.write_frames(
        str(path), (width, height), fps=fps, codec="libx264", pix_fmt_out="yuv420p", quality=None,
        macro_block_size=16,
        output_params=["-crf", str(crf), "-preset", "medium", "-profile:v", "high",
                       "-movflags", "+faststart"],
    )
    writer.send(None)
    try:
        cache = {}
        for i in range(total):
            j = i % len(loop)
            if j not in cache:
                cache[j] = np.ascontiguousarray(resize(loop[j], width, height))
            writer.send(cache[j])
    finally:
        writer.close()


def _lat_weights(height: int) -> np.ndarray:
    lat = (np.arange(height) + 0.5) / height * np.pi - np.pi / 2
    w = np.cos(lat)
    return (w / w.sum()).astype(np.float64)


def _wmean(x: np.ndarray, w: np.ndarray) -> float:
    return float((x.mean(axis=1) * w).sum())


def frame_stats(rgb: np.ndarray) -> dict:
    f = rgb.astype(np.float64)
    w = _lat_weights(f.shape[0])
    y = (0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]) / 255.0
    mean_y = _wmean(y, w)
    contrast = float(np.sqrt(max(0.0, _wmean((y - mean_y) ** 2, w))))
    rg = f[..., 0] - f[..., 1]
    yb = 0.5 * (f[..., 0] + f[..., 1]) - f[..., 2]
    m_rg, m_yb = _wmean(rg, w), _wmean(yb, w)
    s_rg = np.sqrt(max(0.0, _wmean((rg - m_rg) ** 2, w)))
    s_yb = np.sqrt(max(0.0, _wmean((yb - m_yb) ** 2, w)))
    colorfulness = float(np.sqrt(s_rg ** 2 + s_yb ** 2) + 0.3 * np.sqrt(m_rg ** 2 + m_yb ** 2))
    return {"luminance": mean_y, "contrast": contrast, "colorfulness": colorfulness}


def video_stats(frames: list, step: int = 4) -> dict:
    """平均画面统计 + 运动能量（相邻帧亮度绝对差的加权均值，0–1）。"""
    sample = frames[::max(1, step)]
    stats = [frame_stats(f) for f in sample]
    out = {k: float(np.mean([s[k] for s in stats])) for k in stats[0]}
    w = _lat_weights(frames[0].shape[0])
    motion = []
    prev = None
    for f in frames:
        y = (0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]) / 255.0
        if prev is not None:
            motion.append(_wmean(np.abs(y - prev), w))
        prev = y
    out["motion"] = float(np.mean(motion)) if motion else 0.0
    return out


def still_stats(rgb: np.ndarray) -> dict:
    return {**frame_stats(rgb), "motion": 0.0}


def file_md5(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
