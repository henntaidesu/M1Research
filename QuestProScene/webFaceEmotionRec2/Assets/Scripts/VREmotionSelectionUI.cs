// VREmotionSelectionUI.cs
// 「自评」情绪采集场景：头显里展示全景刺激图（Skybox），
// 在用户正对面生成世界空间 Canvas，**右侧竖排** 7 个情绪矩形按钮，
// 用户看完全景后点选自己的真实感受。
//
// 界面上刻意不放「请选择…」之类的提示语：竖排按钮本身已说明要做什么，
// 多一行字只是占地方、分散对全景刺激的注意力（刺激才是实验变量）。
// 每张图只呈现一次、选完即隐藏选项，避免同一次观测被重复计入数据集。
//
// 选中的瞬间调用 QuestProFaceCapture.CaptureSample(label) 记录当前
// 70 维 blendshape（与 DataSet/quest_pro_capture_spec.md 格式一致），
// 不涉及内向摄像头原始人脸图像（Quest Pro 隐私策略不开放）。
//
// 输入：自带射线点击器，不依赖 OVRInputModule（项目为「仅新输入系统」，
// 旧版 Input 点击路径会被条件编译掉）。
//   - 编辑器（无手柄）：鼠标（新输入系统 Mouse.current）+ 左键点击；
//   - 头显：手柄射线（右手优先）+ 扳机/A 键，带可见激光。
// 按钮加 BoxCollider，用 Physics.Raycast 命中，绕开 UI 事件系统的跨平台坑。
//
// 场景前置：
//   1) 场景里有 OVRCameraRig（自带 OVRManager）+ 一个挂 OVRFaceExpressions 的物体。
//   2) OculusProjectConfig 的 Face Tracking Support = Supported。
//   3) 刺激图放到 Assets/Resources/stimulus/ 下。
//   4) Project Settings → Graphics → Always Included Shaders 加入 "Skybox/Panoramic"。

using System.Collections;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;
using UnityEngine.UI;

public class VREmotionSelectionUI : MonoBehaviour
{
    // 界面语言由**网页**决定（/cn→中文、/jp→日语），经 /api/stimulus/current 的 lang 下发。
    // 下面这个 Inspector 选项只在后端还没连上时兜底。序号沿用原始编号，老场景不用动。
    public enum UiLanguage { Japanese = 0, Chinese = 1, English = 2 }

    [Header("引用（留空自动查找）")]
    public QuestProFaceCapture faceCapture;
    public OVRCameraRig cameraRig;

    [Header("语言 / 言語")]
    public UiLanguage language = UiLanguage.Japanese;

    [Header("刺激图")]
    [Tooltip("从 Assets/Resources/<stimulusFolder>/ 加载全景图作为 Skybox")]
    public string stimulusFolder = "stimulus";
    public bool shuffleOrder = true;
    [Tooltip("启动就开始播 Resources 里的本地刺激图。默认关闭：等网页推图过来再显示，"
             + "否则一进 App 就弹出情感选项，而此时受试者还没看到任何刺激")]
    public bool autoStartLocalStimuli = false;

    [Header("按钮面板")]
    [Tooltip("面板与用户的距离（米）")]
    public float panelDistance = 2.2f;
    [Tooltip("面板整体宽度（米）")]
    public float panelWidth = 2.1f;
    [Tooltip("选中后反馈提示停留秒数")]
    public float feedbackSeconds = 1f;
    [Range(0.1f, 1f)]
    [Tooltip("面板整体不透明度（0.5 = 半透明，能透过控件看到全景）")]
    public float panelOpacity = 0.5f;
    [Tooltip("换图后先纯净观看几秒，再显示情绪选择控件")]
    public float controlsDelaySeconds = 5f;

    [Header("实时推送（webside 生成后立即显示）")]
    [Tooltip("开启后轮询后端 /api/stimulus/current，有新图立即贴到全景天空盒")]
    public bool enableLivePush = true;
    [Tooltip("留空＝用 BackendConfig.BaseUrl（服务器公网地址）。只在编辑器里临时改才填。")]
    public string backendBaseUrl = "";
    /// <summary>实际用的后端地址（留空或场景里的回环残留 → 全局 BackendConfig）。</summary>
    private string Base => BackendConfig.Resolve(backendBaseUrl);
    [Tooltip("轮询间隔（秒）。同一请求顺带取回「下一张」的生成进度，压小些进度条更跟手")]
    public float livePollSeconds = 0.5f;
    [Tooltip("选完情绪后把它作为「时间轴自评」POST 到 /api/affect/selfreport。"
             + "这是 PG 链路唯一独立于 FEA 的真值来源，关掉就无法训练情绪预测模型。")]
    public bool reportSelfToBackend = true;

