"""刺激库：图片线 / 视频线成对生成 → 头显播放控制 → 两条线的诱发效果对比。

为什么成对：同一张全景既作「图片线」刺激，又经 image-to-video 生成「视频线」刺激，
两者内容、分辨率、色彩处理完全一致，唯一差别是「动不动」。这样比较两条线时，
差异才能归因到呈现方式，而不是被内容混淆。

  conditions  5 个效价×唤醒条件 + 固定场景表（可复现的提示词）
  comfy       ComfyUI HTTP 客户端
  workflows   全景（Qwen-Image + 360 LoRA）与 I2V（Wan2.2 TI2V-5B）的 API 格式工作流
  postprocess 接缝修补、循环、放大、H.264 编码、低层视觉特征
  library     刺激库（每对一个目录 + meta.json）与自评 ratings.csv
  generator   后台批量生成任务
  playback    头显播放控制（手动 / 序列）+ 运行记录
  compare     两条线的对比分析
"""
