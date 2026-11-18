// StimSpec.cs
// 后端 /api/stimlib/playback 里 stimulus / upcoming / prefetch[] 的刺激对象（契约 v2）。
//
// v2 新字段对 Unity 全部可选：旧后端不发 kind 时按 media 推出 skybox_image / skybox_video，
// 行为与 v1 完全一致。JsonUtility：缺的字段保持默认值（数组为 null），null 的嵌套对象会变成
// 字段全空的实例，所以一律用 id 判空（StimSpec.Has）。

using System.Collections.Generic;

[System.Serializable]
public class StimEvent
{
    public string key;      // 事件名，回报时写成 evt:<key>
    public float t_s;       // 相对呈现起点（onset）的秒数，后端已加好抖动
}

[System.Serializable]
public class StimSpec
{
    // ── v1 字段 ──
    public string id;
    public string pair_id;
    public string line;
    public string media;        // "image" | "video"
    public string url;
    public string sha;
    public string condition;

    // ── v2 字段（括号内为缺省）──
    public string kind;           // skybox_image | skybox_video | scene | mimicry（按 media 推）
    public string trial_class;    // immersive | event | mimicry（immersive）
    public string rating;         // full | quick | none（full）
    public bool audio;            // 播放视频音轨（false）
    public string prefab_key;     // kind=scene 时 StimulusSceneRegistry 里的键（""）
    public string target_emotion; // 7 类之一或 ""
    public StimEvent[] events;    // 相对 onset 的事件时间表（[]）
    public string params_json;    // 各 kind 自己的参数，JSON 字符串（""）
    public float duration_s;      // 0 = 由后端模板决定

    public const string KindImage = "skybox_image";
    public const string KindVideo = "skybox_video";
    public const string KindScene = "scene";
    public const string KindMimicry = "mimicry";

    public static bool Has(StimSpec s) => s != null && !string.IsNullOrEmpty(s.id);

    /// <summary>实际 kind：没给就按 media 推（旧格式刺激）。</summary>
    public string ResolvedKind
    {
        get
        {
            if (!string.IsNullOrEmpty(kind)) return kind;
            return media == "video" ? KindVideo : KindImage;
        }
    }

    /// <summary>自评面板模式：full / quick / none，缺省 full。</summary>
    public string RatingMode => string.IsNullOrEmpty(rating) ? "full" : rating;

    /// <summary>需要从后端下载素材（scene 打包在 APK 里，没有 url）。</summary>
    public bool HasMedia => !string.IsNullOrEmpty(url);

    /// <summary>按 t_s 排好序的事件表（null 安全）。</summary>
    public List<StimEvent> SortedEvents()
    {
        var list = new List<StimEvent>();
        if (events != null)
            foreach (var e in events)
                if (e != null && !string.IsNullOrEmpty(e.key)) list.Add(e);
        list.Sort((a, b) => a.t_s.CompareTo(b.t_s));
        return list;
    }
}

/// <summary>
/// 事件时间表的推进器：scene / mimicry 共用。每帧给当前时刻，到点的事件按顺序回调一次。
/// </summary>
public class EventSchedule
{
    private readonly List<StimEvent> _events;
    private int _next;

    public EventSchedule(StimSpec s) { _events = s.SortedEvents(); }

    public int Count => _events.Count;
    public bool AllFired => _next >= _events.Count;

    public void Advance(float t, System.Action<StimEvent> fire)
    {
        while (_next < _events.Count && _events[_next].t_s <= t)
        {
            var e = _events[_next++];
            fire?.Invoke(e);
        }
    }
}