    private static readonly string[] EmotionKeys =
        { "angry", "disgust", "fear", "happy", "neutral", "sad", "surprise" };
    private static readonly string[] LabelsJa =
        { "怒り", "嫌悪", "恐怖", "喜び", "中立", "悲しみ", "驚き" };
    private static readonly string[] LabelsZh =
        { "愤怒", "厌恶", "恐惧", "开心", "平静", "悲伤", "惊讶" };
    private static readonly string[] LabelsEn =
        { "Angry", "Disgust", "Fear", "Happy", "Neutral", "Sad", "Surprise" };

    private static readonly Color BtnBase = ModernUI.Surface;
    private static readonly Color BtnHover = ModernUI.Accent;
    private static readonly Color BtnPressed = ModernUI.Success;

    private Texture2D[] _stimuli;
    private Material _skyboxMat;
    private int _imageIndex = -1;

    private Image[] _btnImages;
    private BoxCollider[] _btnColliders;
    private Text _feedbackText;
    private Text _waitingText;     // 「等待画像」提示；挂在 canvas 下，控件隐藏时仍可见
    private Text[] _btnLabels;     // 7 个按钮的文字，切语言时要重刷
    private GameObject _controlsRoot;      // 说明文字 + 7 个情绪按钮，换图后延迟显示
    private Coroutine _revealRoutine;

    // 「下一张生成中」进度条（webside 闭环上报 → /api/stimulus/current 带回）
    // 620 而非原来的 1600：横贯视野的长条太抢眼，且会和右侧情感按钮打架
    private const float ProgressBarWidth = 620f;
    private GameObject _progressRoot;
    private Text _progressText;
    private RectTransform _progressFill;
    private LineRenderer _laser;
    private Camera _eyeCamera;

    private int _hover = -1;
    private bool _busy;
    private int _selectedCount;

    private bool _hasLivePanorama;
    private bool _rated;          // 当前这张图是否已被评分（评过就不再让选，防重复计入）
    private bool _exhausted;      // 本地刺激图已全部放完
    private int _liveVersion;
    private string _liveImageId;      // 当前推送图的 url，自评上报时带上以对齐 affect_image

    private static Font _cjkFont;
    private static bool _cjkFontTried;

    // 后端下发的语言（"zh"/"ja"/"en"）；为空表示还没连上，用 Inspector 的兜底值
    private string _srvLang = "";

    private string Lang
    {
        get
        {
            if (!string.IsNullOrEmpty(_srvLang)) return _srvLang;
            switch (language)
            {
                case UiLanguage.Japanese: return "ja";
                case UiLanguage.English: return "en";
                default: return "zh";
            }
        }
    }

    private string[] Labels
    {
        get
        {
            switch (Lang)
            {
                case "ja": return LabelsJa;
                case "en": return LabelsEn;
                default: return LabelsZh;
            }
        }
    }

    // 三语文案统一入口，跟随 Lang（网页语言优先）
    private string L(string zh, string ja, string en)
    {
        switch (Lang)
        {
            case "ja": return ja;
            case "en": return en;
            default: return zh;
        }
    }

    private void Start()
    {
        if (faceCapture == null) faceCapture = FindObjectOfType<QuestProFaceCapture>();
        if (faceCapture == null)
            Debug.LogWarning("[EmotionUI] 场景里没有 QuestProFaceCapture，选择情绪时不会记录 blendshape。");

        if (cameraRig == null) cameraRig = FindObjectOfType<OVRCameraRig>();
        if (cameraRig == null)
        {
            Debug.LogError("[EmotionUI] 场景里没有 OVRCameraRig，无法搭建按钮面板。");
            return;
        }

        _eyeCamera = cameraRig.centerEyeAnchor != null
            ? cameraRig.centerEyeAnchor.GetComponent<Camera>()
            : Camera.main;

        LoadStimuli();
        BuildPanel();
        // 启动时**不出**情感选项：此刻受试者还没看到任何刺激，出选项没有意义。
        // 必须等真的有图（网页推来，或显式开启本地队列）才显示。
        if (autoStartLocalStimuli) ShowNextStimulus();
        else ShowWaitingForImage();

        if (enableLivePush)
            StartCoroutine(LivePushLoop());
    }

    private void LoadStimuli()
    {
        _stimuli = Resources.LoadAll<Texture2D>(stimulusFolder);
        int count = _stimuli == null ? 0 : _stimuli.Length;
        Debug.Log($"[EmotionUI] 载入刺激图 {count} 张（Resources/{stimulusFolder}）");
        if (count == 0)
            Debug.Log($"[EmotionUI] Resources/{stimulusFolder} 无本地贴图（用 webside 实时推送时属正常）。");

        if (shuffleOrder && count > 1)
        {
            for (int i = _stimuli.Length - 1; i > 0; i--)
            {
                int j = Random.Range(0, i + 1);
                (_stimuli[i], _stimuli[j]) = (_stimuli[j], _stimuli[i]);
            }
        }

        EnsureSkyboxMaterial();
    }

