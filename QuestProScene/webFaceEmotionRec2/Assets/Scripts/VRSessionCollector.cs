// VRSessionCollector.cs
// FEA 时序闭环采集的头显编排脚本（任务书 docs/任务书_VR微情感时序采集与情绪预测.md 的 M-A）。
// 一个脚本串起 A1–A4：
//   A1 高帧率采 70 维 FEA（OVRPlugin.FaceExpression2）+ 眼动/注视（默认 60Hz），入队 SessionUploader。
//   A2 刺激「实时下发」：运行时从后端 /api/stimulus/images 拉全景贴图设为 Skybox
//      （拉取失败回退 Resources/stimulus）。
//   A3 每张图后自评：7 类情绪 + SAM（valence/arousal 各 1–9）+ 确定，世界空间面板手柄射线点选。
//   A4 经 SessionUploader 批量 POST 到 PC 后端；会话起始做一次同步事件（屏幕闪白 + 记时间戳）。
//
// 场景前置（与 VREmotionSelectionUI 相同）：
//   1) OVRCameraRig + 挂 OVRFaceExpressions 的物体；OculusProjectConfig 的 Face Tracking = Supported。
//   2) 若要眼动：给 eyeGaze 挂 OVREyeGaze（留空则用中心眼朝向近似）。
//   3) Graphics → Always Included Shaders 加入 "Skybox/Panoramic"。
//   4) 场景里放一个 SessionUploader（配置 backendBase 指向 PC）。
// Quest Pro 隐私红线：拿不到内向摄像头原始人脸图，只有 70 维 blendshape。

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;
using UnityEngine.UI;

public class VRSessionCollector : MonoBehaviour
{
    // 受试者面向国际，只提供日语与英语。序号是场景序列化值，不可重排：
    // 原 1 是中文，现在同一序号即英文，旧场景无需迁移。
    public enum UiLanguage { Japanese = 0, English = 2 }

    [Header("引用（留空自动查找）")]
    public OVRFaceExpressions faceExpressions;
    public OVRCameraRig cameraRig;
    public SessionUploader uploader;
    public OVREyeGaze eyeGaze;                 // 可选：真实眼动；留空用中心眼朝向

    [Header("采集参数")]
    public string subjectId = "p01";
    public UiLanguage language = UiLanguage.Japanese;
    [Tooltip("FEA/gaze 采样帧率（微表情需高帧率，目标 60）")]
    public float feaRateHz = 60f;
    [Tooltip("从后端拉哪个情绪的刺激（留空=全部）")]
    public string stimulusEmotionFilter = "";
    [Tooltip("后端拉取失败时回退到 Resources/<stimulusFolder>/")]
    public string stimulusFolder = "stimulus";

    [Header("面板")]
    public float panelDistance = 2.2f;
    public float panelWidth = 2.4f;

    private static readonly string[] EmotionKeys =
        { "angry", "disgust", "fear", "happy", "neutral", "sad", "surprise" };
    private static readonly string[] LabelsJa =
        { "怒り", "嫌悪", "恐怖", "喜び", "中立", "悲しみ", "驚き" };
    private static readonly string[] LabelsEn =
        { "Angry", "Disgust", "Fear", "Happy", "Neutral", "Sad", "Surprise" };

    private static readonly Color Base = ModernUI.Surface;
    private static readonly Color Hover = ModernUI.Accent;
    private static readonly Color Selected = ModernUI.Success;

    private Material _skyboxMat;
    private Camera _eyeCamera;
    private LineRenderer _laser;

    private List<Texture2D> _stimuli = new List<Texture2D>();
    private List<string> _stimIds = new List<string>();
    private int _imgIndex = -1;

    private long _curOnset;
    private string _curImageId = "";
    private string _curTarget = "";

    private int _selEmotion = -1, _selValence = 0, _selArousal = 0;
    private bool _running;

    private class Btn { public Image img; public BoxCollider col; public string kind; public int idx; }
    private readonly List<Btn> _btns = new List<Btn>();
    private Text _statusText;

    private string[] Labels => language == UiLanguage.English ? LabelsEn : LabelsJa;

    private static long NowMs() => System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

