// VRStimulusControlPanel.cs
// 头显内的「刺激生成控制台」：不摘头显就能开始/结束生成，并调整全部刺激种子参数。
//
// 形态：视野**左侧可展开菜单**。平时只留一条竖向把手（☰），点一下向右展开，再点收起。
// 菜单**固定在世界里**，不跟头转：启动时按当时的朝向摆在左前方，之后转头它就留在原地。
// 只有用手柄按键（B / Start）唤出菜单时，才重新摆到当前视野左前方——否则转过身就再也够不着了。
//
// 视觉：圆角卡片 + 分区标题 + 「左标签／右取值」两列行 + 状态药丸 + 单一强调色。
// 圆角用 ModernUI 运行时生成的抗锯齿九宫格精灵，悬停/点击走 ModernButton 平滑过渡，不需要额外美术资源。
// 层次靠三件事拉开：字号（标题 30 / 取值 26 / 标签 24 / 分区 20）、
// 颜色（主文本亮、次要文本灰）、以及只有主操作用实心强调色。
//
// **不需要挂到任何 GameObject**：用 [RuntimeInitializeOnLoadMethod] 自动实例化。
// 之前反复出现「脚本没挂进场景所以 VR 里什么都没有」，这种全局 UI 不该依赖人去记得挂。
//
// 语言跟随网页：网页在 /cn 就是中文、/jp 就是日语，经共享状态的 lang 字段下发。
//
// 与网页共享同一份状态（后端 backend/src/use_web/stimulus_control.py）：
//   GET  /api/stimulus/options   选项表（情绪 / 场景四要素 / 调制模式），带 zh+ja+en
//   GET  /api/stimulus/control   当前状态（带 version，只在变化时重建）
//   POST /api/stimulus/control   局部更新，body 里带 source:"vr"
//
// **谁在出图**：DeepSeek 提示词与 ComfyUI 采样都跑在浏览器（VRStimulusPanel.vue）里，
// 本面板只是把 running 置真/假。所以 PC 上那个网页必须保持打开，头显按下开始才会真的出图。

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;
using UnityEngine.UI;

public class VRStimulusControlPanel : MonoBehaviour
{
    public enum FallbackLang { Chinese = 0, Japanese = 1, English = 2 }

    [Header("语言（仅后端未连上时生效）")]
    public FallbackLang fallbackLanguage = FallbackLang.Chinese;

    [Header("后端")]
    [Tooltip("留空＝用 BackendConfig.BaseUrl（服务器公网地址）。只在编辑器里临时改才填。")]
    public string backendBaseUrl = "";
    public float pollSeconds = 1.0f;

    [Header("菜单形态")]
    public float panelDistance = 1.1f;
    [Tooltip("菜单整体向左偏移（米）——「左侧菜单」")]
    public float lateralOffset = 0.42f;
    public float verticalOffset = -0.10f;
    [Tooltip("展开后的菜单宽度（米）")]
    public float panelWidth = 0.34f;
    public bool expandedOnStart = false;

    // ── 画布与栅格（内部单位，最终按 panelWidth 等比缩放）──
    // CanvasH 是算过的，改这些常量前请重算：
    //   主菜单 = 页头(70) + 主操作(104) + 10 行(70 每行) + 2 个分区(40 每个) = 954
    //   底部状态栏占约 90（statusText 在 -CanvasH/2+44，高 70）
    // 加「随机生成」那一行之前是 9 行 884，1040 刚好够；加到 10 行后 954 会溢出 4px
    // 压住状态栏，所以提到 1120（余量 76）。最长的二级页是场景 10 项，比这个短。
    private const float CanvasW = 700f;
    private const float CanvasH = 1120f;
    private const float TabW = 116f;
    private const float PanelW = CanvasW - TabW;
    private const float RowX = (-CanvasW / 2f + TabW + CanvasW / 2f) / 2f;
    private const float RowW = 520f;
    private const float Pad = 22f;          // 卡片内左右留白

    private const float HRow = 62f, HPrimary = 88f, HSection = 34f, HOpt = 60f, HBack = 54f;
    private const float GRow = 8f, GSection = 6f, GBlock = 16f;

    // ── 设计令牌 ──
    private static readonly Color CBg = new Color(0.071f, 0.078f, 0.102f, 0.96f);
    private static readonly Color CCard = new Color(0.110f, 0.125f, 0.161f, 1f);
    private static readonly Color CCardHi = new Color(0.169f, 0.204f, 0.267f, 1f);
    private static readonly Color CAccent = new Color(0.231f, 0.510f, 0.965f, 1f);
    private static readonly Color CAccentSoft = new Color(0.231f, 0.510f, 0.965f, 0.22f);
    // 菜单把手用中性灰：它只是「开合菜单」的入口，不是主操作，不该和「开始生成」抢强调色。
    private static readonly Color CTab = new Color(0.361f, 0.388f, 0.439f, 1f);
    private static readonly Color CSuccess = new Color(0.133f, 0.773f, 0.369f, 1f);
    private static readonly Color CDanger = new Color(0.937f, 0.267f, 0.267f, 1f);
    private static readonly Color CText = new Color(0.910f, 0.918f, 0.941f, 1f);
    private static readonly Color CMuted = new Color(0.545f, 0.576f, 0.651f, 1f);
    private static readonly Color CDivider = new Color(0.149f, 0.169f, 0.212f, 1f);

