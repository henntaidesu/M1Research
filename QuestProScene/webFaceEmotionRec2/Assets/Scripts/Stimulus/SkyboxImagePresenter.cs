// SkyboxImagePresenter.cs
// kind = skybox_image：360° 全景图贴到天空盒（v1 的图片线，行为不变）。
//
// Prepare：确保已缓存 → 解码成带 mip 的纹理（经度方向 Repeat，配合 shader 消除接缝）
// Begin  ：记下平均亮度（灰屏亮度匹配用）→ 把全景正中转到头部朝向 → 贴天空盒
// onset  ：Begin 之后第一次 Tick（LateUpdate）＝ 第一帧带着新天空盒渲染出去的那一帧

using System.Collections;
using System.IO;
using UnityEngine;

public class SkyboxImagePresenter : IStimulusPresenter
{
    private StimContext _ctx;
    private Texture2D _tex;
    private bool _begun, _onsetDone, _ended;

    public string Kind => StimSpec.KindImage;
    public bool IsReady { get; private set; }
    public float Position => 0f;
    public bool Completed => false;

    public IEnumerator Prepare(StimSpec spec, StimContext ctx)
    {
        _ctx = ctx;
        yield return ctx.Cache.Ensure(spec);
        if (_ended) yield break;
        string path = ctx.CachePath;
        if (!File.Exists(path))
        {
            ctx.Fail(string.IsNullOrEmpty(ctx.Cache.LastError) ? "download failed" : ctx.Cache.LastError);
            yield break;
        }
        var tex = new Texture2D(2, 2, TextureFormat.RGB24, true);
        if (!tex.LoadImage(File.ReadAllBytes(path)))
        {
            Object.Destroy(tex);
            ctx.Fail("image decode failed");
            yield break;
        }
        tex.wrapModeU = TextureWrapMode.Repeat;
        tex.wrapModeV = TextureWrapMode.Clamp;
        tex.filterMode = FilterMode.Trilinear;
        _tex = tex;
        IsReady = true;
    }

    public void Begin()
    {
        if (!IsReady || _begun) return;
        _begun = true;
        _ctx.SetLastLuma(MeanLuma(_tex));
        _ctx.FaceSubject();
        _ctx.SetSky(_tex);
    }

    public void Tick(float tSinceOnset)
    {
        if (!_begun || _onsetDone) return;
        _onsetDone = true;
        _ctx.Ack("shown", StimNet.NowMs());
    }

    public void SetPaused(bool paused) { }
    public void Seek(float seconds) { }

    public void End()
    {
        _ended = true;
        IsReady = false;
        // Destroy 在帧末才真正释放；StimulusPlayer 在同一帧里已把天空盒换回灰屏，不会闪出粉色
        if (_tex != null) { Object.Destroy(_tex); _tex = null; }
    }

    /// <summary>图片的平均亮度（最小一级 mip 就是全图平均）。</summary>
    public static float MeanLuma(Texture2D tex)
    {
        var c = tex.GetPixels(tex.mipmapCount - 1);
        if (c.Length == 0) return 0.5f;
        float sum = 0f;
        foreach (var p in c) sum += 0.299f * p.r + 0.587f * p.g + 0.114f * p.b;
        return sum / c.Length;
    }
}
