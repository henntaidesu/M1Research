"""ComfyUI HTTP 客户端（标准库 urllib，不新增依赖）。

批量生成跑在后端线程里，直连 config.COMFYUI_URL；网页侧的 VR 刺激图面板仍走 Vite 代理，
两者共用同一个 ComfyUI 队列，互不影响。
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .. import config


class ComfyError(RuntimeError):
    pass


def _url(path: str) -> str:
    return config.COMFYUI_URL.rstrip("/") + path


def _get_json(path: str, timeout: float = 15):
    with urllib.request.urlopen(_url(path), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(path: str, body: dict, timeout: float = 30):
    req = urllib.request.Request(_url(path), data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise ComfyError(f"{path} [{e.code}]: {e.read().decode('utf-8', 'replace')[:500]}") from e


def online() -> bool:
    try:
        _get_json("/system_stats", timeout=3)
        return True
    except (OSError, ValueError):
        return False


def missing_models(required: dict) -> list:
    """required: {loader 节点类型: [(输入名, 文件名), ...]} → 缺失的文件名列表。

    出图前先查，免得排进队列跑半天才报「模型不存在」。
    """
    missing = []
    for node, pairs in required.items():
        try:
            info = _get_json(f"/object_info/{node}")
        except (OSError, ValueError):
            missing.extend(f for _, f in pairs)
            continue
        spec = info.get(node, {}).get("input", {}).get("required", {})
        for inp, fname in pairs:
            choices = spec.get(inp, [[]])[0]
            if not isinstance(choices, list) or fname not in choices:
                missing.append(fname)
    return missing


def upload_image(data: bytes, filename: str) -> str:
    """上传到 ComfyUI input/，返回 LoadImage 可用的名字。"""
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
         "Content-Type: image/png\r\n\r\n").encode() + data + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(_url("/upload/image"), data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    name = res.get("name") or filename
    sub = res.get("subfolder") or ""
    return f"{sub}/{name}" if sub else name


def queue(workflow: dict) -> str:
    res = _post_json("/prompt", {"prompt": workflow, "client_id": "stimlib"})
    if res.get("node_errors"):
        raise ComfyError(f"工作流校验失败：{json.dumps(res['node_errors'], ensure_ascii=False)[:500]}")
    pid = res.get("prompt_id")
    if not pid:
        raise ComfyError(f"未返回 prompt_id：{res}")
    return pid


def interrupt() -> None:
    try:
        _post_json("/interrupt", {})
    except (OSError, ComfyError):
        pass


def wait(prompt_id: str, timeout_s: float, should_stop=lambda: False, poll_s: float = 2.0) -> dict:
    """轮询 /history 直到出结果；返回该 prompt 的 outputs。should_stop() 为真时中断并抛错。"""
    t0 = time.time()
    while True:
        if should_stop():
            interrupt()
            raise ComfyError("已取消")
        try:
            h = _get_json(f"/history/{prompt_id}")
        except (OSError, ValueError):
            h = {}
        entry = h.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                detail = msgs[0][1].get("exception_message", "") if msgs else ""
                raise ComfyError(f"ComfyUI 执行出错：{detail}".strip())
            if entry.get("outputs"):
                return entry["outputs"]
        if time.time() - t0 > timeout_s:
            raise ComfyError(f"等待 ComfyUI 结果超时（{timeout_s:.0f}s）")
        time.sleep(poll_s)


def output_images(outputs: dict) -> list:
    """outputs → [(filename, subfolder, type), ...]，按文件名排序（视频帧按序号）。"""
    items = []
    for node_out in outputs.values():
        for img in node_out.get("images", []) or []:
            items.append((img["filename"], img.get("subfolder", ""), img.get("type", "output")))
    items.sort(key=lambda x: x[0])
    return items


def fetch(filename: str, subfolder: str = "", type_: str = "output") -> bytes:
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": type_})
    with urllib.request.urlopen(_url(f"/view?{q}"), timeout=60) as r:
        return r.read()