    private void Start()
    {
        if (faceExpressions == null) faceExpressions = FindObjectOfType<OVRFaceExpressions>();
        if (cameraRig == null) cameraRig = FindObjectOfType<OVRCameraRig>();
        if (uploader == null) uploader = FindObjectOfType<SessionUploader>();
        if (cameraRig == null) { Debug.LogError("[Session] 缺 OVRCameraRig"); return; }
        if (uploader == null) { Debug.LogError("[Session] 缺 SessionUploader"); return; }
        _eyeCamera = cameraRig.centerEyeAnchor != null
            ? cameraRig.centerEyeAnchor.GetComponent<Camera>() : Camera.main;

        var sh = Shader.Find("Skybox/Panoramic");
        if (sh != null) { _skyboxMat = new Material(sh); RenderSettings.skybox = _skyboxMat; }
        else Debug.LogWarning("[Session] 缺 Skybox/Panoramic shader，全景不显示（采集仍正常）");

        StartCoroutine(RunSession());
    }

    private IEnumerator RunSession()
    {
        yield return uploader.StartSession(subjectId, feaRateHz);
        if (!uploader.Ready) { Debug.LogError("[Session] 会话启动失败，检查 backendBase"); yield break; }

        yield return SyncFlash();                       // A4 同步事件：闪白 + 记时间戳
        yield return LoadStimuli();                      // A2 实时下发（后端→回退 Resources）
        BuildPanel();                                    // A3 自评面板
        _running = true;
        StartCoroutine(SampleLoop());                    // A1 高帧率 FEA+gaze
        ShowNextStimulus();
    }

    // ── A4 同步事件：短暂闪白，记录统一时钟时间戳 ────────────────
    private IEnumerator SyncFlash()
    {
        long ts = NowMs();
        var go = new GameObject("SyncFlash");
        var canvas = go.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = _eyeCamera;
        var rect = go.GetComponent<RectTransform>();
        rect.sizeDelta = new Vector2(4000, 4000);
        Transform eye = cameraRig.centerEyeAnchor;
        go.transform.position = eye.position + eye.forward * 1.0f;
        go.transform.rotation = Quaternion.LookRotation(eye.forward, Vector3.up);
        go.transform.localScale = Vector3.one * 0.001f;
        var img = go.AddComponent<Image>();
        img.color = Color.white;
        Debug.Log($"[Session] 同步事件时间戳 = {ts}（Unity 侧与 PC 侧同一动作对齐时钟 offset 用）");
        yield return new WaitForSeconds(0.15f);
        Destroy(go);
    }

    // ── A1 高帧率采样 ────────────────────────────────────────────
    private IEnumerator SampleLoop()
    {
        int faceCount = (int)OVRFaceExpressions.FaceExpression.Max;   // 70
        var buf = new float[faceCount];
        var wait = new WaitForSeconds(1f / Mathf.Max(1f, feaRateHz));
        while (_running)
        {
            long ts = NowMs();
            if (faceExpressions != null && faceExpressions.ValidExpressions)
            {
                for (int i = 0; i < faceCount; i++)
                    buf[i] = faceExpressions[(OVRFaceExpressions.FaceExpression)i];
                uploader.AddFea(ts, buf);
            }
            Vector3 g = GazeDir();
            uploader.AddGaze(ts, g.x, g.y, g.z);
            yield return wait;
        }
    }

    private Vector3 GazeDir()
    {
        if (eyeGaze != null && eyeGaze.EyeTrackingEnabled)
            return eyeGaze.transform.forward;
        return cameraRig.centerEyeAnchor != null ? cameraRig.centerEyeAnchor.forward : Vector3.forward;
    }

    // ── A2 刺激实时下发：先后端，失败回退 Resources ───────────────
    private IEnumerator LoadStimuli()
    {
        string url = uploader.backendBase + "/api/stimulus/images?limit=200";
        if (!string.IsNullOrEmpty(stimulusEmotionFilter))
            url += "&emotion=" + UnityWebRequest.EscapeURL(stimulusEmotionFilter);

        using (var req = UnityWebRequest.Get(url))
        {
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
            {
                var list = JsonUtility.FromJson<StimList>(req.downloadHandler.text);
                if (list != null && list.images != null)
                {
                    foreach (var it in list.images)
                    {
                        yield return LoadTexture(uploader.backendBase + it.url, it.filename ?? it.path);
                    }
                }
            }
            else Debug.LogWarning($"[Session] 拉刺激列表失败：{req.error}，回退 Resources");
        }

        if (_stimuli.Count == 0)   // 回退
        {
            var texes = Resources.LoadAll<Texture2D>(stimulusFolder);
            foreach (var t in texes) { _stimuli.Add(t); _stimIds.Add(t.name); }
        }
        Debug.Log($"[Session] 载入刺激 {_stimuli.Count} 张");
    }