    private class UiRow
    {
        public BoxCollider Col;
        public Image Img;
        public System.Action OnClick;
        public bool Selected;
        public Color BaseColor = CCard;
    }

    private enum Page { Main, Emotion, Mode, Scene, Intensity, Window }

    private readonly List<UiRow> _rows = new List<UiRow>();
    private OVRCameraRig _rig;
    private Camera _eyeCamera;
    private GameObject _root, _panelRoot;
    private RectTransform _content;
    private BoxCollider _tabCol;
    private Text _tabText;
    private Image _tabDot;               // 生成中时把手上亮一个小圆点
    private Text _statusText;
    private LineRenderer _laser;
    private int _hover = -1;             // >=0 命中行；-2 命中把手
    private bool _expanded;

    private Page _page = Page.Main;
    private string _openDim;

    private OptionsResp _opt;
    private ControlResp _ctl;
    private int _appliedVersion = -1;
    private string _appliedLang = "";
    private float _lastControlOkTime = -999f;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Bootstrap()
    {
        var go = new GameObject("~VRStimulusControlPanel");
        go.AddComponent<VRStimulusControlPanel>();
        DontDestroyOnLoad(go);
    }

    // ── 语言 ────────────────────────────────────────────────────────
    private string Lang
    {
        get
        {
            if (_ctl != null && !string.IsNullOrEmpty(_ctl.lang)) return _ctl.lang;
            switch (fallbackLanguage)
            {
                case FallbackLang.Japanese: return "ja";
                case FallbackLang.English: return "en";
                default: return "zh";
            }
        }
    }

    private string L(string zh, string ja, string en)
    {
        switch (Lang)
        {
            case "ja": return ja;
            case "en": return en;
            default: return zh;
        }
    }

    private string OptLabel(Opt o)
    {
        if (o == null) return "";
        switch (Lang) { case "ja": return o.ja; case "en": return o.en; default: return o.zh; }
    }

    private string DimLabel(SceneDim d)
    {
        if (d == null) return "";
        switch (Lang) { case "ja": return d.ja; case "en": return d.en; default: return d.zh; }
    }

    private IEnumerator Start()
    {
        float deadline = Time.time + 10f;
        while (_rig == null && Time.time < deadline)
        {
            _rig = FindObjectOfType<OVRCameraRig>();
            if (_rig != null) break;
            yield return new WaitForSeconds(0.2f);
        }
        if (_rig == null)
        {
            Debug.LogError("[CtrlPanel] 10 秒内没找到 OVRCameraRig，控制台不启动。");
            yield break;
        }
        _eyeCamera = _rig.centerEyeAnchor != null
            ? _rig.centerEyeAnchor.GetComponent<Camera>() : null;
        if (_eyeCamera == null) _eyeCamera = Camera.main;

        BuildShell();
        SetExpanded(expandedOnStart);
        StartCoroutine(RetryOptionsLoop());
        StartCoroutine(PollLoop());
    }

    // ── 网络 ─────────────────────────────────────────────────────────
    private string Base => BackendConfig.Resolve(backendBaseUrl);