    private void EnsureSkyboxMaterial()
    {
        if (_skyboxMat != null) return;

        // 优先用工程自带的全景材质（Resources/PanoramaSkybox.mat + 自定义等距柱状 shader），
        // 材质在 Resources 里，打包不会被裁；加载不到再退回内置 Skybox/Panoramic。
        var mat = Resources.Load<Material>("PanoramaSkybox");
        if (mat != null)
        {
            _skyboxMat = new Material(mat);
        }
        else
        {
            var sh = Shader.Find("Skybox/Panoramic");
            if (sh == null)
            {
                Debug.LogError("[EmotionUI] 全景材质与 Skybox/Panoramic shader 都找不到，全景图将不显示。");
                return;
            }
            _skyboxMat = new Material(sh);
            if (_skyboxMat.HasProperty("_Mapping")) _skyboxMat.SetFloat("_Mapping", 1f);
            _skyboxMat.EnableKeyword("_MAPPING_LATITUDE_LONGITUDE_LAYOUT");
            _skyboxMat.DisableKeyword("_MAPPING_6_FRAMES_LAYOUT");
        }

        RenderSettings.skybox = _skyboxMat;

        // 确保渲染相机以天空盒清屏，否则看不到全景。
        if (_eyeCamera != null) _eyeCamera.clearFlags = CameraClearFlags.Skybox;
    }

    // 每张图只展示一次：原实现用 % 取模循环，同一张图会被反复评分，
    // 等于把同一次观测重复计入数据集。这里改为顺序推进、放完即止。
    private void ShowNextStimulus()
    {
        if (_hasLivePanorama) return; // 实时推送模式下不覆盖 webside 推来的图
        if (_stimuli == null || _stimuli.Length == 0 || _skyboxMat == null) return;

        if (_imageIndex + 1 >= _stimuli.Length)
        {
            _exhausted = true;
            HideControls();
            // 提示必须用 _waitingText：_feedbackText 在 _controlsRoot 里，刚被隐藏了会看不见
            if (_waitingText != null)
            {
                _waitingText.text = L("全部图片已展示完毕，谢谢参与。",
                                      "すべての画像が終了しました。ありがとうございました。",
                                      "All images are done. Thank you!");
                _waitingText.gameObject.SetActive(true);
            }
            Debug.Log($"[EmotionUI] 本地刺激图已全部展示完（共 {_stimuli.Length} 张），不再循环。");
            return;
        }

        _imageIndex++;
        var tex = _stimuli[_imageIndex];

        // Skybox/Panoramic 用 _MainTex；两个都设以防属性名差异。
        if (_skyboxMat.HasProperty("_MainTex")) _skyboxMat.SetTexture("_MainTex", tex);
        if (_skyboxMat.HasProperty("_Tex")) _skyboxMat.SetTexture("_Tex", tex);

        Debug.Log($"[EmotionUI] 应用全景：{tex.name}（{tex.width}x{tex.height}）"
                  + $"[{_imageIndex + 1}/{_stimuli.Length}]");
        _rated = false;
        HideWaitingText();          // 有图了
        ScheduleControlsReveal();
    }

    /// <summary>立即隐藏情绪选择控件（BoxCollider 随之失活，射线打不到，不会误触）。</summary>
    private void HideControls()
    {
        if (_revealRoutine != null) { StopCoroutine(_revealRoutine); _revealRoutine = null; }
        _hover = -1;
        if (_controlsRoot != null) _controlsRoot.SetActive(false);
    }

    /// <summary>还没有任何刺激图：只收起选项，**不显示「等待图片…」**。
    /// 受试者戴着头显时，一行悬浮文字本身就是干扰刺激，会污染这段时间的表情基线。
    /// （「全部展示完毕」那句仍然保留——那是实验结束的必要告知。）</summary>
    private void ShowWaitingForImage()
    {
        HideControls();
        HideWaitingText();
    }

    /// <summary>图到位了：撤掉等待提示。</summary>
    private void HideWaitingText()
    {
        if (_waitingText != null) _waitingText.gameObject.SetActive(false);
    }

