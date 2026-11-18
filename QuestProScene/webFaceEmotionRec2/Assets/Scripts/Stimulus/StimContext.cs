// StimContext.cs
// 呈现器能用到的 StimulusPlayer 能力，一个刺激一份（带着 StimId）。
// Ack / Fail / SetSky 等只在该刺激仍是「当前刺激」时生效：被切走的旧呈现器即使晚到的回调
// 也改不了播放器状态（以前靠 _curId 比较，这里集中处理）。

using UnityEngine;

public class StimContext
{
    public string StimId;
    public StimSpec Spec;

    // ── 由 StimulusPlayer 填 ──
    public MonoBehaviour Host;            // 跑协程用
    public Transform Root;                // 呈现器创建的对象挂在它下面（DontDestroyOnLoad）
    public OVRCameraRig Rig;
    public Camera EyeCamera;
    public AssetCache Cache;
    public Texture2D Gray;                // 灰屏纹理（亮度由 SetGrayLevel 控制）
    public float DefaultGray = 0.5f;      // 中性灰（grayLevel）
    public bool VideoAudioDefault;        // 旧开关 StimulusPlayer.videoAudio，stim.audio 之外的兜底

    public System.Func<string, bool> IsCurrentFn;
    public System.Func<string> LangFn;
    public System.Action<Texture> SetSkyFn;
    public System.Action FaceSubjectFn;
    public System.Action<float> SetGrayLevelFn;
    public System.Action<string, float> SetLastLumaFn;          // (stimId, luma)；-1 = 视频 / 非图片
    public System.Action<string, string, long> AckFn;           // (stimId, state, onsetHeadsetMs)
    public System.Action<string, string> FailFn;                // (stimId, why)
    public System.Action<string, string, string, long> EventFn; // (stimId, key, detail, headsetMs)
    public System.Action<GameObject, float, float> PlaceInFrontFn;

    public bool IsCurrent => IsCurrentFn != null && IsCurrentFn(StimId);
    public string Lang => LangFn != null ? LangFn() : "zh";
    public string L(string zh, string ja, string en) => StimText.L(Lang, zh, ja, en);
    public string CachePath => Cache != null && Spec != null ? Cache.PathFor(Spec) : "";

    /// <summary>头部当前水平朝向（度）。</summary>
    public float HeadYaw => Rig != null && Rig.centerEyeAnchor != null ? Rig.centerEyeAnchor.eulerAngles.y : 0f;

    public void SetSky(Texture tex) { if (IsCurrent) SetSkyFn?.Invoke(tex); }
    public void SetGray(float level) { if (IsCurrent) { SetGrayLevelFn?.Invoke(level); SetSkyFn?.Invoke(Gray); } }
    public void FaceSubject() { if (IsCurrent) FaceSubjectFn?.Invoke(); }
    public void SetLastLuma(float v) { if (IsCurrent) SetLastLumaFn?.Invoke(StimId, v); }

    /// <summary>
    /// 回报播放状态（shown / playing / paused）。onsetHeadsetMs &gt; 0 时同时登记 onset
    /// （头显时钟 UnixMs，受试者第一次看到刺激的那一帧），后端用 clock_offset 换算到服务器时钟。
    /// </summary>
    public void Ack(string state, long onsetHeadsetMs) { if (IsCurrent) AckFn?.Invoke(StimId, state, onsetHeadsetMs); }

    public void Fail(string why) { if (IsCurrent) FailFn?.Invoke(StimId, why); }

    /// <summary>打点事件，headsetMs = 0 取当前时刻。后端写成 events.csv 的 evt:&lt;key&gt;。</summary>
    public void Event(string key, string detail = "", long headsetMs = 0)
    {
        EventFn?.Invoke(StimId, key, detail ?? "", headsetMs > 0 ? headsetMs : StimNet.NowMs());
    }

    public void PlaceInFront(GameObject go, float dist, float yOffset) => PlaceInFrontFn?.Invoke(go, dist, yOffset);
}
