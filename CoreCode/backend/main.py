"""后端入口：启动 FastAPI 服务（Quest Pro 数据采集 + 模型训练/评测，CUDA 加速）。

实际逻辑在 src/ 包，按职责划分：
  src/config.py                    配置（共用，路径指向仓库根 DataSet/）
  src/settings_store.py            conf.ini 运行时设置
  src/use_web/                     前端 / 头显对接（路由、登录、刺激控制、WebRTC 信令）
  src/use_capture/                 Unity 会话流落盘（DataSet/MyDataSet/Capture）
  src/use_store/                   情感偏好采集 CSV 存储（DataSet/MyDataSet/AffectData）
  src/use_model/                   FEA→情绪规则分类、标签、设备、模型注册表
  src/use_train/                   图像 FER 训练（公开数据集）
  src/use_eval/                    图像 FER 评测
  src/use_predict/                 FEA 时序情绪预测训练（自采数据集，CLI：train_timeline.py）
  src/use_llm/                     DeepSeek 刺激提示词生成
  src/use_stimlib/                 刺激库：图片/视频成对生成、头显播放控制、两条线对比
"""
import uvicorn

from src import config
from src.use_web.app import app

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, reload=False)
