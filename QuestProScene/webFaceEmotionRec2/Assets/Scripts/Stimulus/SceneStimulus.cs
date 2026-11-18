// SceneStimulus.cs
// kind = scene 的刺激场景脚本基类。挂在场景 prefab 的根（或任一子物体）上。
//
// 约定：
//   - prefab 原点 = 受试者脚下，+Z = 受试者正前方；ScenePrefabPresenter 按呈现时的头部朝向摆放
//   - 注视阶段就被实例化（父物体未激活，Awake / OnEnable 还不会跑），刺激阶段开始才激活
//   - 后端 events[] 到点时 ScenePrefabPresenter 调 Fire(key)，并以那一帧的头显时间打 evt:<key>，
//     子类不用自己回报这些计划内事件
//   - 计划外的事件（比如「门已完全关上」）用 Emit(key, detail) 回报，同样写成 evt:<key>
//   - Completed = 场景自己的内容播完了（StimulusPlayer 打一次 complete）；时长仍由后端决定
//   - 安全：不要做 > 3 Hz 的闪烁；声音峰值自己限幅

using UnityEngine;

public class SceneStimulus : MonoBehaviour
{
    [Tooltip("对应 stimulus.prefab_key（只用于日志 / 检查）")]
    public string key;

    /// <summary>计划外事件回报：(key, detail)。由 ScenePrefabPresenter 注入。</summary>
    public System.Action<string, string> Report;

    /// <summary>Prepare 时调用一次，传入 stimulus.params_json（可能为空串）。在 Awake 之前。</summary>
    public virtual void Configure(string paramsJson) { }

    /// <summary>后端时间表里的事件到点。</summary>
    public virtual void Fire(string eventKey)
    {
        Debug.Log($"[StimPlayer] 场景 {key} 没有处理事件 {eventKey}");
    }

    public virtual bool Completed => false;

    protected void Emit(string eventKey, string detail = "") => Report?.Invoke(eventKey, detail ?? "");
}
