// RatingPanel.cs
// 自评面板（从 StimulusPlayer 搬出），两种模式由 stimulus.rating 决定：
//
//   full   SAM 面板（原样）：效价 1–9、唤醒 1–9、7 类情绪、强度 1–5，点「确定」提交。
//          情绪是否必选 / 强度是否出现由后端 rating_opts 决定。
//   quick  事件类 / 模仿类试次用的快速面板：9×9 情感网格（x = 效价 1..9 左→右，
//          y = 唤醒 1..9 下→上）+ 7 个情绪按钮。选了格子、且（require_label7 时）选了情绪
//          就自动提交，没有确定按钮。最后一次点击后留 0.5 秒让受试者看到高亮 / 改选，再提交。
//   none   后端直接跳过自评阶段，不会走到这里。
//
// POST /api/stimlib/rating 的 body 带 mode（full | quick）；quick 的 intensity 恒为 0。
// 交互：手柄射线（扳机 / A 键点击）或编辑器里的鼠标。

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.UI;

public class RatingPanel
{
    private class Btn { public Image img; public BoxCollider col; public string kind; public int idx; public Text label; public bool quick; }

    private static readonly Color BtnBase = ModernUI.Surface;
    private static readonly Color BtnHover = ModernUI.Accent;
    private static readonly Color BtnSel = ModernUI.Success;

    private const float QuickSubmitDelay = 0.5f;

    private readonly MonoBehaviour _host;
    private readonly OVRCameraRig _rig;
    private readonly Camera _eye;
    private readonly System.Func<string> _lang;
    private readonly System.Action<GameObject, float, float> _placeInFront;
    private readonly float _panelDistance;

    private GameObject _fullRoot, _quickRoot;
    private Text _fullTitle, _fullStatus, _lblI, _quickTitle, _quickStatus;
    private readonly List<Btn> _btns = new List<Btn>();
    private LineRenderer _laser;

    private string _mode = "full";
    private int _selV, _selA, _selE = -1, _selI;
    private bool _askIntensity, _needLabel7;
    private string _ratingFor = "";
    private float _shownAt;
    private bool _submitting;
    private float _quickSubmitAt = -1f;

    public bool Visible => (_fullRoot != null && _fullRoot.activeSelf) || (_quickRoot != null && _quickRoot.activeSelf);

    private string L(string zh, string ja, string en) => StimText.L(_lang(), zh, ja, en);

    public RatingPanel(MonoBehaviour host, OVRCameraRig rig, Camera eye, float panelWidth, float panelDistance,
                       System.Func<string> lang, System.Action<GameObject, float, float> placeInFront)
    {
        _host = host; _rig = rig; _eye = eye; _lang = lang; _placeInFront = placeInFront;
        _panelDistance = panelDistance;
        BuildFull(panelWidth);
        BuildQuick(panelWidth);

        var lgo = new GameObject("StimPlayerPointer");
        _laser = lgo.AddComponent<LineRenderer>();
        _laser.widthMultiplier = 0.005f; _laser.positionCount = 2; _laser.useWorldSpace = true;
        var sh = Shader.Find("Sprites/Default") ?? Shader.Find("Unlit/Color");
        if (sh != null) _laser.material = new Material(sh);
        _laser.startColor = _laser.endColor = new Color(0.4f, 0.7f, 1f, 0.9f);
        _laser.enabled = false;
    }

    private RectTransform MakeRoot(string name, float panelWidth, out GameObject root)
    {
        root = new GameObject(name);
        var canvas = root.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = _eye;
        var rect = root.GetComponent<RectTransform>();
        rect.sizeDelta = new Vector2(2400f, 1150f);
        root.transform.localScale = Vector3.one * (panelWidth / 2400f);

        var bg = new GameObject("Bg");
        bg.transform.SetParent(rect, false);
        var bgr = bg.AddComponent<RectTransform>();
        bgr.anchorMin = Vector2.zero; bgr.anchorMax = Vector2.one; bgr.offsetMin = bgr.offsetMax = Vector2.zero;
        var bgi = bg.AddComponent<Image>();
        ModernUI.Round(bgi, 40f);
        bgi.color = new Color(0.06f, 0.07f, 0.09f, 0.85f);
        return rect;
    }

