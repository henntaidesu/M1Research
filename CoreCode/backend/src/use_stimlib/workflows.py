"""API 格式工作流。直接在 Python 里拼，不依赖 ComfyUI 用户目录里的 UI 格式文件——
批量生成要可复现，参数必须全部显式写进 meta.json。

全景：与 comfyui/core/user/default/workflows/qwen360_pano.json 同一套模型
      （Qwen-Image 2512 fp8 + qwen-360 LoRA），出 2:1 等距柱状全景。
视频：Wan2.2 TI2V-5B image-to-video。模型文件需放在
      models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors
      models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
      models/vae/wan2.2_vae.safetensors
"""

PANO_MODELS = {
    "unet": "qwen-image-2512-fp8.safetensors",
    "lora": "qwen-360-diffusion-2512-int8-bf16-v2.safetensors",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
}

VIDEO_MODELS = {
    "unet": "wan2.2_ti2v_5B_fp16.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan2.2_vae.safetensors",
}


def pano_required() -> dict:
    return {
        "UNETLoader": [("unet_name", PANO_MODELS["unet"])],
        "LoraLoaderModelOnly": [("lora_name", PANO_MODELS["lora"])],
        "CLIPLoader": [("clip_name", PANO_MODELS["clip"])],
        "VAELoader": [("vae_name", PANO_MODELS["vae"])],
    }


def video_required() -> dict:
    return {
        "UNETLoader": [("unet_name", VIDEO_MODELS["unet"])],
        "CLIPLoader": [("clip_name", VIDEO_MODELS["clip"])],
        "VAELoader": [("vae_name", VIDEO_MODELS["vae"])],
    }


def pano_workflow(positive: str, negative: str, seed: int, width: int = 2048, height: int = 1024,
                  steps: int = 20, cfg: float = 3.5) -> dict:
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": PANO_MODELS["unet"], "weight_dtype": "default"}},
        "10": {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": ["1", 0], "lora_name": PANO_MODELS["lora"], "strength_model": 1.0}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": PANO_MODELS["clip"], "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "5": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler",
                         "scheduler": "simple", "denoise": 1.0, "model": ["10", 0],
                         "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": PANO_MODELS["vae"]}},
        "8": {"class_type": "VAEDecodeTiled",
              "inputs": {"samples": ["6", 0], "vae": ["7", 0], "tile_size": 512, "overlap": 64,
                         "temporal_size": 64, "temporal_overlap": 8}},
        "9": {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}},
    }


def video_workflow(image_name: str, positive: str, negative: str, seed: int, width: int, height: int,
                   length: int = 121, steps: int = 20, cfg: float = 5.0, shift: float = 8.0) -> dict:
    """输出逐帧 PNG，由后端自己做接缝 / 循环 / 编码。

    输出节点用 PreviewImage（写 ComfyUI 的 temp/，重启即清）而不是 SaveImage：
    每对 121 帧 PNG 约 180MB，存进 output/ 几十对就是十几 GB 垃圾。全景同理。"""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": VIDEO_MODELS["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": VIDEO_MODELS["clip"], "type": "wan", "device": "default"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["3", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["3", 0]}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_MODELS["vae"]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["6", 0], "width": width, "height": height, "length": length,
                         "batch_size": 1, "start_image": ["7", 0]}},
        "9": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "uni_pc",
                         "scheduler": "simple", "denoise": 1.0, "model": ["2", 0],
                         "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["8", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["6", 0]}},
        "11": {"class_type": "PreviewImage", "inputs": {"images": ["10", 0]}},
    }
