// MimicryClipPresenter.cs
// kind = mimicry：面部模仿片段。不是全景，而是一块平面「屏幕」：
//   - 中性灰天空盒（grayLevel），头部当前朝向正前方约 2 m 处一块 ~1.2 m 宽的面片（高度按视频宽高比，默认 16:9）
//   - 视频经 RenderTexture 贴在面片上；stim.audio = true 时带声音
//   - 片段只播一次（不循环），播完 Completed → StimulusPlayer 打 complete 事件；画面停在最后一帧
//   - events[]（如 "smile_peak"）按视频时间 Vp.time 到点打 evt:<key>
//
// params_json（可选）：{"width_m":1.2,"distance_m":2.0,"y_offset_m":0}

using System.Collections;
using System.IO;
using UnityEngine;

public class MimicryClipPresenter : IStimulusPresenter
{
    [System.Serializable]
    private class Params { public float width_m = 1.2f; public float distance_m = 2.0f; public float y_offset_m = 0f; }

    private StimContext _ctx;
    private VideoCore _video;
    private GameObject _quad;
    private Material _mat;
    private EventSchedule _schedule;
    private Params _p = new Params();
    private bool _begun, _wantPaused, _ended;

    public string Kind => StimSpec.KindMimicry;
    public bool IsReady => _video != null && _video.Ready && _quad != null;
    public float Position => _video != null ? _video.Position : 0f;
    public bool Completed => _video != null && _video.Completed;

    public IEnumerator Prepare(StimSpec spec, StimContext ctx)
    {
        _ctx = ctx;
        _schedule = new EventSchedule(spec);
        if (!string.IsNullOrEmpty(spec.params_json))
        {
            try { JsonUtility.FromJsonOverwrite(spec.params_json, _p); }
            catch (System.Exception e) { Debug.LogWarning($"[StimPlayer] mimicry params_json 无法解析：{e.Message}"); }
        }
        if (!spec.HasMedia) { ctx.Fail("mimicry stimulus has no url"); yield break; }
        yield return ctx.Cache.Ensure(spec);
        if (_ended) yield break;
        string path = ctx.CachePath;
        if (!File.Exists(path))
        {
            ctx.Fail(string.IsNullOrEmpty(ctx.Cache.LastError) ? "download failed" : ctx.Cache.LastError);
            yield break;
        }
        _video = new VideoCore(ctx, loop: false, audio: spec.audio || ctx.VideoAudioDefault);
        yield return _video.Prepare(path, wrapRepeat: false);
        if (_ended || !_video.Ready) yield break;

        // 面片：宽 width_m，高按视频宽高比；没碰撞体（不挡自评面板的射线）
        float w = Mathf.Clamp(_p.width_m, 0.3f, 3f);
        float aspect = _video.Vp.width > 0 ? (float)_video.Vp.height / _video.Vp.width : 9f / 16f;
        _quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
        _quad.name = "MimicryScreen_" + spec.id;
        var col = _quad.GetComponent<Collider>();
        if (col != null) Object.Destroy(col);
        _quad.transform.SetParent(ctx.Root, false);
        _quad.transform.localScale = new Vector3(w, w * aspect, 1f);
        _mat = StimUi.UnlitMaterial(Color.white, _video.Rt);
        _quad.GetComponent<MeshRenderer>().sharedMaterial = _mat;
        _quad.SetActive(false);
    }

    public void Begin()
    {
        if (!IsReady || _begun) return;
        _begun = true;
        _ctx.SetLastLuma(-1f);
        _ctx.SetGray(_ctx.DefaultGray);            // 中性灰背景
        // Quad 的正面朝 -Z；PlaceInFront 让 +Z 指向远离受试者，正面正好对着人
        _ctx.PlaceInFront(_quad, Mathf.Clamp(_p.distance_m, 0.8f, 5f), _p.y_offset_m);
        _quad.SetActive(true);
        _video.Start(_wantPaused);
    }

    public void Tick(float tSinceOnset)
    {
        if (!_begun) return;
        _video.Tick();
        if (tSinceOnset < 0f) return;              // onset 前不推进事件
        long ms = StimNet.NowMs();
        _schedule.Advance(_video.Position, e =>
            _ctx.Event(e.key, "t_s=" + e.t_s.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture), ms));
    }

    public void SetPaused(bool paused)
    {
        if (!_begun) { _wantPaused = paused; return; }
        _video.SetPaused(paused);
    }

    public void Seek(float seconds) { if (_begun) _video.Seek(seconds); }

    public void End()
    {
        _ended = true;
        _video?.End();
        if (_quad != null) { Object.Destroy(_quad); _quad = null; }
        if (_mat != null) { Object.Destroy(_mat); _mat = null; }
    }
}
