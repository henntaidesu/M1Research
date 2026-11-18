// IStimulusPresenter.cs
// 一种刺激 kind 的呈现器。StimulusPlayer 只管轮询 / 对时 / FEA / 阶段分发，具体怎么把刺激
// 呈现出来交给这里的实现（SkyboxImage / SkyboxVideo / MimicryClip / ScenePrefab）。
//
// 生命周期（每个刺激 new 一个实例，用完即弃）：
//   Prepare   协程：下载 / 解码 / 实例化（不可见）。成功置 IsReady；失败调 ctx.Fail(原因)
//             scene 在注视阶段就按 upcoming 提前 Prepare，到刺激阶段直接 Begin
//   Begin     变为可见。onset 由呈现器自己确定后调 ctx.Ack(state, onsetHeadsetMs)
//             （图片 / 场景：Begin 后第一帧 LateUpdate；视频：第一帧 frameReady）
//   Tick      每帧 LateUpdate 调用；tSinceOnset < 0 表示还没 onset
//   SetPaused / Seek   网页端的暂停 / 拖动（图片、场景可忽略）
//   Completed 素材自然播完（模仿片段放完、场景事件全部结束）；StimulusPlayer 据此打一次 complete 事件
//   End       停止并销毁自己创建的对象，可重复调用

using System.Collections;

public interface IStimulusPresenter
{
    string Kind { get; }
    IEnumerator Prepare(StimSpec spec, StimContext ctx);
    bool IsReady { get; }
    void Begin();
    void Tick(float tSinceOnset);
    void SetPaused(bool paused);
    void Seek(float seconds);
    float Position { get; }
    bool Completed { get; }
    void End();
}
