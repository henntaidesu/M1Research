// StimulusPlayer.cs
// 刺激库播放器：照后端 /api/stimlib/playback 的状态在头显里呈现刺激（后端 use_stimlib/playback.py）。
//
// **不需要挂到任何 GameObject**：用 [RuntimeInitializeOnLoadMethod] 自动实例化（同 VRStimulusControlPanel）。
// 空闲（mode=idle）时完全不碰天空盒，不影响 VREmotionSelectionUI 等原有流程；
// 网页一开始播放就接管天空盒，停止后还原。
//
// 本脚本只管：轮询后端、对时、FEA 采集上传、状态回报、阶段分发、注视 / 表演 / 休息 / 结束提示、
// 灰屏亮度匹配和全景朝向。具体刺激怎么呈现交给 Assets/Scripts/Stimulus/ 下的呈现器（契约 v2）：
//   skybox_image  SkyboxImagePresenter   全景图贴天空盒
//   skybox_video  SkyboxVideoPresenter   全景视频经 RenderTexture 贴天空盒（stim.audio 时带声音）
//   mimicry       MimicryClipPresenter   中性灰背景 + 正前方 2 m 一块平面屏幕放模仿片段
//   scene         ScenePrefabPresenter   APK 内置 3D 场景（StimulusSceneRegistry），按 events[] 触发
// 旧格式刺激（没有 kind）按 media 推成 skybox_image / skybox_video，行为与以前相同；
// 不认识的 kind 直接报错（status.error），5 秒后重试仍会报同样的错。
//
// 阶段（后端决定，这里只照做）：
//   fixation / recovery   灰色全景 + 中央注视点；fixation 时若 upcoming 是 scene 就提前实例化（不激活）
//   stimulus              交给呈现器
//   rating                灰色全景 + 自评面板（RatingPanel；stimulus.rating = full SAM / quick 网格 / none 不显示）
//   posed / posed_relax   表演校准：提示「请做出 X 的表情并保持」/「放松」
//   break                 休息提示
//   done                  灰色全景 + 结束语
//
// 亮度匹配：注视 / 自评 / 恢复的灰屏亮度 = 即将呈现（或刚呈现）图片的平均亮度，避免刺激出现时的
// 明暗突变引起眨眼、眯眼（Eyes_Closed / Lid_Tightener 会把它当成表情记下来）。视频 / 场景暂用 grayLevel。
// 朝向：刺激呈现时把全景正中（或场景 / 屏幕的正前方）转到受试者当前的头部朝向，保证主要内容在正前方。
//
// 缓存：AssetCache（persistentDataPath/stimcache/<id>_<sha>.<ext>），按后端 prefetch 列表空闲时预下载。
// 每秒回报一次：当前刺激、kind、播放状态、播放位置、onset_headset_ms、已缓存列表。后端要等到这里回报
// playing/shown 才开始给刺激阶段计时；onset_headset_ms 是受试者第一次看到刺激那一帧的头显时间，
// 后端用 clock_offset_ms 换算成服务器时钟作为 onset（比「收到回报的时刻」少掉一段网络延迟）。
// 打点事件（场景惊吓、片段播完、视频卡顿）由 EventReporter 每 0.5 秒批量 POST /api/stimlib/events。
//
// FEA：本脚本自己以 60Hz 读 OVRFaceExpressions 的 70 维 + 上 / 下半脸置信度，每秒批量 POST /api/stimlib/fea。
// 不再依赖 QuestProFaceCapture 每帧一个请求（外网下 30 个请求/秒很容易丢）。
// 时钟：每次轮询记往返时间，服务器在响应里带 server_ms；取最近 20 次中往返最短那次估计
// 偏移 = server_ms − (发出+收到)/2，随 FEA 一起上传，后端据此把每帧换算到服务器时钟，
// 与刺激事件对齐（头显系统时钟与服务器不同步，直接比会差几百毫秒到几秒）。

