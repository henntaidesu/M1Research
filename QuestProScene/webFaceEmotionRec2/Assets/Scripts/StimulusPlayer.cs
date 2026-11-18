// StimulusPlayer.cs
// 刺激库播放器：照后端 /api/stimlib/playback 的状态在头显里呈现刺激（后端 use_stimlib/playback.py）。
//
// **不需要挂到任何 GameObject**：用 [RuntimeInitializeOnLoadMethod] 自动实例化（同 VRStimulusControlPanel）。
// 空闲（mode=idle）时完全不碰天空盒，不影响 VREmotionSelectionUI 等原有流程；
// 网页一开始播放就接管天空盒，停止后还原。
//
// 阶段（后端决定，这里只照做）：
//   fixation / recovery   灰色全景 + 中央注视点
//   stimulus              图片 → 贴天空盒；视频 → VideoPlayer 渲染到 RenderTexture 再贴天空盒
//   rating                灰色全景 + SAM 自评面板（效价 1–9、唤醒 1–9、7 类情绪可选）
//   done                  灰色全景 + 结束语
//
// 缓存：刺激先下载到 persistentDataPath/stimcache/<id>_<sha>.<ext> 再播放（外网下视频几十 MB，
// 不能边下边播）。后端给出 prefetch 列表，空闲时按顺序预下载；sha 变了自动重新下载。
// 每秒回报一次：当前刺激、播放状态、播放位置、已缓存列表。后端要等到这里回报 playing/shown
// 才开始给刺激阶段计时，保证受试者实际看到的时长一致。

using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;
using UnityEngine.UI;
using UnityEngine.Video;