    private IEnumerator LoadTexture(string url, string id)
    {
        using (var req = UnityWebRequestTexture.GetTexture(url))
        {
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
            {
                _stimuli.Add(DownloadHandlerTexture.GetContent(req));
                _stimIds.Add(id);
            }
        }
    }

    private void ShowNextStimulus()
    {
        if (_stimuli.Count == 0) return;
        _imgIndex = (_imgIndex + 1) % _stimuli.Count;
        if (_skyboxMat != null) _skyboxMat.SetTexture("_MainTex", _stimuli[_imgIndex]);
        _curImageId = _stimIds[_imgIndex];
        _curTarget = ParseEmotion(_curImageId);
        _curOnset = NowMs();
        _selEmotion = -1; _selValence = 0; _selArousal = 0;
        Refresh();
    }

    private static string ParseEmotion(string imageId)
    {
        if (string.IsNullOrEmpty(imageId)) return "";
        string s = imageId.ToLower();
        foreach (var k in EmotionKeys) if (s.StartsWith(k)) return k;
        return "";
    }

    // ── A3 自评面板：7 情绪 + valence 1-9 + arousal 1-9 + 确定 ────
    private void BuildPanel()
    {
        Transform eye = cameraRig.centerEyeAnchor;
        Vector3 fwd = eye.forward; fwd.y = 0f;
        if (fwd.sqrMagnitude < 1e-4f) fwd = Vector3.forward;
        fwd.Normalize();

        var canvasGO = new GameObject("SessionSelfReportCanvas");
        var canvas = canvasGO.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = _eyeCamera;
        var canvasRect = canvasGO.GetComponent<RectTransform>();
        canvasRect.sizeDelta = new Vector2(2400f, 1100f);
        canvasGO.transform.localScale = Vector3.one * (panelWidth / 2400f);
        Vector3 pos = eye.position + fwd * panelDistance; pos.y = eye.position.y - 0.2f;
        canvasGO.transform.position = pos;
        canvasGO.transform.rotation = Quaternion.LookRotation(fwd, Vector3.up);

        string instr = language == UiLanguage.English
            ? "Look around, then pick emotion + valence + arousal and press Confirm"
            : "このパノラマを見て、感情・快不快・覚醒度を選んで「確定」";
        CreateText(canvasRect, instr, 40, new Vector2(0, 470), new Vector2(2300, 90), Color.white);

        // 7 情绪
        CreateText(canvasRect, language == UiLanguage.English ? "Emotion" : "感情",
            34, new Vector2(-1080, 330), new Vector2(220, 60), Color.white, TextAnchor.MiddleLeft);
        BuildRow(canvasRect, "emotion", 7, Labels, 300f, 210f, new Vector2(0, 250));

        // SAM valence 1-9
        CreateText(canvasRect, language == UiLanguage.English ? "Valence 1-9" : "快不快(1-9)",
            34, new Vector2(-1080, 60), new Vector2(320, 60), Color.white, TextAnchor.MiddleLeft);
        BuildScaleRow(canvasRect, "valence", new Vector2(0, -10));

        // SAM arousal 1-9
        CreateText(canvasRect, language == UiLanguage.English ? "Arousal 1-9" : "覚醒度(1-9)",
            34, new Vector2(-1080, -170), new Vector2(320, 60), Color.white, TextAnchor.MiddleLeft);
        BuildScaleRow(canvasRect, "arousal", new Vector2(0, -240));

        // 确定
        var confirm = BuildButton(canvasRect, "confirm", 0,
            language == UiLanguage.English ? "Confirm" : "確定",
            new Vector2(0, -430), new Vector2(360, 120));
        confirm.img.color = Base;

        _statusText = CreateText(canvasRect, "", 34, new Vector2(700, -430),
            new Vector2(1000, 100), new Color(0.35f, 0.9f, 0.45f));

        BuildLaser();
        Refresh();
    }

