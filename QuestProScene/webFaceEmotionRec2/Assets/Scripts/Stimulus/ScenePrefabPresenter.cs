// ScenePrefabPresenter.cs
// kind = scene：打包在 APK 里的 3D 场景（按 prefab_key 在 StimulusSceneRegistry / 内置场景里找）。
// 后端对 scene 发 media = ""、没有 url，不走下载缓存。
//
// Prepare：在一个未激活的容器下实例化（Awake / OnEnable 推迟到激活时），注入 Report、Configure(params_json)。
//          StimulusPlayer 在注视阶段看到 upcoming 是 scene 就提前 Prepare，刺激阶段开始直接 Begin。
// Begin  ：容器放到受试者脚下（头部水平位置 + 追踪空间地面高度），转到头部当前朝向，然后激活。
//          天空盒设为中性灰（场景自己有墙就看不到）。
// onset  ：激活后第一次 Tick（LateUpdate）＝ 场景第一次被渲染出去的那一帧。
// 事件   ：events[] 按 onset 后的时间（扣掉暂停时长）到点 → SceneStimulus.Fire(key)，
//          并用这一帧的头显时间打 evt:<key>。网页暂停时时间表冻结。

using System.Collections;
using UnityEngine;

public class ScenePrefabPresenter : IStimulusPresenter
{
    private StimContext _ctx;
    private GameObject _holder;
    private SceneStimulus _scene;
    private EventSchedule _schedule;
    private bool _begun, _onsetDone, _ended, _paused;
    private float _pausedTotal, _pausedAt = -1f, _t;

    public string Kind => StimSpec.KindScene;
    public bool IsReady { get; private set; }
    public float Position => _t;
    public bool Completed => _scene != null && _scene.Completed;

    public IEnumerator Prepare(StimSpec spec, StimContext ctx)
    {
        _ctx = ctx;
        _schedule = new EventSchedule(spec);
        _holder = new GameObject("StimScene_" + spec.id);
        _holder.SetActive(false);
        _holder.transform.SetParent(ctx.Root, false);
        if (!StimulusSceneRegistry.TryInstantiate(spec.prefab_key, _holder.transform, out var root, out string err))
        {
            Debug.LogWarning("[StimPlayer] " + err);
            ctx.Fail(err);
            Object.Destroy(_holder);
            _holder = null;
            yield break;
        }
        _scene = _holder.GetComponentInChildren<SceneStimulus>(true);
        if (_scene != null)
        {
            _scene.Report = (k, d) => _ctx.Event(k, d);
            _scene.Configure(spec.params_json ?? "");
        }
        else if (_schedule.Count > 0)
            Debug.LogWarning($"[StimPlayer] 场景 {spec.prefab_key} 没有 SceneStimulus，事件只打点不触发");
        yield return null;                          // 实例化的开销摊到下一帧之前
        if (_ended) yield break;
        IsReady = true;
    }

    public void Begin()
    {
        if (!IsReady || _begun) return;
        _begun = true;
        _ctx.SetLastLuma(-1f);
        _ctx.SetGray(_ctx.DefaultGray);
        var rig = _ctx.Rig;
        Vector3 pos = Vector3.zero;
        if (rig != null && rig.centerEyeAnchor != null)
        {
            Vector3 eye = rig.centerEyeAnchor.position;
            float floorY = rig.trackingSpace != null ? rig.trackingSpace.position.y : rig.transform.position.y;
            pos = new Vector3(eye.x, floorY, eye.z);
        }
        _holder.transform.SetPositionAndRotation(pos, Quaternion.Euler(0f, _ctx.HeadYaw, 0f));
        _holder.SetActive(true);
        if (_paused) _pausedAt = Time.realtimeSinceStartup;   // 开播前就处于暂停：从现在起算暂停时长
    }

    public void Tick(float tSinceOnset)
    {
        if (!_begun || _ended) return;
        long ms = StimNet.NowMs();
        if (!_onsetDone)
        {
            _onsetDone = true;
            _ctx.Ack("shown", ms);
            return;                                 // 下一帧起 tSinceOnset 才有效
        }
        if (tSinceOnset < 0f) return;
        float pausedNow = _paused && _pausedAt >= 0f ? Time.realtimeSinceStartup - _pausedAt : 0f;
        _t = Mathf.Max(0f, tSinceOnset - _pausedTotal - pausedNow);
        if (_paused) return;
        _schedule.Advance(_t, e =>
        {
            if (_scene != null) _scene.Fire(e.key);
            _ctx.Event(e.key, "t_s=" + e.t_s.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture), ms);
        });
    }

    public void SetPaused(bool paused)
    {
        if (paused == _paused) return;
        _paused = paused;
        if (!_begun) return;
        if (paused) { _pausedAt = Time.realtimeSinceStartup; _ctx.Ack("paused", 0); }
        else
        {
            if (_pausedAt >= 0f) _pausedTotal += Time.realtimeSinceStartup - _pausedAt;
            _pausedAt = -1f;
            _ctx.Ack("shown", 0);
        }
    }

    public void Seek(float seconds) { }             // 场景不支持拖动

    public void End()
    {
        _ended = true;
        IsReady = false;
        if (_holder != null) { Object.Destroy(_holder); _holder = null; }
        _scene = null;
    }
}