    private void BuildPanel()
    {
        Transform eye = cameraRig.centerEyeAnchor;

        Vector3 forward = eye.forward;
        forward.y = 0f;
        if (forward.sqrMagnitude < 0.0001f) forward = Vector3.forward;
        forward.Normalize();

        var canvasGO = new GameObject("EmotionSelectionCanvas");
        var canvas = canvasGO.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = _eyeCamera;

        // 整个面板半透明：CanvasGroup 的 alpha 会乘到所有子 Graphic 上；
        // 命中检测走 BoxCollider + Physics.Raycast，不受 CanvasGroup 影响。
        var group = canvasGO.AddComponent<CanvasGroup>();
        group.alpha = Mathf.Clamp(panelOpacity, 0.1f, 1f);

        var canvasRect = canvasGO.GetComponent<RectTransform>();
        canvasRect.sizeDelta = new Vector2(2100f, 820f);
        canvasGO.transform.localScale = Vector3.one * (panelWidth / 2100f);

        Vector3 panelPos = eye.position + forward * panelDistance;
        panelPos.y = eye.position.y - 0.15f;
        canvasGO.transform.position = panelPos;
        canvasGO.transform.rotation = Quaternion.LookRotation(forward, Vector3.up);

        // 说明文字 + 情绪按钮挂在同一个根下，换图后整体延迟显示（进度条不在其中，始终可见）
        _controlsRoot = new GameObject("ControlsRoot");
        _controlsRoot.transform.SetParent(canvasRect, false);
        var ctrlRect = _controlsRoot.AddComponent<RectTransform>();
        ctrlRect.anchorMin = ctrlRect.anchorMax = new Vector2(0.5f, 0.5f);
        ctrlRect.pivot = new Vector2(0.5f, 0.5f);
        ctrlRect.anchoredPosition = Vector2.zero;
        ctrlRect.sizeDelta = canvasRect.sizeDelta;

        // 不再放「请选择」之类的提示语：右侧竖排按钮本身已说明要做什么，
        // 多一行字只是占地方、分散对全景刺激的注意力。

        _feedbackText = CreateText(ctrlRect, "FeedbackText", "",
            44, new Vector2(-270f, -180f), new Vector2(1500f, 100f), new Color(0.35f, 0.9f, 0.45f));
        _feedbackText.gameObject.SetActive(false);

        // ── 情感选项：右侧竖向长方形矩形堆叠 ──
        // 尺寸是算过的：7 条 × 88 高 + 6 × 10 间隔 = 676，落在 +334…-298 之间，
        // 上不出画布（+410）、下不压进度条（进度条根在 -360，占 -315…-405）。
        const float btnW = 460f, btnH = 88f, gap = 10f;
        const float colX = 790f;          // 右侧列中心：+1050 边界内缩 30 再减半宽
        int n = EmotionKeys.Length;
        float totalH = n * btnH + (n - 1) * gap;
        float colCenterY = 40f;
        float startY = colCenterY + totalH / 2f - btnH / 2f;

        _btnImages = new Image[n];
        _btnColliders = new BoxCollider[n];
        _btnLabels = new Text[n];
        for (int i = 0; i < n; i++)
        {
            float y = startY - i * (btnH + gap);
            CreateEmotionButton(ctrlRect, i, new Vector2(colX, y), new Vector2(btnW, btnH));
        }
        _controlsRoot.SetActive(false);

        // 等待提示挂在 canvas 而不是 _controlsRoot 下：控件隐藏时它还要能看见
        _waitingText = CreateText(canvasRect, "WaitingText", "",
            48, new Vector2(0f, 60f), new Vector2(1800f, 120f), new Color(0.75f, 0.8f, 0.9f));
        _waitingText.gameObject.SetActive(false);

        BuildProgressBar(canvasRect);
        BuildLaser();
    }

    // 换图 → 先纯净观看 controlsDelaySeconds 秒 → 再显示情绪选择控件。
    // 控件隐藏时 BoxCollider 一起失活，射线打不到，不会误触。
    private void ScheduleControlsReveal()
    {
        if (_controlsRoot == null) return;
        if (_exhausted) return;      // 本地图放完了就不再出选择面板
        if (_revealRoutine != null) StopCoroutine(_revealRoutine);
        _revealRoutine = StartCoroutine(RevealControlsRoutine());
    }

    private IEnumerator RevealControlsRoutine()
    {
        _hover = -1;
        _controlsRoot.SetActive(false);
        yield return new WaitForSeconds(Mathf.Max(0f, controlsDelaySeconds));
        _controlsRoot.SetActive(true);
        _revealRoutine = null;
    }