    // ── full：SAM 面板（布局与旧版完全相同）──
    private void BuildFull(float panelWidth)
    {
        var rect = MakeRoot("StimPlayerRating", panelWidth, out _fullRoot);
        _fullTitle = StimUi.MakeText(rect, "", 42, new Vector2(0, 500), new Vector2(2300, 90), Color.white);
        StimUi.MakeText(rect, "", 34, new Vector2(-1040, 410), new Vector2(300, 60), Color.white, TextAnchor.MiddleLeft).name = "LblV";
        Row(rect, "valence", 9, new Vector2(0, 340));
        StimUi.MakeText(rect, "", 34, new Vector2(-1040, 230), new Vector2(300, 60), Color.white, TextAnchor.MiddleLeft).name = "LblA";
        Row(rect, "arousal", 9, new Vector2(0, 160));
        StimUi.MakeText(rect, "", 34, new Vector2(-1040, 50), new Vector2(600, 60), Color.white, TextAnchor.MiddleLeft).name = "LblE";
        Row(rect, "emotion", 7, new Vector2(0, -20));
        _lblI = StimUi.MakeText(rect, "", 34, new Vector2(-1040, -130), new Vector2(600, 60), Color.white, TextAnchor.MiddleLeft);
        _lblI.name = "LblI";
        Row(rect, "intensity", 5, new Vector2(0, -200));
        var ok = MakeButton(rect, "confirm", 0, "", new Vector2(0, -420), new Vector2(380, 120), false);
        ok.label.name = "LblOk";
        _fullStatus = StimUi.MakeText(rect, "", 32, new Vector2(760, -420), new Vector2(900, 100), new Color(1f, 0.75f, 0.4f));
        _fullRoot.SetActive(false);
    }

    private void Row(RectTransform parent, string kind, int n, Vector2 center)
    {
        float w = kind == "emotion" ? 290f : 170f, h = 120f, sp = 20f;
        float total = n * w + (n - 1) * sp;
        float x0 = -total / 2f + w / 2f;
        for (int i = 0; i < n; i++)
            MakeButton(parent, kind, i, kind == "emotion" ? "" : (i + 1).ToString(),
                new Vector2(center.x + x0 + i * (w + sp), center.y), new Vector2(w, h), false);
    }

    // ── quick：9×9 情感网格 + 右侧一列 7 个情绪按钮 ──
    // 网格格子 idx = (arousal-1)*9 + (valence-1)；唤醒 9 在最上面。
    private void BuildQuick(float panelWidth)
    {
        var rect = MakeRoot("StimPlayerRatingQuick", panelWidth, out _quickRoot);
        _quickTitle = StimUi.MakeText(rect, "", 40, new Vector2(0, 515), new Vector2(2300, 80), Color.white);

        const float cell = 88f, sp = 8f;
        Vector2 gc = new Vector2(-420f, -10f);                     // 网格中心
        float span = 9 * cell + 8 * sp;                             // 856
        float x0 = gc.x - span / 2f + cell / 2f, y0 = gc.y - span / 2f + cell / 2f;
        for (int a = 0; a < 9; a++)
            for (int v = 0; v < 9; v++)
                MakeButton(rect, "grid", a * 9 + v, "",
                    new Vector2(x0 + v * (cell + sp), y0 + a * (cell + sp)), new Vector2(cell, cell), true);

        // 轴标签：左右 = 效价，上下 = 唤醒
        var c = new Color(0.85f, 0.88f, 0.95f);
        StimUi.MakeText(rect, "", 30, new Vector2(gc.x - span / 2f - 150f, gc.y), new Vector2(260, 120), c).name = "QLblVLo";
        StimUi.MakeText(rect, "", 30, new Vector2(gc.x + span / 2f + 150f, gc.y), new Vector2(260, 120), c).name = "QLblVHi";
        StimUi.MakeText(rect, "", 30, new Vector2(gc.x, gc.y + span / 2f + 30f), new Vector2(600, 50), c).name = "QLblAHi";
        StimUi.MakeText(rect, "", 30, new Vector2(gc.x, gc.y - span / 2f - 30f), new Vector2(600, 50), c).name = "QLblALo";

        StimUi.MakeText(rect, "", 32, new Vector2(760, 440), new Vector2(600, 60), Color.white).name = "QLblE";
        for (int i = 0; i < 7; i++)
            MakeButton(rect, "qemotion", i, "", new Vector2(760, 340 - i * 112f), new Vector2(420, 100), true);
        _quickStatus = StimUi.MakeText(rect, "", 32, new Vector2(760, -500), new Vector2(800, 80), new Color(1f, 0.75f, 0.4f));
        _quickRoot.SetActive(false);
    }