    private IEnumerator FetchOptions()
    {
        using (var req = UnityWebRequest.Get(Base + "/api/stimulus/options"))
        {
            req.timeout = 8;
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"[CtrlPanel] 拉取 options 失败：{req.error}");
                yield break;
            }
            _lastControlOkTime = Time.time;
            try { _opt = JsonUtility.FromJson<OptionsResp>(req.downloadHandler.text); }
            catch (System.Exception e) { Debug.LogWarning($"[CtrlPanel] options 解析失败：{e.Message}"); }
        }
    }

    private IEnumerator RetryOptionsLoop()
    {
        while (_opt == null)
        {
            yield return StartCoroutine(FetchOptions());
            if (_opt != null) break;
            yield return new WaitForSeconds(3f);
        }
        if (_expanded) BuildPage();
    }

    private IEnumerator PollLoop()
    {
        var wait = new WaitForSeconds(Mathf.Max(0.2f, pollSeconds));
        while (true)
        {
            // client=vr 让后端把这次轮询记成「头显在线」心跳——外网下服务器连不进
            // 头显的 NAT，应用自己发出去的请求是唯一可靠的在线信号。
            using (var req = UnityWebRequest.Get(Base + "/api/stimulus/control?client=vr"))
            {
                req.timeout = 5;
                yield return req.SendWebRequest();
                if (req.result == UnityWebRequest.Result.Success)
                {
                    ControlResp c = null;
                    try { c = JsonUtility.FromJson<ControlResp>(req.downloadHandler.text); }
                    catch { c = null; }
                    if (c != null)
                    {
                        _ctl = c;
                        _lastControlOkTime = Time.time;
                        if (c.version != _appliedVersion || Lang != _appliedLang)
                        {
                            _appliedVersion = c.version;
                            _appliedLang = Lang;
                            UpdateTab();
                            if (_expanded) BuildPage();
                        }
                    }
                }
            }
            RefreshStatus();
            UpdateTabDot();
            yield return wait;
        }
    }

    private void Post(string jsonFields) => StartCoroutine(PostRoutine(jsonFields));

    private IEnumerator PostRoutine(string jsonFields)
    {
        string body = "{" + jsonFields + ",\"source\":\"vr\"}";
        using (var req = new UnityWebRequest(Base + "/api/stimulus/control", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 5;
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"[CtrlPanel] 提交失败：{req.error}");
                yield break;
            }
            ControlResp c = null;
            try { c = JsonUtility.FromJson<ControlResp>(req.downloadHandler.text); }
            catch { c = null; }
            if (c != null)
            {
                _ctl = c;
                _appliedVersion = c.version;
                _appliedLang = Lang;
                _lastControlOkTime = Time.time;
                UpdateTab();
                if (_expanded) BuildPage();
            }
        }
    }

    // ── 基础绘制原语 ──────────────────────────────────────────────────
    // radius 超过半高会自动收成药丸 / 圆形，所以把手、状态药丸、圆点直接传 999
    private Image MakeCard(Transform parent, Vector2 pos, Vector2 size, Color color, float radius = 16f)
    {
        var go = new GameObject("Card");
        go.transform.SetParent(parent, false);
        var rect = go.AddComponent<RectTransform>();
        rect.sizeDelta = size;
        rect.anchoredPosition = pos;
        var img = go.AddComponent<Image>();
        ModernUI.Round(img, radius);
        img.color = color;
        return img;
    }

    private Text MakeText(Transform parent, Vector2 pos, Vector2 size, string content,
                          int fontSize, TextAnchor anchor, Color color)
    {
        var go = new GameObject("Text");
        go.transform.SetParent(parent, false);
        var rect = go.AddComponent<RectTransform>();
        rect.sizeDelta = size;
        rect.anchoredPosition = pos;
        var t = go.AddComponent<Text>();
        t.text = content;
        t.font = VREmotionSelectionUI.ResolveFont();
        t.fontSize = fontSize;
        t.alignment = anchor;
        t.color = color;
        t.horizontalOverflow = HorizontalWrapMode.Overflow;
        t.verticalOverflow = VerticalWrapMode.Overflow;
        return t;
    }

    // ── 外壳 ─────────────────────────────────────────────────────────
    private void BuildShell()
    {
        var canvasGO = new GameObject("StimulusControlCanvas");
        var canvas = canvasGO.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = _eyeCamera;
        canvasGO.AddComponent<CanvasScaler>();
        canvasGO.AddComponent<GraphicRaycaster>();

        var canvasRect = canvasGO.GetComponent<RectTransform>();
        canvasRect.sizeDelta = new Vector2(CanvasW, CanvasH);
        canvasRect.localScale = Vector3.one * (panelWidth / PanelW);
        _root = canvasGO;
        DontDestroyOnLoad(canvasGO);

        // 左侧把手：圆角药丸 + 竖排文字 + 运行指示点
        var tabImg = MakeCard(canvasRect, new Vector2(-CanvasW / 2f + TabW / 2f, 0f),
                              new Vector2(TabW - 16f, 340f), CTab, 999f);
        _tabCol = tabImg.gameObject.AddComponent<BoxCollider>();
        _tabCol.size = new Vector3(TabW - 16f, 340f, 6f);
        _tabCol.isTrigger = true;
        _tabText = MakeText(tabImg.transform, new Vector2(0f, -14f), new Vector2(TabW - 26f, 280f),
                            "", 28, TextAnchor.MiddleCenter, Color.white);
        _tabDot = MakeCard(tabImg.transform, new Vector2(0f, 132f), new Vector2(16f, 16f), CSuccess, 999f);
        _tabDot.gameObject.SetActive(false);

        // 面板本体
        _panelRoot = new GameObject("PanelRoot");
        _panelRoot.transform.SetParent(canvasRect, false);
        var panelRect = _panelRoot.AddComponent<RectTransform>();
        panelRect.sizeDelta = new Vector2(PanelW, CanvasH);
        panelRect.anchoredPosition = new Vector2(RowX, 0f);

        var bg = MakeCard(panelRect, Vector2.zero, new Vector2(PanelW, CanvasH), CBg, 28f);
        bg.transform.SetAsFirstSibling();

        var content = new GameObject("Content");
        content.transform.SetParent(panelRect, false);
        _content = content.AddComponent<RectTransform>();
        _content.sizeDelta = new Vector2(PanelW, CanvasH);

        _statusText = MakeText(panelRect, new Vector2(0f, -CanvasH / 2f + 44f),
            new Vector2(RowW, 70f), "", 21, TextAnchor.MiddleCenter, CMuted);

        BuildLaser();
        UpdateTab();
        PlaceNow();
    }

    private void UpdateTab()
    {
        if (_tabText == null) return;
        string label = _expanded ? L("收起", "閉じる", "CLOSE") : L("菜单", "メニュー", "MENU");
        var sb = new System.Text.StringBuilder("☰\n\n");
        foreach (char c in label) { sb.Append(c); sb.Append('\n'); }
        _tabText.text = sb.ToString().TrimEnd('\n');
        UpdateTabDot();
    }

    private void UpdateTabDot()
    {
        if (_tabDot == null) return;
        bool running = _ctl != null && _ctl.running;
        if (_tabDot.gameObject.activeSelf != running) _tabDot.gameObject.SetActive(running);
    }

    private void BuildLaser()
    {
        var go = new GameObject("CtrlLaser");
        go.transform.SetParent(transform, false);
        _laser = go.AddComponent<LineRenderer>();
        _laser.positionCount = 2;
        _laser.widthMultiplier = 0.005f;
        _laser.useWorldSpace = true;
        var sh = Shader.Find("Sprites/Default") ?? Shader.Find("Unlit/Color");
        if (sh != null) _laser.material = new Material(sh);
        _laser.startColor = _laser.endColor = CAccent;
        _laser.enabled = false;
    }

    // ── 摆位（一次性，不跟随）────────────────────────────────────────
    /// <summary>把菜单摆到当前视野左前方并**钉死**在那里；只在启动和按键唤出时调用。</summary>
    private void PlaceNow()
    {
        if (_root == null || _rig == null || _rig.centerEyeAnchor == null) return;
        Transform eye = _rig.centerEyeAnchor;
        Vector3 fwd = eye.forward;
        fwd.y = 0f;
        if (fwd.sqrMagnitude < 0.0001f) fwd = Vector3.forward;
        fwd.Normalize();
        Vector3 right = Vector3.Cross(Vector3.up, fwd).normalized;
        Vector3 pos = eye.position + fwd * panelDistance - right * lateralOffset
                      + Vector3.up * verticalOffset;
        _root.transform.SetPositionAndRotation(pos, Quaternion.LookRotation(fwd, Vector3.up));
    }

    // ── 组件：页头 / 分区 / 行 ────────────────────────────────────────
    private void AddHeader(ref float y, string title)
    {
        MakeText(_content, new Vector2(-RowW / 2f + Pad + 150f, y - 28f), new Vector2(300f, 46f),
                 title, 30, TextAnchor.MiddleLeft, CText);

        bool running = _ctl != null && _ctl.running;
        // 状态药丸：一眼看出在不在跑
        var pill = MakeCard(_content, new Vector2(RowW / 2f - Pad - 66f, y - 28f),
                            new Vector2(132f, 42f),
                            running ? new Color(CSuccess.r, CSuccess.g, CSuccess.b, 0.20f)
                                    : new Color(CMuted.r, CMuted.g, CMuted.b, 0.16f), 999f);
        MakeText(pill.transform, Vector2.zero, new Vector2(126f, 40f),
                 running ? L("生成中", "生成中", "RUNNING") : L("已停止", "停止中", "IDLE"),
                 21, TextAnchor.MiddleCenter, running ? CSuccess : CMuted);
        y -= 56f + 14f;

        MakeCard(_content, new Vector2(0f, y - 1f), new Vector2(RowW, 2f), CDivider);
        y -= 2f + 12f;
    }

    private void AddSection(ref float y, string title)
    {
        MakeText(_content, new Vector2(-RowW / 2f + Pad + 130f, y - HSection / 2f),
                 new Vector2(280f, HSection), title, 20, TextAnchor.MiddleLeft, CMuted);
        y -= HSection + GSection;
    }

    /// <summary>主操作：整条实心强调色，页面上唯一的高饱和块。</summary>
    private void AddPrimary(ref float y, string label, bool running, System.Action onClick)
    {
        var img = MakeCard(_content, new Vector2(0f, y - HPrimary / 2f), new Vector2(RowW, HPrimary),
                           running ? CDanger : CAccent);
        var col = img.gameObject.AddComponent<BoxCollider>();
        col.size = new Vector3(RowW, HPrimary, 6f);
        col.isTrigger = true;
        MakeText(img.transform, Vector2.zero, new Vector2(RowW - 40f, HPrimary), label, 30,
                 TextAnchor.MiddleCenter, Color.white);
        _rows.Add(new UiRow { Col = col, Img = img, OnClick = onClick, BaseColor = img.color });
        y -= HPrimary + GBlock;
    }

    /// <summary>设置行：左标签（次要）／右取值（主要）＋ 尾部 ›。</summary>
    private void AddSettingRow(ref float y, string label, string value, System.Action onClick)
    {
        var img = MakeCard(_content, new Vector2(0f, y - HRow / 2f), new Vector2(RowW, HRow), CCard);
        var col = img.gameObject.AddComponent<BoxCollider>();
        col.size = new Vector3(RowW, HRow, 6f);
        col.isTrigger = true;

        MakeText(img.transform, new Vector2(-RowW / 2f + Pad + 140f, 0f), new Vector2(280f, HRow),
                 label, 24, TextAnchor.MiddleLeft, CMuted);
        MakeText(img.transform, new Vector2(RowW / 2f - Pad - 24f - 130f, 0f), new Vector2(260f, HRow),
                 value, 26, TextAnchor.MiddleRight, CText);
        MakeText(img.transform, new Vector2(RowW / 2f - Pad - 6f, 0f), new Vector2(24f, HRow),
                 "›", 30, TextAnchor.MiddleRight, CMuted);

        _rows.Add(new UiRow { Col = col, Img = img, OnClick = onClick, BaseColor = CCard });
        y -= HRow + GRow;
    }

    /// <summary>候选项行：选中时软强调底 + 左侧强调竖条 + 右侧 ✓。</summary>
    private void AddOptionRow(ref float y, string label, bool selected, System.Action onClick,
                              float height = HOpt)
    {
        Color baseCol = selected ? CAccentSoft : CCard;
        var img = MakeCard(_content, new Vector2(0f, y - height / 2f), new Vector2(RowW, height), baseCol);
        var col = img.gameObject.AddComponent<BoxCollider>();
        col.size = new Vector3(RowW, height, 6f);
        col.isTrigger = true;

        if (selected)
            MakeCard(img.transform, new Vector2(-RowW / 2f + 10f, 0f),
                     new Vector2(6f, height - 20f), CAccent);

        MakeText(img.transform, new Vector2(-RowW / 2f + Pad + 160f, 0f), new Vector2(320f, height),
                 label, 26, TextAnchor.MiddleLeft, selected ? CText : CMuted);
        if (selected)
            MakeText(img.transform, new Vector2(RowW / 2f - Pad - 12f, 0f), new Vector2(40f, height),
                     "✓", 28, TextAnchor.MiddleRight, CAccent);

        _rows.Add(new UiRow { Col = col, Img = img, OnClick = onClick, Selected = selected, BaseColor = baseCol });
        y -= height + GRow;
    }

    private void AddBackRow(ref float y)
    {
        var img = MakeCard(_content, new Vector2(0f, y - HBack / 2f), new Vector2(RowW, HBack),
                           new Color(CCard.r, CCard.g, CCard.b, 0.7f));
        var col = img.gameObject.AddComponent<BoxCollider>();
        col.size = new Vector3(RowW, HBack, 6f);
        col.isTrigger = true;
        MakeText(img.transform, new Vector2(-RowW / 2f + Pad + 120f, 0f), new Vector2(240f, HBack),
                 "‹  " + L("返回", "戻る", "Back"), 24, TextAnchor.MiddleLeft, CMuted);
        _rows.Add(new UiRow { Col = col, Img = img, OnClick = GoMain, BaseColor = img.color });
        y -= HBack + GBlock;
    }

    private void AddPageTitle(ref float y, string title)
    {
        MakeText(_content, new Vector2(-RowW / 2f + Pad + 180f, y - 24f), new Vector2(360f, 48f),
                 title, 28, TextAnchor.MiddleLeft, CText);
        y -= 48f + 12f;
    }

    // ── 换页 ─────────────────────────────────────────────────────────
    private void ClearContent()
    {
        _rows.Clear();
        _hover = -1;
        if (_content == null) return;
        for (int i = _content.childCount - 1; i >= 0; i--)
            Destroy(_content.GetChild(i).gameObject);
    }

    private void BuildPage()
    {
        if (_content == null) return;
        ClearContent();
        float y = CanvasH / 2f - 40f;

        if (_opt == null) { BuildOffline(ref y); ApplyRowColors(); RefreshStatus(); return; }

        switch (_page)
        {
            case Page.Main: BuildMain(ref y); break;
            case Page.Emotion:
                BuildOptionPage(ref y, L("目标情绪", "目標感情", "Target emotion"), _opt.emotions,
                    () => _ctl != null ? _ctl.emotion : "",
                    key => Post("\"emotion\":\"" + key + "\""));
                break;
            case Page.Mode:
                BuildOptionPage(ref y, L("调制模式", "調節モード", "Modulation mode"), _opt.modes,
                    () => _ctl != null ? _ctl.mode : "",
                    key => Post("\"mode\":\"" + key + "\""));
                break;
            case Page.Scene: BuildScenePage(ref y); break;
            case Page.Intensity:
                BuildNumberPage(ref y, L("目标强度", "目標強度", "Target intensity"),
                    new float[] { 0.2f, 0.3f, 0.4f, 0.5f, 0.6f, 0.7f, 0.8f, 0.9f, 1.0f },
                    v => v.ToString("0.0"),
                    () => _ctl != null ? _ctl.target_intensity : 0.6f,
                    v => Post("\"target_intensity\":" + Inv(v, "0.##")));
                break;
            case Page.Window:
                BuildNumberPage(ref y, L("观察窗（秒）", "観察ウィンドウ（秒）", "Measure window (s)"),
                    new float[] { 4f, 6f, 8f, 10f, 12f, 15f, 20f, 30f },
                    v => v.ToString("0") + " s",
                    () => _ctl != null ? _ctl.measure_window_s : 6f,
                    v => Post("\"measure_window_s\":" + Inv(v, "0.#")));
                break;
        }

        ApplyRowColors();
        RefreshStatus();
    }

    private static string Inv(float v, string fmt) =>
        v.ToString(fmt, System.Globalization.CultureInfo.InvariantCulture);

    private void BuildOffline(ref float y)
    {
        AddPageTitle(ref y, L("刺激生成控制台", "刺激生成コントロール", "Stimulus Control"));
        var card = MakeCard(_content, new Vector2(0f, y - 150f), new Vector2(RowW, 300f), CCard);
        MakeText(card.transform, Vector2.zero, new Vector2(RowW - 40f, 280f),
            // 现场排查顺序：先看头显自己的网络，再看服务器——adb / 反向隧道那套已经不用了，
            // 头显是直接连公网地址的，写旧步骤只会把人带偏。
            L("连不上后端\n" + Base + "\n\n1. 头显是否已连上笔记本的 WiFi\n2. 笔记本是否能上网\n3. 服务器上 start_sys.bat 是否在跑\n\n每 3 秒自动重试",
              "バックエンドに接続できません\n" + Base + "\n\n1. ヘッドセットがノート PC の WiFi に接続済みか\n2. ノート PC がインターネットに出られるか\n3. サーバーで start_sys.bat が動いているか\n\n3 秒ごとに自動再試行",
              "Cannot reach backend\n" + Base + "\n\n1. Is the headset on the laptop's WiFi?\n2. Does the laptop have internet?\n3. Is start_sys.bat running on the server?\n\nRetrying every 3 s"),
            22, TextAnchor.MiddleCenter, CMuted);
        y -= 300f + GBlock;
        AddSettingRow(ref y, L("立即重试", "今すぐ再試行", "Retry now"), "",
                      () => StartCoroutine(RetryNow()));
    }

    private IEnumerator RetryNow()
    {
        yield return StartCoroutine(FetchOptions());
        BuildPage();
    }

    private void BuildMain(ref float y)
    {
        AddHeader(ref y, L("刺激生成", "刺激生成", "Stimulus"));

        bool running = _ctl != null && _ctl.running;
        AddPrimary(ref y,
            running ? L("■  结束生成", "■  生成を終了", "■  STOP")
                    : L("▶  开始生成", "▶  生成を開始", "▶  START"),
            running, () => Post("\"running\":" + (running ? "false" : "true")));

        // 随机生成：情绪 + 场景全随机再开跑。生成中不显示——那时该点的是上面的「结束生成」。
        if (!running)
            AddSettingRow(ref y, L("随机生成", "ランダム生成", "Random start"), "🎲",
                          RandomizeAndStart);

        AddSection(ref y, L("刺激种子", "刺激シード", "STIMULUS SEED"));
        AddSettingRow(ref y, L("目标情绪", "目標感情", "Emotion"), CurrentEmotionLabel(),
                      () => Go(Page.Emotion, null));
        if (_opt.scene_dims != null)
        {
            foreach (var d in _opt.scene_dims)
            {
                int idx = SceneIndex(d.key);
                string val = (d.items != null && idx >= 0 && idx < d.items.Length)
                    ? OptLabel(d.items[idx]) : "-";
                string key = d.key;
                AddSettingRow(ref y, DimLabel(d), val, () => Go(Page.Scene, key));
            }
            AddSettingRow(ref y, L("随机组合", "ランダム", "Randomize"), "🎲", RandomizeScene);
        }

        AddSection(ref y, L("闭环参数", "クローズドループ", "CLOSED LOOP"));
        AddSettingRow(ref y, L("调制模式", "モード", "Mode"), CurrentModeLabel(),
                      () => Go(Page.Mode, null));
        AddSettingRow(ref y, L("目标强度", "目標強度", "Intensity"),
                      (_ctl != null ? _ctl.target_intensity : 0.6f).ToString("0.00"),
                      () => Go(Page.Intensity, null));
        AddSettingRow(ref y, L("观察窗", "観察ウィンドウ", "Window"),
                      (_ctl != null ? _ctl.measure_window_s : 6f).ToString("0") + " s",
                      () => Go(Page.Window, null));
    }

    private void Go(Page p, string dim)
    {
        _page = p;
        _openDim = dim;
        BuildPage();
    }

    private void GoMain() => Go(Page.Main, null);

    private string CurrentEmotionLabel()
    {
        if (_opt.emotions == null || _ctl == null) return "-";
        foreach (var e in _opt.emotions) if (e.key == _ctl.emotion) return OptLabel(e);
        return _ctl.emotion ?? "-";
    }

    private string CurrentModeLabel()
    {
        if (_opt.modes == null || _ctl == null) return "-";
        foreach (var m in _opt.modes) if (m.key == _ctl.mode) return OptLabel(m);
        return _ctl.mode ?? "-";
    }

    private void BuildOptionPage(ref float y, string title, Opt[] items,
                                 System.Func<string> current, System.Action<string> pick)
    {
        AddPageTitle(ref y, title);
        AddBackRow(ref y);
        string cur = current != null ? current() : "";
        if (items == null) return;
        foreach (var it in items)
        {
            string key = it.key;
            AddOptionRow(ref y, OptLabel(it), key == cur, () => { pick(key); GoMain(); });
        }
    }

    private void BuildScenePage(ref float y)
    {
        SceneDim dim = null;
        if (_opt.scene_dims != null)
            foreach (var d in _opt.scene_dims) if (d.key == _openDim) { dim = d; break; }
        if (dim == null) { GoMain(); return; }

        AddPageTitle(ref y, DimLabel(dim));
        AddBackRow(ref y);
        int cur = SceneIndex(dim.key);
        string dkey = dim.key;
        for (int i = 0; i < dim.items.Length; i++)
        {
            int idx = i;
            AddOptionRow(ref y, OptLabel(dim.items[i]), idx == cur,
                         () => { Post("\"scene\":{\"" + dkey + "\":" + idx + "}"); GoMain(); });
        }
    }

    private void BuildNumberPage(ref float y, string title, float[] values,
                                 System.Func<float, string> fmt,
                                 System.Func<float> current, System.Action<float> pick)
    {
        AddPageTitle(ref y, title);
        AddBackRow(ref y);
        float cur = current();
        foreach (float v in values)
        {
            float val = v;
            AddOptionRow(ref y, fmt(v), Mathf.Abs(val - cur) < 0.001f,
                         () => { pick(val); GoMain(); });
        }
    }

    private int SceneIndex(string dim)
    {
        if (_ctl == null || _ctl.scene == null) return 0;
        switch (dim)
        {
            case "time": return _ctl.scene.time;
            case "place": return _ctl.scene.place;
            case "task": return _ctl.scene.task;
            case "event": return _ctl.scene.@event;
            default: return 0;
        }
    }

    /// <summary>场景四要素随机，拼成 "scene":{...} 片段；没有选项表时返回空串。</summary>
    private string RandomSceneJson()
    {
        if (_opt.scene_dims == null) return "";
        var sb = new System.Text.StringBuilder("\"scene\":{");
        bool first = true;
        foreach (var d in _opt.scene_dims)
        {
            int n = d.items != null ? d.items.Length : 0;
            if (n <= 0) continue;
            if (!first) sb.Append(',');
            sb.Append('"').Append(d.key).Append("\":").Append(Random.Range(0, n));
            first = false;
        }
        sb.Append('}');
        return first ? "" : sb.ToString();
    }

    private void RandomizeScene()
    {
        string scene = RandomSceneJson();
        if (scene.Length > 0) Post(scene);
    }

    /// <summary>整套种子随机（目标情绪 + 场景四要素）并立刻开始生成。
    ///
    /// 一次 POST 全带上：分成多次发的话，后端每次都会自增 version，网页会看到
    /// 「改情绪」「改场景」「开始」三个中间态，可能拿着还没换完的种子就起跑了。
    /// 情绪从后端选项表里抽，不在这里写死七个 key。</summary>
    private void RandomizeAndStart()
    {
        var sb = new System.Text.StringBuilder();
        if (_opt.emotions != null && _opt.emotions.Length > 0)
        {
            var e = _opt.emotions[Random.Range(0, _opt.emotions.Length)];
            sb.Append("\"emotion\":\"").Append(e.key).Append('"');
        }
        string scene = RandomSceneJson();
        if (scene.Length > 0)
        {
            if (sb.Length > 0) sb.Append(',');
            sb.Append(scene);
        }
        if (sb.Length > 0) sb.Append(',');
        sb.Append("\"running\":true");
        Post(sb.ToString());
    }

    private void RefreshStatus()
    {
        if (_statusText == null) return;
        if (Time.time - _lastControlOkTime > Mathf.Max(3f, pollSeconds * 4f))
        {
            _statusText.text = L("后端未连接", "バックエンド未接続", "Backend not reachable");
            _statusText.color = new Color(1f, 0.55f, 0.35f);
            return;
        }
        bool running = _ctl != null && _ctl.running;
        string who = (_ctl != null && _ctl.source == "vr") ? "VR" : "PC";
        _statusText.text = running
            ? L($"需网页保持打开 · 最后修改 {who}",
                $"PC の網頁が必要 · 最終変更 {who}",
                $"PC page required · last {who}")
            : L($"最后修改 {who}", $"最終変更 {who}", $"last change {who}");
        _statusText.color = CMuted;
    }

    private void ApplyRowColors()
    {
        for (int i = 0; i < _rows.Count; i++)
        {
            var r = _rows[i];
            if (r.Img == null) continue;
            ModernUI.SetState(r.Img, i == _hover ? CCardHi : r.BaseColor, i == _hover);
        }
        if (_tabCol != null)
            ModernUI.SetState(_tabCol.GetComponent<Image>(), _hover == -2 ? CCardHi : CTab, _hover == -2);
    }

    // ── 展开 / 收起 ───────────────────────────────────────────────────
    private void SetExpanded(bool v)
    {
        _expanded = v;
        if (_panelRoot != null) _panelRoot.SetActive(v);
        UpdateTab();
        if (v) { _page = Page.Main; _openDim = null; BuildPage(); }
        else ClearContent();
    }

    private void Update()
    {
        if (_root == null || _rig == null) return;

        bool toggle = OVRInput.GetDown(OVRInput.Button.Two)
                   || OVRInput.GetDown(OVRInput.Button.Start);
        if (Keyboard.current != null)
            toggle = toggle || Keyboard.current.tabKey.wasPressedThisFrame;
        if (toggle)
        {
            // 菜单钉在世界里，转过身就看不见了；按键唤出时重新摆到眼前才够得着。
            // 收起时不动——收起的人还在原地看着它。
            if (!_expanded) PlaceNow();
            SetExpanded(!_expanded);
        }

        var connected = OVRInput.GetConnectedControllers();
        bool usingController = (connected & (OVRInput.Controller.RTouch
                                           | OVRInput.Controller.LTouch
                                           | OVRInput.Controller.Touch)) != 0;

        Ray ray;
        bool click;
        if (usingController)
        {
            Transform aim = PointerAnchor();
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

        int hover = -1;
        Vector3 hitPoint = ray.origin + ray.direction * 1.5f;
        if (Physics.Raycast(ray, out RaycastHit hit, 20f))
        {
            hitPoint = hit.point;
            if (_tabCol != null && hit.collider == _tabCol) hover = -2;
            else
                for (int i = 0; i < _rows.Count; i++)
                    if (_rows[i].Col == hit.collider) { hover = i; break; }
        }

        if (hover != _hover) { _hover = hover; ApplyRowColors(); }

        if (_laser != null)
        {
            _laser.enabled = usingController;
            if (usingController)
            {
                _laser.SetPosition(0, ray.origin);
                _laser.SetPosition(1, hitPoint);
            }
        }

        if (!click) return;
        if (hover == -2) { ModernUI.Press(_tabCol.GetComponent<Image>()); SetExpanded(!_expanded); return; }
        if (hover >= 0 && hover < _rows.Count)
        {
            ModernUI.Press(_rows[hover].Img);
            var act = _rows[hover].OnClick;   // 回调可能重建整页，先取出再调用
            if (act != null) act();
        }
    }

    // 菜单在左边，右手点最顺
    private Transform PointerAnchor()
    {
        if (_rig == null) return null;
        var c = OVRInput.GetConnectedControllers();
        if ((c & OVRInput.Controller.RTouch) != 0 && _rig.rightHandAnchor != null)
            return _rig.rightHandAnchor;
        if ((c & OVRInput.Controller.LTouch) != 0 && _rig.leftHandAnchor != null)
            return _rig.leftHandAnchor;
        return _rig.centerEyeAnchor;
    }

    // ── JSON（与 backend/src/use_web/stimulus_control.py 对应）──
    [System.Serializable]
    private class Opt
    {
        public string key;
        public string zh;
        public string ja;
        public string en;
    }

    [System.Serializable]
    private class SceneDim
    {
        public string key;
        public string zh;
        public string ja;
        public string en;
        public Opt[] items;
    }

    [System.Serializable]
    private class OptionsResp
    {
        public Opt[] emotions;
        public Opt[] modes;
        public SceneDim[] scene_dims;
    }

    [System.Serializable]
    private class SceneSel
    {
        public int time;
        public int place;
        public int task;
        public int @event;      // event 是 C# 关键字，用 @ 转义；序列化名仍是 "event"
    }

    [System.Serializable]
    private class ControlResp
    {
        public bool running;
        public string lang;
        public string emotion;
        public string mode;
        public float target_intensity;
        public float measure_window_s;
        public SceneSel scene;
        public int seed;
        public string subject_id;
        public int version;
        public string source;
    }
}