    private void BuildRow(RectTransform parent, string kind, int n, string[] labels,
        float btnW, float btnH, Vector2 center)
    {
        float spacing = 24f;
        float total = n * btnW + (n - 1) * spacing;
        float startX = -total / 2f + btnW / 2f;
        for (int i = 0; i < n; i++)
        {
            float x = startX + i * (btnW + spacing);
            string lbl = labels != null ? ModernUI.TwoLine(labels[i], EmotionKeys[i], 22) : (i + 1).ToString();
            BuildButton(parent, kind, i, lbl, new Vector2(center.x + x, center.y), new Vector2(btnW, btnH));
        }
    }

    private void BuildScaleRow(RectTransform parent, string kind, Vector2 center)
    {
        int n = 9; float btnW = 150f, btnH = 130f, spacing = 18f;
        float total = n * btnW + (n - 1) * spacing;
        float startX = -total / 2f + btnW / 2f;
        for (int i = 0; i < n; i++)
        {
            float x = startX + i * (btnW + spacing);
            BuildButton(parent, kind, i, (i + 1).ToString(),
                new Vector2(center.x + x, center.y), new Vector2(btnW, btnH));
        }
    }

    private Btn BuildButton(RectTransform parent, string kind, int idx, string label,
        Vector2 pos, Vector2 size)
    {
        var go = new GameObject($"Btn_{kind}_{idx}");
        go.transform.SetParent(parent, false);
        var rect = go.AddComponent<RectTransform>();
        rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
        rect.pivot = new Vector2(0.5f, 0.5f);
        rect.anchoredPosition = pos;
        rect.sizeDelta = size;
        var image = go.AddComponent<Image>();
        ModernUI.Round(image, 22f);
        image.color = Base;
        var col = go.AddComponent<BoxCollider>();
        col.size = new Vector3(size.x, size.y, 10f);

        var textGO = new GameObject("Label");
        textGO.transform.SetParent(go.transform, false);
        var tr = textGO.AddComponent<RectTransform>();
        tr.anchorMin = Vector2.zero; tr.anchorMax = Vector2.one;
        tr.offsetMin = Vector2.zero; tr.offsetMax = Vector2.zero;
        var text = textGO.AddComponent<Text>();
        text.text = label; text.font = ResolveFont(); text.fontSize = 32;
        text.alignment = TextAnchor.MiddleCenter; text.color = Color.white;

        var b = new Btn { img = image, col = col, kind = kind, idx = idx };
        _btns.Add(b);
        return b;
    }

    private Text CreateText(RectTransform parent, string content, int size, Vector2 pos,
        Vector2 dim, Color color, TextAnchor anchor = TextAnchor.MiddleCenter)
    {
        var go = new GameObject("Text");
        go.transform.SetParent(parent, false);
        var rect = go.AddComponent<RectTransform>();
        rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
        rect.pivot = new Vector2(0.5f, 0.5f);
        rect.anchoredPosition = pos; rect.sizeDelta = dim;
        var text = go.AddComponent<Text>();
        text.text = content; text.font = ResolveFont(); text.fontSize = size;
        text.alignment = anchor; text.color = color; text.horizontalOverflow = HorizontalWrapMode.Wrap;
        return text;
    }

    private void BuildLaser()
    {
        var go = new GameObject("Pointer");
        _laser = go.AddComponent<LineRenderer>();
        _laser.widthMultiplier = 0.005f; _laser.positionCount = 2; _laser.useWorldSpace = true;
        var sh = Shader.Find("Sprites/Default") ?? Shader.Find("Unlit/Color");
        if (sh != null) _laser.material = new Material(sh);
        _laser.startColor = _laser.endColor = new Color(0.4f, 0.7f, 1f, 0.9f);
        _laser.enabled = false;
    }