    private Btn MakeButton(RectTransform parent, string kind, int idx, string label, Vector2 pos, Vector2 size, bool quick)
    {
        var go = new GameObject($"Btn_{kind}_{idx}");
        go.transform.SetParent(parent, false);
        var r = go.AddComponent<RectTransform>();
        r.anchoredPosition = pos; r.sizeDelta = size;
        var img = go.AddComponent<Image>();
        ModernUI.Round(img, kind == "grid" ? 12f : 22f);
        img.color = BtnBase;
        var col = go.AddComponent<BoxCollider>();
        col.size = new Vector3(size.x, size.y, 10f);
        var t = StimUi.MakeText(r, label, 36, Vector2.zero, size, Color.white);
        var b = new Btn { img = img, col = col, kind = kind, idx = idx, label = t, quick = quick };
        _btns.Add(b);
        return b;
    }

    private static void SetLabel(GameObject root, string name, string text)
    {
        foreach (Transform ch in root.transform)
        {
            if (ch.name != name) continue;
            var t = ch.GetComponent<Text>();
            if (t != null) t.text = text;
        }
    }

    // ── 显示 / 隐藏 ──
    /// <param name="mode">full | quick（其它值按 full）</param>
    public void Show(string stimId, string mode, bool requireLabel7, bool askIntensity)
    {
        mode = mode == "quick" ? "quick" : "full";
        if (Visible && _ratingFor == stimId && _mode == mode) return;
        HideRoots();
        _mode = mode;
        _ratingFor = stimId;
        _selV = 0; _selA = 0; _selE = -1; _selI = 0; _submitting = false; _quickSubmitAt = -1f;
        _needLabel7 = requireLabel7;
        _askIntensity = mode == "full" && askIntensity;
        string[] labels = StimText.Labels(_lang());
        GameObject root;

        if (mode == "full")
        {
            root = _fullRoot;
            _fullTitle.text = L("刚才的画面让你感觉如何？", "今の映像でどう感じましたか？", "How did the scene make you feel?");
            _fullStatus.text = "";
            SetLabel(root, "LblV", L("愉快度  1 不愉快 … 9 愉快", "快・不快  1 不快 … 9 快", "Valence  1 unpleasant … 9 pleasant"));
            SetLabel(root, "LblA", L("唤醒度  1 平静 … 9 激动", "覚醒度  1 落ち着き … 9 興奮", "Arousal  1 calm … 9 excited"));
            SetLabel(root, "LblE", _needLabel7
                ? L("最接近的情绪", "最も近い感情", "Closest emotion")
                : L("最接近的情绪（可不选）", "最も近い感情（任意）", "Closest emotion (optional)"));
            SetLabel(root, "LblI", L("这种情绪有多强  1 很弱 … 5 很强", "その感情の強さ  1 弱い … 5 強い",
                                     "How strong  1 weak … 5 strong"));
            _lblI.gameObject.SetActive(_askIntensity);
        }
        else
        {
            root = _quickRoot;
            _quickTitle.text = L("刚才的感觉？点格子：左右 = 愉快度，上下 = 唤醒度",
                                 "今の感じは？マスを選択：左右 = 快・不快、上下 = 覚醒度",
                                 "How did you feel? Pick a cell: left–right = valence, down–up = arousal");
            _quickStatus.text = "";
            SetLabel(root, "QLblVLo", L("不愉快", "不快", "Unpleasant"));
            SetLabel(root, "QLblVHi", L("愉快", "快", "Pleasant"));
            SetLabel(root, "QLblAHi", L("激动 ↑", "興奮 ↑", "Excited ↑"));
            SetLabel(root, "QLblALo", L("平静 ↓", "落ち着き ↓", "Calm ↓"));
            SetLabel(root, "QLblE", _needLabel7
                ? L("最接近的情绪", "最も近い感情", "Closest emotion")
                : L("最接近的情绪（可不选）", "最も近い感情（任意）", "Closest emotion (optional)"));
        }
        foreach (var b in _btns)
        {
            if (b.kind == "intensity") b.img.gameObject.SetActive(_askIntensity);
            if (b.kind == "emotion" || b.kind == "qemotion") b.label.text = labels[b.idx];
            if (b.kind == "confirm") b.label.text = L("确定", "確定", "Confirm");
        }
        _placeInFront(root, _panelDistance, -0.15f);
        root.SetActive(true);
        _shownAt = Time.realtimeSinceStartup;
        RefreshButtons(null);
    }

    private void HideRoots()
    {
        if (_fullRoot != null && _fullRoot.activeSelf) _fullRoot.SetActive(false);
        if (_quickRoot != null && _quickRoot.activeSelf) _quickRoot.SetActive(false);
    }