using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

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
    [Tooltip("视频是否一律输出声音（否则只在 stimulus.audio = true 时出声）")]
    public bool videoAudio = false;
    public float panelDistance = 2.0f;
    public float panelWidth = 2.2f;

    private string Base => BackendConfig.BaseUrl;

    // ── 后端状态（JsonUtility：null 的嵌套对象会变成字段全空的实例，用 id 判空）──
    [System.Serializable] private class SeekInfo { public float pos_s; public int ver; }
    [System.Serializable] private class RunInfo { public string run_id; public int index; public int total; }
    [System.Serializable] private class Cue { public string emotion; public int rep; public int i; public int n; }
    [System.Serializable] private class RatingOpts { public bool require_label7; public bool ask_intensity; }
    [System.Serializable]
    private class PbState
    {
        public int version; public string mode; public string phase; public StimSpec stimulus; public bool paused;
        public SeekInfo seek; public bool waiting_headset; public string lang; public StimSpec[] prefetch; public RunInfo run;
        public long server_ms; public Cue cue; public RatingOpts rating_opts; public StimSpec upcoming;
    }

    [Tooltip("FEA 采样率（Hz）")]
    public float feaRateHz = 60f;

    // ── FEA 采集 + 对时 ──
    private OVRFaceExpressions _face;
    private float _feaNextAt;
    private float _feaLastValidAt = -10f;
    private readonly List<string> _feaRows = new List<string>();
    private const int FeaBacklogMax = 600;           // 上传失败时最多积压 10 秒，防止内存无限增长
    private readonly Queue<long[]> _sync = new Queue<long[]>();   // [rtt, offset]
    private long _clockOffset;
    private bool _clockKnown;

    private static long NowMs() => StimNet.NowMs();

    private OVRCameraRig _rig;
    private Camera _eyeCamera;
    private Material _skyboxMat;
    private Material _origSkybox;
    private bool _active;

    private Texture2D _gray;
    private float _grayNow = -1f;
    private float _lastLuma = -1f;                    // 最近一次呈现的图片平均亮度（-1 = 视频 / 场景）
    private readonly Dictionary<string, float> _luma = new Dictionary<string, float>();

    private PbState _st;
    private string _lang = "zh";
    private string _curId = "";          // 正在呈现（或正在加载）的刺激
    private string _curKind = "";
    private string _hsState = "";        // 回报给后端的状态
    private string _hsError = "";
    private int _seekVer = -1;
    private bool _loading;
    private int _loadSeq;
    private float _errorAt;

    // ── 呈现器 ──
    private AssetCache _cache;
    private EventReporter _events;
    private IStimulusPresenter _cur;     // 当前刺激的呈现器
    private StimContext _curCtx;
    private bool _curBegun;
    private long _onsetMs;               // 当前刺激的 onset（头显时钟），0 = 还没有
    private float _onsetAt = -1f;        // 同一时刻的 realtimeSinceStartup
    private bool _completeSent;
    private IStimulusPresenter _pre;     // 注视阶段按 upcoming 提前准备好的 scene
    private StimContext _preCtx;
    private string _preId = "";

    // ── UI ──
    private GameObject _fixRoot;
    private Text _fixText;
    private RatingPanel _rating;

    private void Start()
    {
        _cache = new AssetCache(Path.Combine(Application.persistentDataPath, "stimcache"));
        _cache.OnError = e => _hsError = e;
        _events = new EventReporter();
        _gray = new Texture2D(4, 2, TextureFormat.RGB24, false);
        SetGrayLevel(grayLevel);

        StartCoroutine(PollLoop());
        StartCoroutine(ReportLoop());
        StartCoroutine(_cache.PrefetchLoop(() => _st?.prefetch));
        StartCoroutine(FeaUploadLoop());
        StartCoroutine(_events.Loop(() => _clockKnown ? _clockOffset : (long?)null));
    }

    // ── FEA 采集 ────────────────────────────────────────────────────
    private void SampleFea()
    {
        if (Time.realtimeSinceStartup < _feaNextAt) return;
        _feaNextAt = Time.realtimeSinceStartup + 1f / Mathf.Max(1f, feaRateHz);
        if (_face == null)
        {
            _face = FindFirstObjectByType<OVRFaceExpressions>();
            if (_face == null) _face = gameObject.AddComponent<OVRFaceExpressions>();   // 场景里没挂就自己挂一个
        }
        if (!_face.ValidExpressions) return;
        _feaLastValidAt = Time.realtimeSinceStartup;
        int n = (int)OVRFaceExpressions.FaceExpression.Max;   // 70
        var sb = new StringBuilder(n * 7 + 20);
        sb.Append('[').Append(NowMs());
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        for (int i = 0; i < n; i++)
            sb.Append(',').Append(_face[(OVRFaceExpressions.FaceExpression)i].ToString("0.####", inv));
        // 区域置信度（下半脸 / 上半脸），后端写进 fea.csv 的 conf_lower / conf_upper
        _face.TryGetWeightConfidence(OVRFaceExpressions.FaceRegionConfidence.Lower, out float cl);
        _face.TryGetWeightConfidence(OVRFaceExpressions.FaceRegionConfidence.Upper, out float cu);
        sb.Append(',').Append(cl.ToString("0.###", inv)).Append(',').Append(cu.ToString("0.###", inv));
        sb.Append(']');
        _feaRows.Add(sb.ToString());
        if (_feaRows.Count > FeaBacklogMax) _feaRows.RemoveRange(0, _feaRows.Count - FeaBacklogMax);
    }

    private IEnumerator FeaUploadLoop()
    {
        var wait = new WaitForSeconds(1f);
        while (true)
        {
            yield return wait;
            if (_feaRows.Count == 0) continue;
            var batch = new List<string>(_feaRows);
            _feaRows.Clear();
            string json = "{\"offset_ms\":" + (_clockKnown ? _clockOffset : 0)
                        + ",\"rate_hz\":" + feaRateHz.ToString(System.Globalization.CultureInfo.InvariantCulture)
                        + ",\"rows\":[" + string.Join(",", batch) + "]}";
            bool ok = false;
            yield return StimNet.Post("/api/stimlib/fea", json, (code, _) => ok = code == 200);
            if (!ok)                                     // 失败就放回队首，下一秒连同新数据一起重发
            {
                _feaRows.InsertRange(0, batch);
                if (_feaRows.Count > FeaBacklogMax) _feaRows.RemoveRange(0, _feaRows.Count - FeaBacklogMax);
            }
        }
    }

    private void UpdateClock(long t0, long t1, long serverMs)
    {
        if (serverMs <= 0) return;
        _sync.Enqueue(new[] { t1 - t0, serverMs - (t0 + t1) / 2 });
        while (_sync.Count > 20) _sync.Dequeue();
        long bestRtt = long.MaxValue;
        foreach (var s in _sync) if (s[0] < bestRtt) { bestRtt = s[0]; _clockOffset = s[1]; }
        _clockKnown = true;
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
                long t0 = NowMs();
                yield return req.SendWebRequest();
                long t1 = NowMs();
                if (req.result == UnityWebRequest.Result.Success)
                {
                    PbState st = null;
                    try { st = JsonUtility.FromJson<PbState>(req.downloadHandler.text); } catch { st = null; }
                    if (st != null)
                    {
                        UpdateClock(t0, t1, st.server_ms);
                        Apply(st);
                    }
                }
            }
            yield return wait;
        }
    }

    private static bool Has(StimSpec s) => StimSpec.Has(s);

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
                SetGrayLevel(_lastLuma >= 0 ? _lastLuma : grayLevel);
                SetSky(_gray);
                ShowFixation(false, "");
                if (Has(st.stimulus) && st.stimulus.RatingMode != "none" && _rating != null)
                    _rating.Show(st.stimulus.id, st.stimulus.RatingMode,
                                 st.rating_opts != null && st.rating_opts.require_label7,
                                 st.rating_opts != null && st.rating_opts.ask_intensity);
                break;
            case "done":
                StopMedia();
                SetGrayLevel(grayLevel);
                SetSky(_gray);
                HideRating();
                ShowFixation(true, L("实验结束，谢谢参与！", "実験は終了しました。ご協力ありがとうございました！", "All done. Thank you!"));
                break;
            case "posed":                       // 表演校准：提示做某类表情
                StopMedia();
                SetGrayLevel(grayLevel);
                SetSky(_gray);
                HideRating();
                ShowFixation(true, PosedText(st.cue));
                break;
            case "posed_relax":
                StopMedia();
                SetGrayLevel(grayLevel);
                SetSky(_gray);
                HideRating();
                ShowFixation(true, L("放松", "リラックス", "Relax"));
                break;
            case "break":
                StopMedia();
                SetGrayLevel(grayLevel);
                SetSky(_gray);
                HideRating();
                ShowFixation(true, L("休息一下\n可以闭眼放松，稍后继续", "少し休憩しましょう\n目を閉じてリラックスしてください",
                                     "Take a short break\nYou can close your eyes"));
                break;
            case "rest":                        // 整场开头的静息基线
                StopMedia();
                SetGrayLevel(grayLevel);
                SetSky(_gray);
                HideRating();
                ShowFixation(true, L("请放松，自然地注视前方\n+", "リラックスして、正面を自然に見てください\n+",
                                     "Relax and look straight ahead\n+"));
                break;
            default:   // fixation / recovery / rating_done
                StopMedia();
                if (st.phase == "fixation")
                {
                    SetGrayLevel(LumaFor(st.upcoming));
                    PrepareUpcoming(st.upcoming);
                }
                else if (st.phase == "recovery" || st.phase == "rating_done") SetGrayLevel(_lastLuma >= 0 ? _lastLuma : grayLevel);
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
        DropPrepared();
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

    private void SetGrayLevel(float v)
    {
        v = Mathf.Clamp01(v);
        if (Mathf.Abs(v - _grayNow) < 0.002f) return;
        _grayNow = v;
        var g = new Color(v, v, v);
        for (int x = 0; x < 4; x++) for (int y = 0; y < 2; y++) _gray.SetPixel(x, y, g);
        _gray.Apply();
    }

    // 图片的平均亮度，按刺激 id 缓存；非图片（视频 / 场景 / 模仿）或未缓存时返回 grayLevel
    private float LumaFor(StimSpec s)
    {
        if (!Has(s) || s.ResolvedKind != StimSpec.KindImage || !s.HasMedia) return grayLevel;
        if (_luma.TryGetValue(s.id, out float v)) return v;
        string path = _cache.PathFor(s);
        if (!File.Exists(path)) return grayLevel;
        var tex = new Texture2D(2, 2, TextureFormat.RGB24, true);
        tex.LoadImage(File.ReadAllBytes(path));
        v = SkyboxImagePresenter.MeanLuma(tex);
        Destroy(tex);
        _luma[s.id] = v;
        return v;
    }

    // 把全景正中（+Z）转到头部当前朝向；shader 里 _Rotation = -yaw
    private void FaceSubject()
    {
        if (_skyboxMat == null || !_skyboxMat.HasProperty("_Rotation") || _rig == null) return;
        float yaw = _rig.centerEyeAnchor.eulerAngles.y;
        _skyboxMat.SetFloat("_Rotation", Mathf.Repeat(-yaw, 360f));
    }

    private void SetSky(Texture tex)
    {
        if (_skyboxMat == null || tex == null) return;
        if (_skyboxMat.HasProperty("_MainTex")) _skyboxMat.SetTexture("_MainTex", tex);
        if (_skyboxMat.HasProperty("_Tex")) _skyboxMat.SetTexture("_Tex", tex);
    }

    private void StopMedia()
    {
        if (_cur != null) _cur.End();
        _cur = null;
        _curCtx = null;
        _curBegun = false;
        _onsetMs = 0;
        _onsetAt = -1f;
        _completeSent = false;
        _curId = "";
        _curKind = "";
        if (_hsState != "error") _hsState = "";
    }

    private void DropPrepared()
    {
        _pre?.End();
        _pre = null;
        _preCtx = null;
        _preId = "";
    }

    // ── 呈现器 ──────────────────────────────────────────────────────
    private static IStimulusPresenter CreatePresenter(string kind)
    {
        switch (kind)
        {
            case StimSpec.KindImage: return new SkyboxImagePresenter();
            case StimSpec.KindVideo: return new SkyboxVideoPresenter();
            case StimSpec.KindMimicry: return new MimicryClipPresenter();
            case StimSpec.KindScene: return new ScenePrefabPresenter();
            default: return null;
        }
    }

    private StimContext NewContext(StimSpec s)
    {
        return new StimContext
        {
            StimId = s.id, Spec = s, Host = this, Root = transform, Rig = _rig, EyeCamera = _eyeCamera,
            Cache = _cache, Gray = _gray, DefaultGray = grayLevel, VideoAudioDefault = videoAudio,
            IsCurrentFn = id => !string.IsNullOrEmpty(id) && id == _curId,
            LangFn = () => _lang,
            SetSkyFn = SetSky,
            FaceSubjectFn = FaceSubject,
            SetGrayLevelFn = SetGrayLevel,
            SetLastLumaFn = (id, v) => { _lastLuma = v; if (v >= 0f) _luma[id] = v; },
            AckFn = OnAck,
            FailFn = (id, why) => Fail(why),
            EventFn = (id, k, d, ms) => _events.Add(id, k, d, ms),
            PlaceInFrontFn = PlaceInFront,
        };
    }

    // 注视阶段：下一个是 scene 就先实例化好（不激活），刺激阶段开始时不卡帧
    private void PrepareUpcoming(StimSpec up)
    {
        if (!Has(up) || up.ResolvedKind != StimSpec.KindScene) return;
        if (_preId == up.id) return;
        DropPrepared();
        _preId = up.id;
        _preCtx = NewContext(up);
        _pre = new ScenePrefabPresenter();
        StartCoroutine(_pre.Prepare(up, _preCtx));
    }

    private void OnAck(string stimId, string state, long onsetHeadsetMs)
    {
        _hsState = state;
        if (onsetHeadsetMs > 0 && _onsetMs == 0)
        {
            _onsetMs = onsetHeadsetMs;
            _onsetAt = Time.realtimeSinceStartup - Mathf.Max(0f, (NowMs() - onsetHeadsetMs) / 1000f);
        }
        StartCoroutine(ReportOnce());
    }

    // ── 加载并呈现 ──────────────────────────────────────────────────
    private IEnumerator LoadAndShow(StimSpec s)
    {
        int seq = ++_loadSeq;
        _hsState = "";
        StopMedia();
        _curId = s.id;
        _curKind = s.ResolvedKind;
        _hsState = "loading";
        _hsError = "";
        SetSky(_gray);
        ShowFixation(true, L("加载中…", "読み込み中…", "Loading…"));

        IStimulusPresenter p;
        StimContext ctx;
        if (_pre != null && _preId == s.id && _pre.IsReady)
        {
            p = _pre; ctx = _preCtx;                     // 注视阶段已经准备好的 scene
            _pre = null; _preCtx = null; _preId = "";
            _cur = p; _curCtx = ctx;
        }
        else
        {
            DropPrepared();
            p = CreatePresenter(_curKind);
            if (p == null)
            {
                Fail($"unknown stimulus kind '{_curKind}' (supported: {StimSpec.KindImage}, {StimSpec.KindVideo}, "
                     + $"{StimSpec.KindMimicry}, {StimSpec.KindScene})");
                Debug.LogWarning("[StimPlayer] " + _hsError);
                yield break;
            }
            ctx = NewContext(s);
            _cur = p; _curCtx = ctx;                     // 先登记，加载期间被切走时 StopMedia 能把它 End 掉
            _loading = true;                             // 预缓存让路给当前要播的刺激（AssetCache.Loading）
            yield return p.Prepare(s, ctx);
            if (seq == _loadSeq) _loading = false;
        }
        if (seq != _loadSeq || _cur != p) { p.End(); yield break; }   // 加载期间已经切走了
        if (!p.IsReady)
        {
            if (_hsState != "error") Fail("prepare failed");
            yield break;
        }

        ShowFixation(false, "");
        _seekVer = _st != null && _st.seek != null ? _st.seek.ver : -1;
        p.SetPaused(_st != null && _st.paused);         // Begin 之前告诉它要不要先暂停
        p.Begin();
        _curBegun = true;
        Debug.Log($"[StimPlayer] 呈现 {s.id}（{_curKind}）");
    }

    private void LateUpdate()
    {
        if (_cur == null || !_curBegun) return;
        float t = _onsetAt >= 0f ? Time.realtimeSinceStartup - _onsetAt : -1f;
        _cur.Tick(t);
        if (_cur != null && !_completeSent && _cur.Completed)
        {
            _completeSent = true;
            _curCtx?.Event("complete", "");
        }
    }

    private string PosedText(Cue c)
    {
        if (c == null || string.IsNullOrEmpty(c.emotion)) return "+";
        int k = System.Array.IndexOf(StimText.EmotionKeys, c.emotion);
        string[] labels = StimText.Labels(_lang);
        string name = k >= 0 ? labels[k] : c.emotion;
        string head = L($"请做出「{name}」的表情并保持", $"「{name}」の表情をして、そのまま保ってください",
                        $"Make a \"{name}\" face and hold it");
        return head + $"\n{c.i} / {c.n}";
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
        if (_cur == null || !_curBegun) return;
        if (st.seek != null && st.seek.ver != _seekVer)
        {
            _seekVer = st.seek.ver;
            _cur.Seek(st.seek.pos_s);
        }
        _cur.SetPaused(st.paused);
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
        float pos = _cur != null ? _cur.Position : 0f;
        var sb = new StringBuilder();
        sb.Append("{\"stimulus_id\":\"").Append(StimNet.Esc(_curId)).Append("\",\"state\":\"").Append(StimNet.Esc(_hsState))
          .Append("\",\"kind\":\"").Append(StimNet.Esc(_curKind))
          .Append("\",\"position_s\":").Append(pos.ToString(System.Globalization.CultureInfo.InvariantCulture))
          .Append(",\"onset_headset_ms\":").Append(_onsetMs > 0 ? _onsetMs.ToString() : "null")
          .Append(",\"error\":\"").Append(StimNet.Esc(_hsError))
          .Append("\",\"fea_ok\":").Append(Time.realtimeSinceStartup - _feaLastValidAt < 2f ? "true" : "false")
          .Append(",\"clock_offset_ms\":").Append(_clockKnown ? _clockOffset.ToString() : "null")
          .Append(",\"cached_ids\":[");
        bool first = true;
        if (_cache != null)
            foreach (var id in _cache.CachedIds) { if (!first) sb.Append(','); sb.Append('"').Append(StimNet.Esc(id)).Append('"'); first = false; }
        sb.Append("]}");
        yield return StimNet.Post("/api/stimlib/playback/status", sb.ToString(), null);
    }

    private string L(string zh, string ja, string en) => StimText.L(_lang, zh, ja, en);

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
        _fixText = StimUi.MakeText(r, "+", 120, Vector2.zero, new Vector2(1600, 300), Color.white);
        _fixRoot.SetActive(false);

        _rating = new RatingPanel(this, _rig, _eyeCamera, panelWidth, panelDistance, () => _lang, PlaceInFront);
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

    private void HideRating() => _rating?.Hide();

    private void Update()
    {
        SampleFea();
        _rating?.Tick();
    }

    private void OnDestroy()
    {
        _cur?.End();
        _pre?.End();
    }
}
