// SkyboxVideoPresenter.cs
// kind = skybox_video：360° 视频经 RenderTexture 贴到天空盒（v1 的视频线）。
//
// 与 v1 的区别只有两点（契约 v2）：
//   - stim.audio = true 时播放音轨（AudioSource 输出）；默认仍然无声
//   - 回报 playing 的时机从「调用 Play 后立刻」改为「第一帧真正出来」，并带上 onset_headset_ms
// 灰屏亮度匹配对视频不做（LastLuma = -1，注视 / 恢复用 grayLevel），与 v1 相同。

using System.Collections;
using System.IO;

public class SkyboxVideoPresenter : IStimulusPresenter
{
    private StimContext _ctx;
    private VideoCore _video;
    private bool _begun, _wantPaused, _ended;

    public string Kind => StimSpec.KindVideo;
    public bool IsReady => _video != null && _video.Ready;
    public float Position => _video != null ? _video.Position : 0f;
    public bool Completed => false;               // 循环播放，时长由后端控制

    public IEnumerator Prepare(StimSpec spec, StimContext ctx)
    {
        _ctx = ctx;
        yield return ctx.Cache.Ensure(spec);
        if (_ended) yield break;
        string path = ctx.CachePath;
        if (!File.Exists(path))
        {
            ctx.Fail(string.IsNullOrEmpty(ctx.Cache.LastError) ? "download failed" : ctx.Cache.LastError);
            yield break;
        }
        _video = new VideoCore(ctx, loop: true, audio: spec.audio || ctx.VideoAudioDefault);
        yield return _video.Prepare(path, wrapRepeat: true);
    }

    public void Begin()
    {
        if (!IsReady || _begun) return;
        _begun = true;
        _ctx.SetLastLuma(-1f);
        _ctx.FaceSubject();
        _ctx.SetSky(_video.Rt);
        _video.Start(_wantPaused);
    }

    public void Tick(float tSinceOnset) { if (_begun) _video.Tick(); }

    public void SetPaused(bool paused)
    {
        if (!_begun) { _wantPaused = paused; return; }   // Begin 之前：记下来，开播时照办
        _video.SetPaused(paused);
    }

    public void Seek(float seconds) { if (_begun) _video.Seek(seconds); }

    public void End()
    {
        _ended = true;
        _video?.End();
    }
}
