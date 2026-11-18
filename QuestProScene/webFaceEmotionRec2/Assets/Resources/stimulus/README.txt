把 360° 全景刺激图（jpg/png，等距柱状投影 equirectangular）放在这个目录下。
两个采集脚本都会从这里加载：
  - QuestFeaCollector.cs        自动定时循环播放 + 记录 blendshape
  - VREmotionSelectionUI.cs     每张图看完后由用户点选 7 类情绪按钮

文件名建议 <emotion>_<序号>.jpg，例如 happy_01.jpg，便于人工核对。
图片来源：webside「图像生成 → VR 刺激图」面板批量生成后导出到这里。