    // 面板底部：「下一张生成中 xx%」文字 + 进度条，未在生成时整体隐藏。
    // 进度条：细长圆角轨道 + 强调色填充，文字压在轨道上方左对齐。
    // 与控制台同一套设计令牌（圆角九宫格、同一强调色），两块 UI 观感一致。
    // 原来 1600 宽横贯整个视野，又长又抢眼；这里收到 620，挪到左下角，
    // 不与右侧的情感按钮抢注意力。
    private void BuildProgressBar(RectTransform parent)
    {
        _progressRoot = new GameObject("ProgressRoot");
        _progressRoot.transform.SetParent(parent, false);
        var rootRect = _progressRoot.AddComponent<RectTransform>();
        rootRect.anchorMin = rootRect.anchorMax = new Vector2(0.5f, 0.5f);
        rootRect.pivot = new Vector2(0.5f, 0.5f);
        // 左下角：避开右侧竖排按钮（按钮列左界 +560）
        rootRect.anchoredPosition = new Vector2(-270f, -330f);
        rootRect.sizeDelta = new Vector2(ProgressBarWidth, 70f);

        _progressText = CreateText(rootRect, "ProgressText", "", 26,
            new Vector2(0f, 22f), new Vector2(ProgressBarWidth, 34f),
            new Color(0.545f, 0.576f, 0.651f), TextAnchor.MiddleLeft);

        var bgGO = new GameObject("BarBg");
        bgGO.transform.SetParent(rootRect, false);
        var bgRect = bgGO.AddComponent<RectTransform>();
        bgRect.anchorMin = bgRect.anchorMax = new Vector2(0.5f, 0.5f);
        bgRect.pivot = new Vector2(0.5f, 0.5f);
        bgRect.anchoredPosition = new Vector2(0f, -8f);
        bgRect.sizeDelta = new Vector2(ProgressBarWidth, 10f);   // 细轨道，20 → 10
        var bgImg = bgGO.AddComponent<Image>();
        ModernUI.Round(bgImg, 999f);
        bgImg.color = new Color(1f, 1f, 1f, 0.12f);

        var fillGO = new GameObject("BarFill");
        fillGO.transform.SetParent(bgRect, false);
        _progressFill = fillGO.AddComponent<RectTransform>();
        _progressFill.anchorMin = new Vector2(0f, 0f);   // 贴左边、纵向撑满，宽度即进度
        _progressFill.anchorMax = new Vector2(0f, 1f);
        _progressFill.pivot = new Vector2(0f, 0.5f);
        _progressFill.anchoredPosition = Vector2.zero;
        _progressFill.sizeDelta = new Vector2(0f, 0f);
        var fillImg = fillGO.AddComponent<Image>();
        ModernUI.Round(fillImg, 999f);
        fillImg.color = new Color(0.231f, 0.510f, 0.965f, 1f);   // 与控制台同一强调色

        _progressRoot.SetActive(false);
    }

    // 应用后端回传的进度：running=false 隐藏；max=0 表示还在生成提示词阶段。
    private void ApplyProgress(ProgressResp p)
    {
        if (_progressRoot == null) return;
        bool show = p != null && p.running;
        if (_progressRoot.activeSelf != show) _progressRoot.SetActive(show);
        if (!show) return;

        if (p.max > 0)
        {
            float t = Mathf.Clamp01((float)p.current / p.max);
            int pct = Mathf.RoundToInt(t * 100f);
            _progressFill.sizeDelta = new Vector2(ProgressBarWidth * t, 0f);
            _progressText.text = L(
                $"下一张生成中… {pct}%（{p.current}/{p.max}）",
                $"次の画像を生成中… {pct}%（{p.current}/{p.max}）",
                $"Generating next image… {pct}% ({p.current}/{p.max})");
        }
        else
        {
            _progressFill.sizeDelta = new Vector2(0f, 0f);
            _progressText.text = L(
                $"正在准备下一张…（第 {p.step} 张）",
                $"次の画像を準備中…（{p.step} 枚目）",
                $"Preparing next image… (#{p.step})");
        }
    }

    private Text CreateText(RectTransform parent, string name, string content, int fontSize,
        Vector2 anchoredPos, Vector2 size, Color color, TextAnchor align = TextAnchor.MiddleCenter)
    {
        var go = new GameObject(name);
        go.transform.SetParent(parent, false);

        var rect = go.AddComponent<RectTransform>();
        rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
        rect.pivot = new Vector2(0.5f, 0.5f);
        rect.anchoredPosition = anchoredPos;
        rect.sizeDelta = size;

        var text = go.AddComponent<Text>();
        text.text = content;
        text.font = ResolveFont();
        text.fontSize = fontSize;
        text.alignment = align;
        text.color = color;
        text.horizontalOverflow = HorizontalWrapMode.Wrap;
        return text;
    }