    public void Hide()
    {
        HideRoots();
        _ratingFor = "";
        _quickSubmitAt = -1f;
        if (_laser != null) _laser.enabled = false;
    }

    // ── 每帧：射线 / 鼠标交互 ──
    public void Tick()
    {
        if (!Visible) return;
        if (_quickSubmitAt > 0f && Time.realtimeSinceStartup >= _quickSubmitAt) { _quickSubmitAt = -1f; Submit(); }

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
            if (_eye == null || Mouse.current == null) return;
            ray = _eye.ScreenPointToRay(Mouse.current.position.ReadValue());
            click = Mouse.current.leftButton.wasPressedThisFrame;
        }

        // RaycastAll：场景里可能还有别的面板挡在射线上，只认本面板（当前模式）的按钮
        bool quick = _mode == "quick";
        Btn hover = null;
        Vector3 end = ray.origin + ray.direction * 3f;
        float best = float.MaxValue;
        foreach (var hit in Physics.RaycastAll(ray, 50f))
        {
            foreach (var b in _btns)
            {
                if (b.quick != quick) continue;
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
            case "intensity": _selI = b.idx + 1; break;
            case "confirm": Submit(); return;
            case "grid": _selV = b.idx % 9 + 1; _selA = b.idx / 9 + 1; break;
            case "qemotion": _selE = _selE == b.idx ? -1 : b.idx; break;
        }
        RefreshButtons(b);
        if (_mode == "quick")
        {
            // 自动提交：格子已选且（需要时）情绪已选；改选会重新计时
            bool ready = _selV > 0 && _selA > 0 && (!_needLabel7 || _selE >= 0);
            _quickSubmitAt = ready && !_submitting ? Time.realtimeSinceStartup + QuickSubmitDelay : -1f;
            if (!ready && _selV > 0 && _needLabel7)
                _quickStatus.text = L("再选一个最接近的情绪", "最も近い感情も選んでください", "Now pick the closest emotion");
        }
    }

    private void RefreshButtons(Btn hover)
    {
        int cellSel = _selV > 0 && _selA > 0 ? (_selA - 1) * 9 + (_selV - 1) : -1;
        foreach (var b in _btns)
        {
            bool sel = (b.kind == "valence" && b.idx + 1 == _selV) || (b.kind == "arousal" && b.idx + 1 == _selA)
                    || ((b.kind == "emotion" || b.kind == "qemotion") && b.idx == _selE)
                    || (b.kind == "intensity" && b.idx + 1 == _selI)
                    || (b.kind == "grid" && b.idx == cellSel);
            ModernUI.SetState(b.img, b == hover ? BtnHover : (sel ? BtnSel : BtnBase), b == hover);
        }
    }

    private void Submit()
    {
        if (_submitting) return;
        Text status = _mode == "quick" ? _quickStatus : _fullStatus;
        if (_selV == 0 || _selA == 0)
        {
            status.text = L("请选择愉快度和唤醒度", "快・不快と覚醒度を選んでください", "Please select valence and arousal");
            return;
        }
        if (_needLabel7 && _selE < 0)
        {
            status.text = L("请选择最接近的情绪", "最も近い感情を選んでください", "Please select an emotion");
            return;
        }
        if (_askIntensity && _selE >= 0 && _selI == 0)
        {
            status.text = L("请选择情绪强度", "感情の強さを選んでください", "Please select intensity");
            return;
        }
        _submitting = true;
        int ms = Mathf.RoundToInt((Time.realtimeSinceStartup - _shownAt) * 1000f);
        string label = _selE >= 0 ? StimText.EmotionKeys[_selE] : "";
        string json = "{\"stimulus_id\":\"" + StimNet.Esc(_ratingFor) + "\",\"valence\":" + _selV + ",\"arousal\":" + _selA
                    + ",\"label7\":\"" + label + "\",\"intensity\":" + (_askIntensity ? _selI : 0)
                    + ",\"rating_ms\":" + ms + ",\"mode\":\"" + _mode + "\"}";
        _host.StartCoroutine(StimNet.Post("/api/stimlib/rating", json, (code, text) =>
        {
            _submitting = false;
            if (code == 200)
            {
                status.text = L("已记录", "記録しました", "Recorded");
                Hide();
            }
            else
            {
                status.text = L("提交失败，请重试", "送信に失敗しました。もう一度", "Failed, please retry");
                Debug.LogWarning($"[StimPlayer] 自评提交失败 [{code}] {text}");
            }
        }));
    }
}