    // ── 交互 ──────────────────────────────────────────────────────
    private void Update()
    {
        if (_btns.Count == 0) return;

        bool ctrl = ControllerConnected();
        Ray ray; bool click;
        if (ctrl)
        {
            Transform aim = ActiveHand();
            if (aim == null) return;
            ray = new Ray(aim.position, aim.forward);
            click = OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger)
                 || OVRInput.GetDown(OVRInput.Button.SecondaryIndexTrigger)
                 || OVRInput.GetDown(OVRInput.Button.One);
        }
        else
        {
            if (_eyeCamera == null || Mouse.current == null) return;
            ray = _eyeCamera.ScreenPointToRay(Mouse.current.position.ReadValue());
            click = Mouse.current.leftButton.wasPressedThisFrame;
        }

        Btn hover = null;
        Vector3 hitPoint = ray.origin + ray.direction * 3f;
        if (Physics.Raycast(ray, out RaycastHit hit, 50f))
        {
            hitPoint = hit.point;
            foreach (var b in _btns) if (b.col == hit.collider) { hover = b; break; }
        }
        UpdateLaser(ctrl, ray.origin, hitPoint);
        Refresh(hover);

        if (click && hover != null) { ModernUI.Press(hover.img); Dispatch(hover); }
    }

    private void Dispatch(Btn b)
    {
        switch (b.kind)
        {
            case "emotion": _selEmotion = b.idx; break;
            case "valence": _selValence = b.idx + 1; break;
            case "arousal": _selArousal = b.idx + 1; break;
            case "confirm": Submit(); return;
        }
        Refresh();
    }

    private void Submit()
    {
        if (_selEmotion < 0 || _selValence == 0 || _selArousal == 0)
        {
            if (_statusText != null)
                _statusText.text = language == UiLanguage.English ? "Please select all three" : "全部選んでね";
            return;
        }
        long now = NowMs();
        uploader.AddSelfReport(now, _curImageId, EmotionKeys[_selEmotion], _selValence, _selArousal);
        uploader.AddStimulus(_curOnset, _curImageId, _curTarget, now, now - _curOnset);
        if (_statusText != null)
            _statusText.text = (language == UiLanguage.English ? "Recorded: " : "記録：") + Labels[_selEmotion];
        ShowNextStimulus();
    }

    private void Refresh(Btn hover = null)
    {
        foreach (var b in _btns)
        {
            bool sel = (b.kind == "emotion" && b.idx == _selEmotion)
                    || (b.kind == "valence" && b.idx + 1 == _selValence)
                    || (b.kind == "arousal" && b.idx + 1 == _selArousal);
            ModernUI.SetState(b.img, (b == hover) ? Hover : (sel ? Selected : Base), b == hover);
        }
    }

    private bool ControllerConnected()
    {
        var c = OVRInput.GetConnectedControllers();
        return (c & (OVRInput.Controller.RTouch | OVRInput.Controller.LTouch | OVRInput.Controller.Touch)) != 0;
    }

    private Transform ActiveHand()
    {
        var c = OVRInput.GetConnectedControllers();
        if ((c & OVRInput.Controller.RTouch) != 0 && cameraRig.rightHandAnchor != null) return cameraRig.rightHandAnchor;
        if ((c & OVRInput.Controller.LTouch) != 0 && cameraRig.leftHandAnchor != null) return cameraRig.leftHandAnchor;
        return cameraRig.rightHandAnchor != null ? cameraRig.rightHandAnchor : cameraRig.centerEyeAnchor;
    }

    private void UpdateLaser(bool show, Vector3 from, Vector3 to)
    {
        if (_laser == null) return;
        _laser.enabled = show;
        if (show) { _laser.SetPosition(0, from); _laser.SetPosition(1, to); }
    }

    private void OnDestroy()
    {
        _running = false;
        // 在 uploader 自己的对象上收尾（本对象正被销毁，协程不会跑）
        if (uploader != null && uploader.Ready) uploader.StartCoroutine(uploader.StopSession());
    }

    private static Font _cjkFont; private static bool _tried;
    private static Font ResolveFont()
    {
        if (!_tried)
        {
            _tried = true;
            try
            {
                _cjkFont = Font.CreateDynamicFontFromOSFont(new[]
                {
                    "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic",
                    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Droid Sans Fallback"
                }, 40);
            }
            catch { _cjkFont = null; }
        }
        return _cjkFont != null ? _cjkFont : Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
    }

    [System.Serializable] private class StimItem { public string url; public string filename; public string path; public string emotion; }
    [System.Serializable] private class StimList { public StimItem[] images; }
}
