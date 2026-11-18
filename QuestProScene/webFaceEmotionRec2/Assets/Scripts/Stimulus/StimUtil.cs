// StimUtil.cs
// 刺激库播放器各部件共用的小工具：网络 POST、JSON 转义、时钟、多语言文案、UI 文本。
// 从 StimulusPlayer 里原样抽出来，行为不变。

using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

public static class StimNet
{
    private static string Base => BackendConfig.BaseUrl;

    public static long NowMs() => System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

    public static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"")
                                                    .Replace("\n", "\\n").Replace("\r", "\\r");

    public static IEnumerator Post(string path, string json, System.Action<long, string> done)
    {
        using (var req = new UnityWebRequest(Base + path, "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 8;
            yield return req.SendWebRequest();
            done?.Invoke(req.responseCode, req.downloadHandler?.text ?? req.error);
        }
    }
}

public static class StimText
{
    // 7 类固定顺序，必须与 config.TRAIN_CLASSES / fea_emotion._EN_ORDER / VREmotionSelectionUI 一致
    public static readonly string[] EmotionKeys = { "angry", "disgust", "fear", "happy", "neutral", "sad", "surprise" };
    public static readonly string[] LabelsZh = { "愤怒", "厌恶", "恐惧", "开心", "平静", "悲伤", "惊讶" };
    public static readonly string[] LabelsJa = { "怒り", "嫌悪", "恐怖", "喜び", "中立", "悲しみ", "驚き" };
    public static readonly string[] LabelsEn = { "Angry", "Disgust", "Fear", "Happy", "Neutral", "Sad", "Surprise" };

    public static string L(string lang, string zh, string ja, string en) => lang == "ja" ? ja : (lang == "en" ? en : zh);

    public static string[] Labels(string lang) => lang == "ja" ? LabelsJa : (lang == "en" ? LabelsEn : LabelsZh);
}

public static class StimUi
{
    public static Text MakeText(RectTransform parent, string s, int size, Vector2 pos, Vector2 dim, Color color,
        TextAnchor anchor = TextAnchor.MiddleCenter)
    {
        var go = new GameObject("Text");
        go.transform.SetParent(parent, false);
        var r = go.AddComponent<RectTransform>();
        r.anchoredPosition = pos; r.sizeDelta = dim;
        var t = go.AddComponent<Text>();
        t.text = s; t.font = VREmotionSelectionUI.ResolveFont(); t.fontSize = size;
        t.alignment = anchor; t.color = color; t.horizontalOverflow = HorizontalWrapMode.Wrap;
        t.raycastTarget = false;
        return t;
    }

    private static Shader _unlit;

    /// <summary>
    /// 不受光照的纯色 / 贴图材质。优先用 Resources/M1Unlit.shader（写深度，一定被打包）；
    /// 找不到时退回 Sprites/Default（GraphicsSettings 的 Always Included 里有它）。
    /// 不用 Shader.Find("Unlit/Texture")：没被任何材质引用时打包会被裁掉，头显上返回 null。
    /// </summary>
    public static Material UnlitMaterial(Color color, Texture tex = null)
    {
        if (_unlit == null) _unlit = Resources.Load<Shader>("M1Unlit");
        var sh = _unlit != null ? _unlit : (Shader.Find("Sprites/Default") ?? Shader.Find("UI/Default"));
        var m = new Material(sh);
        m.color = color;
        if (tex != null) m.mainTexture = tex;
        return m;
    }
}