public class StimulusPlayer : MonoBehaviour
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Bootstrap()
    {
        var go = new GameObject("~StimulusPlayer");
        go.AddComponent<StimulusPlayer>();
        DontDestroyOnLoad(go);
    }

    [Tooltip("轮询后端播放状态的间隔（秒）")]
    public float pollSeconds = 0.3f;
    [Tooltip("灰屏亮度（0–1）。注视与恢复阶段的背景")]
    public float grayLevel = 0.5f;
    [Tooltip("视频是否输出声音（当前生成的视频没有音轨）")]
    public bool videoAudio = false;
    public float panelDistance = 2.0f;
    public float panelWidth = 2.2f;

    private string Base => BackendConfig.BaseUrl;

    // ── 后端状态（JsonUtility：null 的嵌套对象会变成字段全空的实例，用 id 判空）──
    [System.Serializable] private class Stim { public string id; public string pair_id; public string line; public string media; public string url; public string sha; public string condition; }
    [System.Serializable] private class SeekInfo { public float pos_s; public int ver; }
    [System.Serializable] private class RunInfo { public string run_id; public int index; public int total; }
    [System.Serializable]
    private class PbState
    {
        public int version; public string mode; public string phase; public Stim stimulus; public bool paused;
        public SeekInfo seek; public bool waiting_headset; public string lang; public Stim[] prefetch; public RunInfo run;
    }

    private static readonly string[] EmotionKeys = { "angry", "disgust", "fear", "happy", "neutral", "sad", "surprise" };
    private static readonly string[] LabelsZh = { "愤怒", "厌恶", "恐惧", "开心", "平静", "悲伤", "惊讶" };
    private static readonly string[] LabelsJa = { "怒り", "嫌悪", "恐怖", "喜び", "中立", "悲しみ", "驚き" };
    private static readonly string[] LabelsEn = { "Angry", "Disgust", "Fear", "Happy", "Neutral", "Sad", "Surprise" };

    private OVRCameraRig _rig;
    private Camera _eyeCamera;
    private Material _skyboxMat;
    private Texture _origSkyTex;
    private Material _origSkybox;
    private bool _active;

    private Texture2D _gray;
    private Texture2D _imageTex;
    private VideoPlayer _vp;
    private RenderTexture _rt;

    private PbState _st;
    private string _lang = "zh";
    private string _curId = "";          // 正在呈现（或正在加载）的刺激
    private string _hsState = "";        // 回报给后端的状态
    private string _hsError = "";
    private int _seekVer = -1;
    private bool _loading;
    private float _errorAt;

    private readonly HashSet<string> _cached = new HashSet<string>();
    private string _downloadingId = "";
    private string _cacheDir;

    // ── UI ──
    private GameObject _fixRoot;
    private Text _fixText;
    private GameObject _ratingRoot;
    private Text _ratingTitle, _ratingStatus;
    private readonly List<Btn> _btns = new List<Btn>();
    private LineRenderer _laser;
    private int _selV, _selA, _selE = -1;
    private string _ratingFor = "";
    private float _ratingShownAt;
    private bool _submitting;

    private class Btn { public Image img; public BoxCollider col; public string kind; public int idx; public Text label; }

    private static readonly Color BtnBase = ModernUI.Surface;
    private static readonly Color BtnHover = ModernUI.Accent;
    private static readonly Color BtnSel = ModernUI.Success;

    private void Start()
    {
        _cacheDir = Path.Combine(Application.persistentDataPath, "stimcache");
        Directory.CreateDirectory(_cacheDir);
        _gray = new Texture2D(4, 2, TextureFormat.RGB24, false);
        var g = new Color(grayLevel, grayLevel, grayLevel);
        for (int x = 0; x < 4; x++) for (int y = 0; y < 2; y++) _gray.SetPixel(x, y, g);
        _gray.Apply();

        _vp = gameObject.AddComponent<VideoPlayer>();
        _vp.playOnAwake = false;
        _vp.isLooping = true;            // 时长由后端控制；文件本身已按设定时长循环编码，这里再兜底
        _vp.waitForFirstFrame = true;
        _vp.skipOnDrop = true;
        _vp.source = VideoSource.Url;
        _vp.renderMode = VideoRenderMode.RenderTexture;
        _vp.audioOutputMode = videoAudio ? VideoAudioOutputMode.Direct : VideoAudioOutputMode.None;
        _vp.errorReceived += (src, msg) => { _hsError = "video: " + msg; Fail(msg); Debug.LogWarning("[StimPlayer] " + msg); };

        StartCoroutine(PollLoop());
        StartCoroutine(ReportLoop());
        StartCoroutine(PrefetchLoop());
    }

    private bool EnsureRig()
    {
        if (_rig != null) return true;
        _rig = FindFirstObjectByType<OVRCameraRig>();
        if (_rig == null) return false;
        _eyeCamera = _rig.centerEyeAnchor != null ? _rig.centerEyeAnchor.GetComponent<Camera>() : Camera.main;
        BuildUi();
        return true;
    }

    // ── 轮询后端 ────────────────────────────────────────────────────
    private IEnumerator PollLoop()
    {
        var wait = new WaitForSeconds(Mathf.Max(0.1f, pollSeconds));
        while (true)
        {
            using (var req = UnityWebRequest.Get(Base + "/api/stimlib/playback?client=vr"))
            {
                req.timeout = 5;
                yield return req.SendWebRequest();
                if (req.result == UnityWebRequest.Result.Success)
                {
                    PbState st = null;
                    try { st = JsonUtility.FromJson<PbState>(req.downloadHandler.text); } catch { st = null; }
                    if (st != null) Apply(st);
                }
            }
            yield return wait;
        }
    }

    private static bool Has(Stim s) => s != null && !string.IsNullOrEmpty(s.id);

    private void Apply(PbState st)
    {
        _st = st;
        if (!string.IsNullOrEmpty(st.lang)) _lang = st.lang;

        if (st.mode == "idle" && (st.phase == "idle" || string.IsNullOrEmpty(st.phase)))
        {
            Release();
            return;
        }
        if (!EnsureRig()) return;
        Takeover();

        switch (st.phase)
        {
            case "stimulus":
                HideRating();                   // 注视点 / 「加载中」提示由 LoadAndShow 自己管
                if (Has(st.stimulus))
                {
                    // 下载 / 准备失败的同一刺激，5 秒后重试（外网偶发断线不该卡死整个试次）
                    bool retry = st.stimulus.id == _curId && _hsState == "error" && !_loading
                                 && Time.realtimeSinceStartup - _errorAt > 5f;
                    if (st.stimulus.id != _curId || retry) StartCoroutine(LoadAndShow(st.stimulus));
                    else ApplyPauseSeek(st);
                }
                break;
            case "rating":
                StopMedia();
                SetSky(_gray);
                ShowFixation(false, "");
                if (Has(st.stimulus)) ShowRating(st.stimulus.id);
                break;
            case "done":
                StopMedia();
                SetSky(_gray);
                HideRating();
                ShowFixation(true, L("实验结束，谢谢参与！", "実験は終了しました。ご協力ありがとうございました！", "All done. Thank you!"));
                break;
            default:   // fixation / recovery / rating_done
                StopMedia();
                SetSky(_gray);
                HideRating();
                ShowFixation(true, "+");
                break;
        }
    }

    // ── 天空盒接管 / 还原 ──────────────────────────────────────────
    private void Takeover()
    {
        if (_active) return;
        _active = true;
        _origSkybox = RenderSettings.skybox;
        var mat = Resources.Load<Material>("PanoramaSkybox");
        if (mat != null) _skyboxMat = new Material(mat);
        else
        {
            var sh = Shader.Find("Skybox/Panoramic");
            if (sh != null)
            {
                _skyboxMat = new Material(sh);
                if (_skyboxMat.HasProperty("_Mapping")) _skyboxMat.SetFloat("_Mapping", 1f);
                _skyboxMat.EnableKeyword("_MAPPING_LATITUDE_LONGITUDE_LAYOUT");
            }
        }
        if (_skyboxMat != null) RenderSettings.skybox = _skyboxMat;
        if (_eyeCamera != null) _eyeCamera.clearFlags = CameraClearFlags.Skybox;
        Debug.Log("[StimPlayer] 接管天空盒");
    }

    private void Release()
    {
        if (!_active) return;
        _active = false;
        StopMedia();
        HideRating();
        ShowFixation(false, "");
        if (_origSkybox != null) RenderSettings.skybox = _origSkybox;
        _curId = "";
        _hsState = "idle";
        Debug.Log("[StimPlayer] 播放结束，还原天空盒");
    }

    private void SetSky(Texture tex)
    {
        if (_skyboxMat == null || tex == null) return;
        if (_skyboxMat.HasProperty("_MainTex")) _skyboxMat.SetTexture("_MainTex", tex);
        if (_skyboxMat.HasProperty("_Tex")) _skyboxMat.SetTexture("_Tex", tex);
    }

    private void StopMedia()
    {
        if (_vp != null && (_vp.isPlaying || _vp.isPrepared)) _vp.Stop();
        _curId = "";
        if (_hsState != "error") _hsState = "";
    }

    // ── 加载并呈现 ──────────────────────────────────────────────────
    private IEnumerator LoadAndShow(Stim s)
    {
        _hsState = "";
        StopMedia();
        _curId = s.id;
        _hsState = "loading";
        _hsError = "";
        SetSky(_gray);
        ShowFixation(true, L("加载中…", "読み込み中…", "Loading…"));

        string path = CachePath(s);
        _loading = true;                                 // 预缓存让路给当前要播的刺激
        if (!IsCached(s, path))
        {
            while (!string.IsNullOrEmpty(_downloadingId) && _downloadingId != s.id) yield return null;
            if (!IsCached(s, path)) yield return Download(s, path);
        }
        _loading = false;
        if (_curId != s.id) yield break;                // 加载期间已经切走了
        if (!File.Exists(path)) { Fail("download failed"); yield break; }

        ShowFixation(false, "");
        if (s.media == "video")
        {
            _vp.url = path;
            _vp.Prepare();
            float t0 = Time.realtimeSinceStartup;
            while (!_vp.isPrepared && _hsState != "error" && Time.realtimeSinceStartup - t0 < 30f) yield return null;
            if (_curId != s.id) yield break;
            if (!_vp.isPrepared) { Fail("video prepare timeout"); yield break; }
            if (_rt == null || _rt.width != (int)_vp.width || _rt.height != (int)_vp.height)
            {
                if (_rt != null) _rt.Release();
                _rt = new RenderTexture((int)_vp.width, (int)_vp.height, 0, RenderTextureFormat.ARGB32);
                _rt.wrapModeU = TextureWrapMode.Repeat;     // 经度方向环绕，配合 shader 消除接缝
                _rt.wrapModeV = TextureWrapMode.Clamp;
                _rt.Create();
            }
            _vp.targetTexture = _rt;
            SetSky(_rt);
            _seekVer = _st != null && _st.seek != null ? _st.seek.ver : -1;
            if (_st != null && _st.paused) { _vp.Pause(); _hsState = "paused"; }
            else { _vp.Play(); _hsState = "playing"; }
        }
        else
        {
            var tex = new Texture2D(2, 2, TextureFormat.RGB24, true);
            tex.LoadImage(File.ReadAllBytes(path));
            tex.wrapModeU = TextureWrapMode.Repeat;
            tex.wrapModeV = TextureWrapMode.Clamp;
            tex.filterMode = FilterMode.Trilinear;
            if (_imageTex != null) Destroy(_imageTex);
            _imageTex = tex;
            SetSky(tex);
            _hsState = "shown";
        }
        StartCoroutine(ReportOnce());
        Debug.Log($"[StimPlayer] 呈现 {s.id}（{s.media}）");
    }

    private void Fail(string why)
    {
        _hsState = "error";
        if (string.IsNullOrEmpty(_hsError)) _hsError = why;
        _errorAt = Time.realtimeSinceStartup;
        ShowFixation(true, L("加载失败，正在重试…", "読み込みに失敗しました。再試行中…", "Load failed, retrying…"));
    }

    private void ApplyPauseSeek(PbState st)
    {
        if (_vp == null || !_vp.isPrepared || _st?.stimulus?.media != "video") return;
        if (st.seek != null && st.seek.ver != _seekVer)
        {
            _seekVer = st.seek.ver;
            _vp.time = st.seek.pos_s;
        }
        if (st.paused && _vp.isPlaying) { _vp.Pause(); _hsState = "paused"; }
        else if (!st.paused && !_vp.isPlaying) { _vp.Play(); _hsState = "playing"; }
    }

    // ── 缓存 ────────────────────────────────────────────────────────
    private string CachePath(Stim s)
    {
        string ext = s.media == "video" ? ".mp4" : ".jpg";
        string safe = s.id.Replace(":", "_").Replace("/", "_");
        string sha = string.IsNullOrEmpty(s.sha) ? "nosha" : s.sha;
        return Path.Combine(_cacheDir, safe + "_" + sha + ext);
    }

    private bool IsCached(Stim s, string path)
    {
        bool ok = File.Exists(path) && new FileInfo(path).Length > 0;
        if (ok) _cached.Add(s.id);
        return ok;
    }

    private IEnumerator Download(Stim s, string path)
    {
        _downloadingId = s.id;
        string tmp = path + ".part";
        using (var req = new UnityWebRequest(Base + s.url, "GET"))
        {
            req.downloadHandler = new DownloadHandlerFile(tmp) { removeFileOnAbort = true };
            req.timeout = 900;
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
            {
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);
                _cached.Add(s.id);
                Debug.Log($"[StimPlayer] 已缓存 {s.id}");
            }
            else
            {
                _hsError = $"download {s.id}: {req.error}";
                Debug.LogWarning("[StimPlayer] " + _hsError);
            }
        }
        _downloadingId = "";
    }

    private IEnumerator PrefetchLoop()
    {
        var wait = new WaitForSeconds(1f);
        while (true)
        {
            var list = _st?.prefetch;
            if (list != null && string.IsNullOrEmpty(_downloadingId) && !_loading)
            {
                foreach (var s in list)
                {
                    if (!Has(s)) continue;
                    string p = CachePath(s);
                    if (IsCached(s, p)) continue;
                    yield return Download(s, p);
                    break;
                }
            }
            yield return wait;
        }
    }

    // ── 回报 ────────────────────────────────────────────────────────
    private IEnumerator ReportLoop()
    {
        var wait = new WaitForSeconds(1f);
        while (true)
        {
            yield return ReportOnce();
            yield return wait;
        }
    }

    private IEnumerator ReportOnce()
    {
        float pos = (_vp != null && _vp.isPrepared) ? (float)_vp.time : 0f;
        var sb = new StringBuilder();
        sb.Append("{\"stimulus_id\":\"").Append(Esc(_curId)).Append("\",\"state\":\"").Append(Esc(_hsState))
          .Append("\",\"position_s\":").Append(pos.ToString(System.Globalization.CultureInfo.InvariantCulture))
          .Append(",\"error\":\"").Append(Esc(_hsError)).Append("\",\"cached_ids\":[");
        bool first = true;
        foreach (var id in _cached) { if (!first) sb.Append(','); sb.Append('"').Append(Esc(id)).Append('"'); first = false; }
        sb.Append("]}");
        yield return Post("/api/stimlib/playback/status", sb.ToString(), null);
    }

    private IEnumerator Post(string path, string json, System.Action<long, string> done)
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

    private static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");

    private string L(string zh, string ja, string en) => _lang == "ja" ? ja : (_lang == "en" ? en : zh);

    // ── UI：注视点 / 提示 ───────────────────────────────────────────
    private void BuildUi()
    {
        _fixRoot = new GameObject("StimPlayerFixation");
        var c = _fixRoot.AddComponent<Canvas>();
        c.renderMode = RenderMode.WorldSpace;
        c.worldCamera = _eyeCamera;
        var r = _fixRoot.GetComponent<RectTransform>();
        r.sizeDelta = new Vector2(1600, 300);
        _fixRoot.transform.localScale = Vector3.one * 0.001f;
        _fixText = MakeText(r, "+", 120, Vector2.zero, new Vector2(1600, 300), Color.white);
        _fixRoot.SetActive(false);

        BuildRatingPanel();
        var lgo = new GameObject("StimPlayerPointer");
        _laser = lgo.AddComponent<LineRenderer>();
        _laser.widthMultiplier = 0.005f; _laser.positionCount = 2; _laser.useWorldSpace = true;
        var sh = Shader.Find("Sprites/Default") ?? Shader.Find("Unlit/Color");
        if (sh != null) _laser.material = new Material(sh);
        _laser.startColor = _laser.endColor = new Color(0.4f, 0.7f, 1f, 0.9f);
        _laser.enabled = false;
    }

    private void PlaceInFront(GameObject go, float dist, float yOffset)
    {
        Transform eye = _rig.centerEyeAnchor;
        Vector3 fwd = eye.forward; fwd.y = 0f;
        if (fwd.sqrMagnitude < 1e-4f) fwd = Vector3.forward;
        fwd.Normalize();
        go.transform.position = eye.position + fwd * dist + Vector3.up * yOffset;
        go.transform.rotation = Quaternion.LookRotation(fwd, Vector3.up);
    }

    private void ShowFixation(bool show, string text)
    {
        if (_fixRoot == null) return;
        if (show)
        {
            _fixText.text = text;
            _fixText.fontSize = text == "+" ? 160 : 64;
            if (!_fixRoot.activeSelf) PlaceInFront(_fixRoot, 2.0f, 0f);
        }
        _fixRoot.SetActive(show);
    }

    // ── UI：SAM 自评面板 ────────────────────────────────────────────
    private void BuildRatingPanel()
    {
        _ratingRoot = new GameObject("StimPlayerRating");
        var canvas = _ratingRoot.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = _eyeCamera;
        var rect = _ratingRoot.GetComponent<RectTransform>();
        rect.sizeDelta = new Vector2(2400f, 1150f);
        _ratingRoot.transform.localScale = Vector3.one * (panelWidth / 2400f);

        var bg = new GameObject("Bg");
        bg.transform.SetParent(rect, false);
        var bgr = bg.AddComponent<RectTransform>();
        bgr.anchorMin = Vector2.zero; bgr.anchorMax = Vector2.one; bgr.offsetMin = bgr.offsetMax = Vector2.zero;
        var bgi = bg.AddComponent<Image>();
        ModernUI.Round(bgi, 40f);
        bgi.color = new Color(0.06f, 0.07f, 0.09f, 0.85f);

        _ratingTitle = MakeText(rect, "", 42, new Vector2(0, 490), new Vector2(2300, 90), Color.white);
        MakeText(rect, "", 34, new Vector2(-1040, 340), new Vector2(300, 60), Color.white, TextAnchor.MiddleLeft).name = "LblV";
        Row(rect, "valence", 9, new Vector2(0, 260));
        MakeText(rect, "", 34, new Vector2(-1040, 110), new Vector2(300, 60), Color.white, TextAnchor.MiddleLeft).name = "LblA";
        Row(rect, "arousal", 9, new Vector2(0, 30));
        MakeText(rect, "", 34, new Vector2(-1040, -120), new Vector2(600, 60), Color.white, TextAnchor.MiddleLeft).name = "LblE";
        Row(rect, "emotion", 7, new Vector2(0, -200));
        var ok = MakeButton(rect, "confirm", 0, "", new Vector2(0, -400), new Vector2(380, 120));
        ok.label.name = "LblOk";
        _ratingStatus = MakeText(rect, "", 32, new Vector2(760, -400), new Vector2(900, 100), new Color(1f, 0.75f, 0.4f));
        _ratingRoot.SetActive(false);
    }

    private void Row(RectTransform parent, string kind, int n, Vector2 center)
    {
        float w = kind == "emotion" ? 290f : 170f, h = 120f, sp = 20f;
        float total = n * w + (n - 1) * sp;
        float x0 = -total / 2f + w / 2f;
        for (int i = 0; i < n; i++)
            MakeButton(parent, kind, i, kind == "emotion" ? "" : (i + 1).ToString(),
                new Vector2(center.x + x0 + i * (w + sp), center.y), new Vector2(w, h));
    }

    private Btn MakeButton(RectTransform parent, string kind, int idx, string label, Vector2 pos, Vector2 size)
    {
        var go = new GameObject($"Btn_{kind}_{idx}");
        go.transform.SetParent(parent, false);
        var r = go.AddComponent<RectTransform>();
        r.anchoredPosition = pos; r.sizeDelta = size;
        var img = go.AddComponent<Image>();
        ModernUI.Round(img, 22f);
        img.color = BtnBase;
        var col = go.AddComponent<BoxCollider>();
        col.size = new Vector3(size.x, size.y, 10f);
        var t = MakeText(r, label, 36, Vector2.zero, size, Color.white);
        var b = new Btn { img = img, col = col, kind = kind, idx = idx, label = t };
        _btns.Add(b);
        return b;
    }

    private Text MakeText(RectTransform parent, string s, int size, Vector2 pos, Vector2 dim, Color color,
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

    private void ShowRating(string stimId)
    {
        if (_ratingRoot == null) return;
        if (_ratingRoot.activeSelf && _ratingFor == stimId) return;
        _ratingFor = stimId;
        _selV = 0; _selA = 0; _selE = -1; _submitting = false;
        string[] labels = _lang == "ja" ? LabelsJa : (_lang == "en" ? LabelsEn : LabelsZh);
        _ratingTitle.text = L("刚才的画面让你感觉如何？", "今の映像でどう感じましたか？", "How did the scene make you feel?");
        _ratingStatus.text = "";
        foreach (Transform ch in _ratingRoot.transform)
        {
            var t = ch.GetComponent<Text>();
            if (t == null) continue;
            if (ch.name == "LblV") t.text = L("愉快度  1 不愉快 … 9 愉快", "快・不快  1 不快 … 9 快", "Valence  1 unpleasant … 9 pleasant");
            if (ch.name == "LblA") t.text = L("唤醒度  1 平静 … 9 激动", "覚醒度  1 落ち着き … 9 興奮", "Arousal  1 calm … 9 excited");
            if (ch.name == "LblE") t.text = L("最接近的情绪（可不选）", "最も近い感情（任意）", "Closest emotion (optional)");
        }
        foreach (var b in _btns)
        {
            if (b.kind == "emotion") b.label.text = labels[b.idx];
            if (b.kind == "confirm") b.label.text = L("确定", "確定", "Confirm");
        }
        PlaceInFront(_ratingRoot, panelDistance, -0.15f);
        _ratingRoot.SetActive(true);
        _ratingShownAt = Time.realtimeSinceStartup;
        RefreshButtons(null);
    }

    private void HideRating()
    {
        if (_ratingRoot != null && _ratingRoot.activeSelf) _ratingRoot.SetActive(false);
        _ratingFor = "";
        if (_laser != null) _laser.enabled = false;
    }

    private void Update()
    {
        if (_ratingRoot == null || !_ratingRoot.activeSelf) return;

        bool ctrl = (OVRInput.GetConnectedControllers() & (OVRInput.Controller.RTouch | OVRInput.Controller.LTouch)) != 0;
        Ray ray; bool click;
        if (ctrl)
        {
            var c = OVRInput.GetConnectedControllers();
            Transform aim = (c & OVRInput.Controller.RTouch) != 0 ? _rig.rightHandAnchor : _rig.leftHandAnchor;
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

        // RaycastAll：场景里可能还有别的面板挡在射线上，只认本面板的按钮
        Btn hover = null;
        Vector3 end = ray.origin + ray.direction * 3f;
        float best = float.MaxValue;
        foreach (var hit in Physics.RaycastAll(ray, 50f))
        {
            foreach (var b in _btns)
            {
                if (b.col == hit.collider && hit.distance < best) { best = hit.distance; hover = b; end = hit.point; }
            }
        }
        if (_laser != null)
        {
            _laser.enabled = ctrl;
            if (ctrl) { _laser.SetPosition(0, ray.origin); _laser.SetPosition(1, end); }
        }
        RefreshButtons(hover);
        if (click && hover != null) { ModernUI.Press(hover.img); OnButton(hover); }
    }

    private void OnButton(Btn b)
    {
        switch (b.kind)
        {
            case "valence": _selV = b.idx + 1; break;
            case "arousal": _selA = b.idx + 1; break;
            case "emotion": _selE = _selE == b.idx ? -1 : b.idx; break;
            case "confirm": Submit(); return;
        }
        RefreshButtons(b);
    }

    private void RefreshButtons(Btn hover)
    {
        foreach (var b in _btns)
        {
            bool sel = (b.kind == "valence" && b.idx + 1 == _selV) || (b.kind == "arousal" && b.idx + 1 == _selA)
                    || (b.kind == "emotion" && b.idx == _selE);
            ModernUI.SetState(b.img, b == hover ? BtnHover : (sel ? BtnSel : BtnBase), b == hover);
        }
    }

    private void Submit()
    {
        if (_submitting) return;
        if (_selV == 0 || _selA == 0)
        {
            _ratingStatus.text = L("请选择愉快度和唤醒度", "快・不快と覚醒度を選んでください", "Please select valence and arousal");
            return;
        }
        _submitting = true;
        int ms = Mathf.RoundToInt((Time.realtimeSinceStartup - _ratingShownAt) * 1000f);
        string label = _selE >= 0 ? EmotionKeys[_selE] : "";
        string json = "{\"stimulus_id\":\"" + Esc(_ratingFor) + "\",\"valence\":" + _selV + ",\"arousal\":" + _selA
                    + ",\"label7\":\"" + label + "\",\"rating_ms\":" + ms + "}";
        StartCoroutine(Post("/api/stimlib/rating", json, (code, text) =>
        {
            _submitting = false;
            if (code == 200)
            {
                _ratingStatus.text = L("已记录", "記録しました", "Recorded");
                HideRating();
            }
            else
            {
                _ratingStatus.text = L("提交失败，请重试", "送信に失敗しました。もう一度", "Failed, please retry");
                Debug.LogWarning($"[StimPlayer] 自评提交失败 [{code}] {text}");
            }
        }));
    }

    private void OnDestroy()
    {
        if (_rt != null) _rt.Release();
    }
}
