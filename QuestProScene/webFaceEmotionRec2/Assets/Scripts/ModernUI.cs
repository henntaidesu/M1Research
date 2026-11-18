// ModernUI.cs
// 三块头显 UI（情绪选择 / 会话自评 / 刺激控制台）共用的按钮外观：
//   · 运行时生成的抗锯齿圆角精灵（九宫格），替代内置 UISprite.psd —— 那张图自带灰色斜面描边，是「老式按钮」观感的主要来源；
//   · ModernButton：颜色平滑过渡 + 悬停轻微放大 + 点击回弹，替代原来的瞬间换色。
// 全部用代码生成，不需要额外美术资源。

using UnityEngine;
using UnityEngine.UI;

public static class ModernUI
{
    // ── 设计令牌（与 VRStimulusControlPanel 同一套）──
    public static readonly Color Surface = new Color(0.110f, 0.125f, 0.161f, 0.92f);
    public static readonly Color Accent = new Color(0.231f, 0.510f, 0.965f, 1f);
    public static readonly Color Success = new Color(0.133f, 0.773f, 0.369f, 1f);
    public static readonly Color Muted = new Color(0.545f, 0.576f, 0.651f, 1f);

    // 精灵里的圆角半径（像素）；实际半径由 Image.pixelsPerUnitMultiplier 缩放
    private const int SpriteRadius = 64;
    private static Sprite _rounded;

    /// <summary>白色圆角矩形九宫格精灵（上色靠 Image.color）。</summary>
    public static Sprite RoundedSprite()
    {
        if (_rounded != null) return _rounded;

        int size = SpriteRadius * 2 + 4;
        var tex = new Texture2D(size, size, TextureFormat.RGBA32, true)
        {
            wrapMode = TextureWrapMode.Clamp,
            filterMode = FilterMode.Trilinear,
            name = "ModernUI_Rounded",
        };
        float half = size / 2f;
        float inner = half - SpriteRadius;      // 中心直边段的半宽
        var px = new Color32[size * size];
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
            {
                // 到内矩形的距离 - 半径 = 有符号距离；±0.5 像素做抗锯齿
                float dx = Mathf.Max(Mathf.Abs(x + 0.5f - half) - inner, 0f);
                float dy = Mathf.Max(Mathf.Abs(y + 0.5f - half) - inner, 0f);
                float d = Mathf.Sqrt(dx * dx + dy * dy) - SpriteRadius;
                byte a = (byte)(Mathf.Clamp01(0.5f - d) * 255f);
                px[y * size + x] = new Color32(255, 255, 255, a);
            }
        tex.SetPixels32(px);
        tex.Apply(true, true);

        float b = SpriteRadius + 1;
        _rounded = Sprite.Create(tex, new Rect(0, 0, size, size), new Vector2(0.5f, 0.5f),
                                 100f, 0, SpriteMeshType.FullRect, new Vector4(b, b, b, b));
        return _rounded;
    }

    /// <summary>套用圆角；radius 是画布单位，超过半高时 Unity 会自动收成药丸/圆形。</summary>
    public static void Round(Image img, float radius = 18f)
    {
        img.sprite = RoundedSprite();
        img.type = Image.Type.Sliced;
        img.pixelsPerUnitMultiplier = SpriteRadius / Mathf.Max(radius, 1f);
    }

    /// <summary>设定按钮的目标颜色与悬停状态，由 ModernButton 平滑过渡过去。</summary>
    public static void SetState(Image img, Color color, bool hovered = false)
    {
        if (img == null) return;
        Fx(img).SetState(color, hovered);
    }

    /// <summary>点击回弹。</summary>
    public static void Press(Image img)
    {
        if (img == null) return;
        Fx(img).Press();
    }

    /// <summary>主标签 + 下方一行小号灰色副标签（需 Text.supportRichText，默认开启）。</summary>
    public static string TwoLine(string primary, string secondary, int secondarySize)
    {
        string hex = ColorUtility.ToHtmlStringRGB(Muted);
        return $"{primary}\n<size={secondarySize}><color=#{hex}>{secondary}</color></size>";
    }

    private static ModernButton Fx(Image img)
    {
        var fx = img.GetComponent<ModernButton>();
        return fx != null ? fx : img.gameObject.AddComponent<ModernButton>();
    }
}

/// <summary>挂在按钮 Image 上：颜色 / 缩放朝目标值指数逼近。</summary>
[DisallowMultipleComponent]
public class ModernButton : MonoBehaviour
{
    private const float HoverScale = 1.04f;
    private const float PressScale = 0.94f;
    private const float Speed = 14f;

    private Image _img;
    private Color _target;
    private float _targetScale = 1f;
    private float _scale = 1f;
    private bool _init;

    public void SetState(Color color, bool hovered)
    {
        Init();
        _target = color;
        _targetScale = hovered ? HoverScale : 1f;
    }

    public void Press()
    {
        Init();
        _scale = PressScale;
    }

    private void Init()
    {
        if (_init) return;
        _init = true;
        _img = GetComponent<Image>();
        _target = _img != null ? _img.color : Color.white;
    }

    private void OnDisable()
    {
        // 面板隐藏期间不跑 Update；再次出现时直接落在目标态，不重播过渡
        if (!_init) return;
        _scale = _targetScale = 1f;
        transform.localScale = Vector3.one;
        if (_img != null) _img.color = _target;
    }

    private void Update()
    {
        if (!_init) return;
        float k = 1f - Mathf.Exp(-Speed * Time.unscaledDeltaTime);
        if (_img != null) _img.color = Color.Lerp(_img.color, _target, k);
        _scale = Mathf.Lerp(_scale, _targetScale, k);
        transform.localScale = new Vector3(_scale, _scale, 1f);
    }
}