    private void CreateEmotionButton(RectTransform parent, int index, Vector2 anchoredPos, Vector2 size)
    {
        string key = EmotionKeys[index];

        var go = new GameObject($"Btn_{key}");
        go.transform.SetParent(parent, false);

        var rect = go.AddComponent<RectTransform>();
        rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
        rect.pivot = new Vector2(0.5f, 0.5f);
        rect.anchoredPosition = anchoredPos;
        rect.sizeDelta = size;

        var image = go.AddComponent<Image>();
        ModernUI.Round(image, 22f);
        image.color = BtnBase;

        var boxCollider = go.AddComponent<BoxCollider>();
        boxCollider.center = Vector3.zero;
        boxCollider.size = new Vector3(size.x, size.y, 10f);

        var textGO = new GameObject("Label");
        textGO.transform.SetParent(go.transform, false);
        var textRect = textGO.AddComponent<RectTransform>();
        textRect.anchorMin = Vector2.zero;
        textRect.anchorMax = Vector2.one;
        textRect.offsetMin = Vector2.zero;
        textRect.offsetMax = Vector2.zero;

        var text = textGO.AddComponent<Text>();
        text.text = ButtonLabel(index);
        text.font = ResolveFont();
        text.fontSize = 34;
        text.alignment = TextAnchor.MiddleCenter;
        text.color = Color.white;

        _btnImages[index] = image;
        _btnColliders[index] = boxCollider;
        _btnLabels[index] = text;      // 切语言时要重刷这些文字
    }

    // 英文界面下 key 与 label 只差大小写，就不重复显示两行了
    // 日文时额外用小号灰字显示英文 key 便于实验员核对；英文时只显示标签
    private string ButtonLabel(int i)
    {
        string label = Labels[i];
        return Lang == "en" ? label : ModernUI.TwoLine(label, EmotionKeys[i], 22);
    }

    private void BuildLaser()
    {
        var laserGO = new GameObject("Pointer");
        _laser = laserGO.AddComponent<LineRenderer>();
        _laser.widthMultiplier = 0.005f;
        _laser.positionCount = 2;
        _laser.useWorldSpace = true;
        _laser.numCapVertices = 2;
        var sh = Shader.Find("Sprites/Default") ?? Shader.Find("Unlit/Color");
        if (sh != null) _laser.material = new Material(sh);
        _laser.startColor = _laser.endColor = new Color(0.4f, 0.7f, 1f, 0.9f);
        _laser.enabled = false;
    }

    private void Update()
    {
        if (_btnColliders == null) return;

        bool usingController = ControllerConnected();
        Ray ray;
        bool click;

        if (usingController)
        {
            Transform aim = ActiveHandAnchor();
            if (aim == null) return;
            ray = new Ray(aim.position, aim.forward);
            click = OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger)
                 || OVRInput.GetDown(OVRInput.Button.SecondaryIndexTrigger)
                 || OVRInput.GetDown(OVRInput.Button.One);
        }
        else
        {
            if (_eyeCamera == null || Mouse.current == null) return;
            Vector2 mp = Mouse.current.position.ReadValue();
            ray = _eyeCamera.ScreenPointToRay(mp);
            click = Mouse.current.leftButton.wasPressedThisFrame;
        }

        int hover = -1;
        Vector3 hitPoint = ray.origin + ray.direction * 3f;
        if (Physics.Raycast(ray, out RaycastHit hit, 50f))
        {
            hitPoint = hit.point;
            for (int i = 0; i < _btnColliders.Length; i++)
                if (_btnColliders[i] == hit.collider) { hover = i; break; }
        }

        if (!_busy) SetHover(hover);
        UpdateLaser(usingController, ray.origin, hitPoint);

