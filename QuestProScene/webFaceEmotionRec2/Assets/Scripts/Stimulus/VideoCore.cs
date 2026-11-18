// VideoCore.cs
// SkyboxVideoPresenter / MimicryClipPresenter 共用的视频播放部分：
// VideoPlayer → RenderTexture、可选音轨、onset 判定、暂停 / 拖动、卡顿检测、播完检测。
//
// 声音：stim.audio = true 时 VideoAudioOutputMode.AudioSource（2D，音量 1），否则不出声。
//       音轨设置必须在 Prepare 之前完成。
// onset：Play 之后第一个 frameReady（sendFrameReadyEvents 只在等 onset 时打开，省 CPU）；
//        兜底：Play 之后 time > 0 的第一帧；再兜底：Play 后 3 秒还没等到就按当时算（并打 onset_fallback 事件）。
// 卡顿：播放中（未暂停）time 超过 1 秒没前进 → 打一次 video_stall 事件，恢复前进后可再次触发。

using System.Collections;
using UnityEngine;
using UnityEngine.Video;

public class VideoCore
{
    public VideoPlayer Vp { get; private set; }
    public RenderTexture Rt { get; private set; }
    public bool Ready { get; private set; }
    public bool Failed { get; private set; }
    public bool Completed { get; private set; }

    private readonly StimContext _ctx;
    private readonly bool _loop;
    private GameObject _go;
    private bool _ended, _playing, _onsetDone, _awaitFrame;
    private long _frameMs;
    private float _playAt;
    private double _lastT = -1;
    private float _lastAdvanceAt;
    private bool _stallReported;

    public VideoCore(StimContext ctx, bool loop, bool audio)
    {
        _ctx = ctx;
        _loop = loop;
        _go = new GameObject("StimVideo_" + ctx.StimId);
        _go.transform.SetParent(ctx.Root, false);
        Vp = _go.AddComponent<VideoPlayer>();
        Vp.playOnAwake = false;
        Vp.isLooping = loop;              // 天空盒视频：时长由后端控制，文件已按设定时长循环编码，这里再兜底
        Vp.waitForFirstFrame = true;
        Vp.skipOnDrop = true;
        Vp.source = VideoSource.Url;
        Vp.renderMode = VideoRenderMode.RenderTexture;
        if (audio)
        {
            var src = _go.AddComponent<AudioSource>();
            src.playOnAwake = false;
            src.spatialBlend = 0f;
            src.volume = 1f;
            Vp.audioOutputMode = VideoAudioOutputMode.AudioSource;
            Vp.controlledAudioTrackCount = 1;
            Vp.EnableAudioTrack(0, true);
            Vp.SetTargetAudioSource(0, src);
        }
        else Vp.audioOutputMode = VideoAudioOutputMode.None;
        Vp.errorReceived += (s, msg) =>
        {
            Failed = true;
            Debug.LogWarning("[StimPlayer] " + msg);
            _ctx.Fail("video: " + msg);
        };
        Vp.loopPointReached += s => { if (!_loop) Completed = true; };
        Vp.frameReady += (s, idx) =>
        {
            if (_awaitFrame && _frameMs == 0 && Vp.isPlaying) _frameMs = StimNet.NowMs();
        };
    }

    /// <param name="wrapRepeat">天空盒：经度方向环绕，配合 shader 消除接缝</param>
    public IEnumerator Prepare(string path, bool wrapRepeat)
    {
        Vp.url = path;
        Vp.Prepare();
        float t0 = Time.realtimeSinceStartup;
        while (!_ended && !Vp.isPrepared && !Failed && Time.realtimeSinceStartup - t0 < 30f) yield return null;
        if (_ended || Failed) yield break;
        if (!Vp.isPrepared) { Failed = true; _ctx.Fail("video prepare timeout"); yield break; }
        Rt = new RenderTexture((int)Vp.width, (int)Vp.height, 0, RenderTextureFormat.ARGB32);
        Rt.wrapModeU = wrapRepeat ? TextureWrapMode.Repeat : TextureWrapMode.Clamp;
        Rt.wrapModeV = TextureWrapMode.Clamp;
        Rt.Create();
        Vp.targetTexture = Rt;
        Ready = true;
    }

    /// <summary>开始播放（paused = true 时只显示首帧，状态报 paused，onset 等真正开播再算）。</summary>
    public void Start(bool paused)
    {
        if (!Ready) return;
        if (paused) { Vp.Pause(); _playing = false; _ctx.Ack("paused", 0); }
        else Play();
    }

    private void Play()
    {
        if (Completed) return;                       // 不循环的片段播完后不因「取消暂停」重播
        Vp.Play();
        _playing = true;
        _lastAdvanceAt = Time.realtimeSinceStartup;
        _stallReported = false;
        if (!_onsetDone)
        {
            _awaitFrame = true;
            _frameMs = 0;
            _playAt = Time.realtimeSinceStartup;
            Vp.sendFrameReadyEvents = true;
        }
        else _ctx.Ack("playing", 0);
    }

    public void SetPaused(bool paused)
    {
        if (!Ready) return;
        if (paused && Vp.isPlaying) { Vp.Pause(); _playing = false; _ctx.Ack("paused", 0); }
        else if (!paused && !Vp.isPlaying && !Completed) Play();
    }

    public void Seek(float seconds)
    {
        if (!Ready) return;
        Vp.time = seconds;
        if (Completed && Vp.length > 0 && seconds < Vp.length - 0.1) Completed = false;
        _lastAdvanceAt = Time.realtimeSinceStartup;
    }

    public float Position => Ready ? (float)Vp.time : 0f;

    /// <summary>每帧（LateUpdate）：判定 onset、检测卡顿。</summary>
    public void Tick()
    {
        if (!Ready || Failed || _ended) return;
        float now = Time.realtimeSinceStartup;
        if (_awaitFrame && !_onsetDone)
        {
            long ms = 0;
            string src = "";
            if (_frameMs > 0) ms = _frameMs;
            else if (Vp.isPlaying && Vp.time > 0) ms = StimNet.NowMs();
            else if (now - _playAt > 3f) { ms = StimNet.NowMs(); src = "timeout"; }
            if (ms > 0)
            {
                _onsetDone = true;
                _awaitFrame = false;
                Vp.sendFrameReadyEvents = false;
                if (src == "timeout") _ctx.Event("onset_fallback", "no frame 3s after Play");
                _ctx.Ack("playing", ms);
            }
        }
        if (_playing && _onsetDone && !Completed)
        {
            double t = Vp.time;
            if (t != _lastT) { _lastT = t; _lastAdvanceAt = now; _stallReported = false; }
            else if (!_stallReported && now - _lastAdvanceAt > 1f)
            {
                _stallReported = true;
                _ctx.Event("video_stall", "pos_s=" + t.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture));
            }
        }
    }

    public void End()
    {
        if (_ended) return;
        _ended = true;
        Ready = false;
        if (Vp != null && (Vp.isPlaying || Vp.isPrepared)) Vp.Stop();
        if (_go != null) Object.Destroy(_go);
        if (Rt != null) { Rt.Release(); Object.Destroy(Rt); Rt = null; }
    }
}