        // _rated 兜底：面板隐藏那一帧射线可能已命中，防止同一张图被提交两次
        if (click && hover >= 0 && !_busy && !_rated)
            StartCoroutine(SelectRoutine(hover));
    }

    private bool ControllerConnected()
    {
        var c = OVRInput.GetConnectedControllers();
        return (c & (OVRInput.Controller.RTouch | OVRInput.Controller.LTouch | OVRInput.Controller.Touch)) != 0;
    }

    private Transform ActiveHandAnchor()
    {
        var c = OVRInput.GetConnectedControllers();
        if ((c & OVRInput.Controller.RTouch) != 0 && cameraRig.rightHandAnchor != null)
            return cameraRig.rightHandAnchor;
        if ((c & OVRInput.Controller.LTouch) != 0 && cameraRig.leftHandAnchor != null)
            return cameraRig.leftHandAnchor;
        return cameraRig.rightHandAnchor != null ? cameraRig.rightHandAnchor : cameraRig.centerEyeAnchor;
    }

    private void SetHover(int hover)
    {
        if (hover == _hover) return;
        _hover = hover;
        for (int i = 0; i < _btnImages.Length; i++)
            ModernUI.SetState(_btnImages[i], i == hover ? BtnHover : BtnBase, i == hover);
    }

    private void UpdateLaser(bool show, Vector3 from, Vector3 to)
    {
        if (_laser == null) return;
        _laser.enabled = show;
        if (show)
        {
            _laser.SetPosition(0, from);
            _laser.SetPosition(1, to);
        }
    }

    private IEnumerator SelectRoutine(int idx)
    {
        _busy = true;
        _hover = -1;
        for (int i = 0; i < _btnImages.Length; i++)
            ModernUI.SetState(_btnImages[i], i == idx ? BtnPressed : BtnBase);
        ModernUI.Press(_btnImages[idx]);

        string key = EmotionKeys[idx];
        string label = Labels[idx];
        string sampleId = faceCapture != null ? faceCapture.CaptureSample(key) : null;
        // 同时把这次选择作为「时间轴自评」上报给后端（PG 链路的真值来源）
        StartCoroutine(PostSelfReport(key));
        _selectedCount++;
        _rated = true;

        // 选完立刻隐藏选择面板：这张图已评过，再让选就会重复计入同一次观测。
        // 反馈文字留在原处，等下一张图到位（或本地队列放完）时再决定要不要重新出面板。
        HideControls();

        _feedbackText.text = sampleId != null ? RecordedMsg(label) : NoFaceMsg(label);
        _feedbackText.gameObject.SetActive(true);

        yield return new WaitForSeconds(feedbackSeconds);

        _feedbackText.gameObject.SetActive(false);
        ShowNextStimulus();     // 本地模式换下一张；实时推送模式下什么都不做，等新图推来
        // 推送模式下这一张已评完、下一张还没来 → 回到「等待画像」，选项保持隐藏
        if (_hasLivePanorama) ShowWaitingForImage();

        for (int i = 0; i < _btnImages.Length; i++)
            ModernUI.SetState(_btnImages[i], BtnBase);
        _busy = false;
    }

    private string RecordedMsg(string label) => L(
        $"已记录：{label}（第 {_selectedCount} 次）",
        $"記録しました：{label}（{_selectedCount}回目）",
        $"Recorded: {label} (#{_selectedCount})");

    private string NoFaceMsg(string label) => L(
        $"未记录到人脸数据（{label}）",
        $"顔データを取得できませんでした（{label}）",
        $"No face data captured ({label})");

    /// <summary>外部（webside 实时推送）设置全景贴图，覆盖本地循环图。</summary>
    public void SetPanoramaTexture(Texture2D tex)
    {
        if (tex == null) return;
        EnsureSkyboxMaterial();
        if (_skyboxMat == null) return;
        // 经度方向缠绕：Repeat 让缝两侧双线性过滤跨越左右边缘采样，配合 shader 的 tex2Dgrad 消除接缝。
        tex.wrapModeU = TextureWrapMode.Repeat;
        tex.wrapModeV = TextureWrapMode.Clamp;   // 纬度不缠绕，避免上下极点串色
        tex.filterMode = FilterMode.Bilinear;
        if (_skyboxMat.HasProperty("_MainTex")) _skyboxMat.SetTexture("_MainTex", tex);
        if (_skyboxMat.HasProperty("_Tex")) _skyboxMat.SetTexture("_Tex", tex);
        _hasLivePanorama = true;
        if (_eyeCamera != null) _eyeCamera.clearFlags = CameraClearFlags.Skybox;
        _rated = false;             // 新图 → 允许再评一次（每张图仅一次）
        if (_feedbackText != null) _feedbackText.gameObject.SetActive(false);
        HideWaitingText();          // 网页推来图了，撤掉「等待画像」
        ScheduleControlsReveal();   // 新图到位 → 先看 controlsDelaySeconds 秒再出控件
    }

    [System.Serializable]
    private class ProgressResp
    {
        public bool running;
        public int current;   // 已完成采样步
        public int max;       // 总步数（0 = 还在生成提示词）
        public int step;      // 闭环第几张
        public string note;
    }

    [System.Serializable]
    private class CurrentStimulusResp
    {
        public int version;
        public string lang;        // 网页当前语言，头显跟随
        public string url;
        public string emotion;
        public ProgressResp progress;
    }

    // ── PG 时间轴自评上报 ───────────────────────────────────────────
    // 用户点选的情绪是**唯一独立于 FEA 的真值**：规则式 classify_fea 与 FEA 同源
    // （y=f(X)），不能既当输入又当标签。不上报的话，PG 链路采到的 FEA 时间轴
    // 只有信号没有真值，无法用于训练情绪预测模型。
    // 与 CaptureSample（静态图数据集）并行，互不替代。
    private IEnumerator PostSelfReport(string label7)
    {
        if (!reportSelfToBackend) yield break;
        string baseUrl = Base;
        if (string.IsNullOrEmpty(baseUrl)) yield break;

        long nowMs = (long)(System.DateTime.UtcNow -
                            new System.DateTime(1970, 1, 1, 0, 0, 0, System.DateTimeKind.Utc))
                           .TotalMilliseconds;
        // image_id 用当前推送图的 url，和 affect_image 对得上，便于 block-level 分组
        string imageId = string.IsNullOrEmpty(_liveImageId) ? "" : _liveImageId;
        string body = "{\"rows\":[{\"ts_ms\":" + nowMs +
                      ",\"image_id\":\"" + EscapeJson(imageId) + "\"" +
                      ",\"label7\":\"" + EscapeJson(label7) + "\"" +
                      ",\"source\":\"headset_ui\"}]}";

        using (var req = new UnityWebRequest(baseUrl + "/api/affect/selfreport", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 5;
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogWarning($"[EmotionUI] 自评上报失败：{req.error} {req.downloadHandler?.text}");
            else
                Debug.Log($"[EmotionUI] 自评已上报 {label7}");
        }
    }

    private static string EscapeJson(string s) =>
        (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");

    // 轮询后端 /api/stimulus/current，version 变大时下载新全景贴到天空盒。
    private IEnumerator LivePushLoop()
    {
        Debug.Log($"[EmotionUI] 实时推送轮询启动 → {Base}");
        var wait = new WaitForSeconds(Mathf.Max(0.3f, livePollSeconds));
        while (true)
        {
            string baseUrl = Base;
            using (var req = UnityWebRequest.Get(baseUrl + "/api/stimulus/current"))
            {
                req.timeout = 5;
                yield return req.SendWebRequest();
                bool ok = req.result == UnityWebRequest.Result.Success;
                if (ok)
                {
                    CurrentStimulusResp cur = null;
                    try { cur = JsonUtility.FromJson<CurrentStimulusResp>(req.downloadHandler.text); }
                    catch { cur = null; }
                    ApplyProgress(cur?.progress);
                    ApplyServerLang(cur?.lang);
                    if (cur != null && cur.version > _liveVersion && !string.IsNullOrEmpty(cur.url))
                    {
                        // **下载成功了才推进 _liveVersion**。以前是先推进再下载，一旦下载失败
                        // （外网下 3MB 全景很容易超时）版本号就被吃掉了，这张图再也不会重试，
                        // 头显永远停在上一张、进度条却是 100%——就是「切不到下一张」那个现象。
                        // 现在失败就保持旧版本号，下一轮轮询会自动重试。
                        bool dlOk = false;
                        yield return StartCoroutine(
                            DownloadPanorama(baseUrl + cur.url, cur.version, r => dlOk = r));
                        if (dlOk)
                        {
                            _liveVersion = cur.version;
                            _liveImageId = cur.url;  // 自评上报时带上，和 affect_image 对齐
                        }
                    }
                }
                else ApplyProgress(null);   // 后端不通时不留一条卡住的进度条
            }
            yield return wait;
        }
    }

    // 网页切了语言 → 头显跟着换。按钮标签与说明文字都要重刷。
    private void ApplyServerLang(string lang)
    {
        if (string.IsNullOrEmpty(lang) || lang == _srvLang) return;
        _srvLang = lang;
        RefreshTexts();
        Debug.Log($"[EmotionUI] 界面语言跟随网页切到 {lang}");
    }

    private void RefreshTexts()
    {
        if (_btnLabels != null)
        {
            for (int i = 0; i < _btnLabels.Length; i++)
            {
                if (_btnLabels[i] == null) continue;
                _btnLabels[i].text = ButtonLabel(i);
            }
        }
        // 「等待图片…」已取消，切语言时不再需要刷新它；_waitingText 现在只承载
        // 「全部展示完毕」，那句在 _exhausted 分支里自己会设。
    }

    // 全景图 3MB 起步，走公网 + 笔记本热点，20 秒不够——超时就等于这张图丢了。
    private const int PanoTimeoutSec = 90;

    /// <summary>下载全景并贴上；done 回调告诉调用方成不成功（决定要不要推进版本号）。</summary>
    private IEnumerator DownloadPanorama(string url, int version, System.Action<bool> done)
    {
        using (var req = UnityWebRequestTexture.GetTexture(url))
        {
            req.timeout = PanoTimeoutSec;
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"[EmotionUI] 下载推送全景失败 v{version} {url}：{req.error}（保持旧版本号，下轮重试）");
                done?.Invoke(false);
                yield break;
            }
            var tex = DownloadHandlerTexture.GetContent(req);
            SetPanoramaTexture(tex);
            Debug.Log($"[EmotionUI] 已应用推送全景 v{version}：{tex.width}x{tex.height}");
            done?.Invoke(true);
        }
    }

    /// <summary>日/英混排字体（VRStimulusControlPanel 复用，避免两处各维护一份字体列表）。</summary>
    internal static Font ResolveFont()
    {
        if (!_cjkFontTried)
        {
            _cjkFontTried = true;
            try
            {
                _cjkFont = Font.CreateDynamicFontFromOSFont(
                    new[]
                    {
                        "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic",
                        "Noto Sans CJK JP", "Droid Sans Japanese",
                        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                        "PingFang SC", "Droid Sans Fallback"
                    },
                    40);
            }
            catch
            {
                _cjkFont = null;
            }
        }

        return _cjkFont != null ? _cjkFont : Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
    }
}
