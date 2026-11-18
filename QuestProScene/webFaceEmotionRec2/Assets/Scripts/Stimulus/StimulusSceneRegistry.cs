// StimulusSceneRegistry.cs
// prefab_key → 场景 prefab 的注册表（ScriptableObject），放在 Assets/Resources/StimulusSceneRegistry.asset，
// 随 APK 打包（先不用 Addressables）。用菜单 M1/Create Stimulus Scene Registry 创建，
// 在 Inspector 里往 entries 加 {key, prefab}。
//
// 资产不存在时按空表处理（不报错）。注册表里没有的键再查内置场景（纯代码搭的，不需要任何资产）：
//   startle_door → StartleDoorStimulus
// 都没有就返回错误，由 ScenePrefabPresenter 交给 ctx.Fail 回报给后端。

using System.Collections.Generic;
using UnityEngine;

public class StimulusSceneRegistry : ScriptableObject
{
    public const string ResourcePath = "StimulusSceneRegistry";

    [System.Serializable]
    public class Entry
    {
        public string key;
        public GameObject prefab;
    }

    public List<Entry> entries = new List<Entry>();

    private static StimulusSceneRegistry _instance;

    public static StimulusSceneRegistry Instance
    {
        get
        {
            if (_instance != null) return _instance;
            _instance = Resources.Load<StimulusSceneRegistry>(ResourcePath);
            if (_instance == null)
            {
                Debug.Log("[StimPlayer] 没有 Resources/StimulusSceneRegistry，只用内置场景");
                _instance = CreateInstance<StimulusSceneRegistry>();
            }
            return _instance;
        }
    }

    public GameObject FindPrefab(string key)
    {
        if (string.IsNullOrEmpty(key) || entries == null) return null;
        foreach (var e in entries)
            if (e != null && e.key == key && e.prefab != null) return e.prefab;
        return null;
    }

    // ── 内置场景：parent 是未激活的容器，工厂在它下面搭好物体并返回 SceneStimulus ──
    private static readonly Dictionary<string, System.Func<Transform, SceneStimulus>> Builtins =
        new Dictionary<string, System.Func<Transform, SceneStimulus>>
        {
            ["startle_door"] = parent =>
            {
                var go = new GameObject("StartleDoor");
                go.transform.SetParent(parent, false);
                var s = go.AddComponent<StartleDoorStimulus>();
                s.key = "startle_door";
                return s;
            },
        };

    public static bool HasBuiltin(string key) => !string.IsNullOrEmpty(key) && Builtins.ContainsKey(key);

    /// <summary>
    /// 在 parent（应当处于未激活状态）下实例化 key 对应的场景。注册表优先，其次内置。
    /// 返回 false 时 error 给出原因。
    /// </summary>
    public static bool TryInstantiate(string key, Transform parent, out GameObject root, out string error)
    {
        root = null;
        error = "";
        if (string.IsNullOrEmpty(key)) { error = "scene stimulus has empty prefab_key"; return false; }
        var prefab = Instance.FindPrefab(key);
        if (prefab != null)
        {
            root = Object.Instantiate(prefab, parent, false);
            root.name = prefab.name;
            return true;
        }
        if (Builtins.TryGetValue(key, out var make))
        {
            var s = make(parent);
            root = s != null ? s.gameObject : null;
            if (root != null) return true;
            error = $"built-in scene '{key}' failed to build";
            return false;
        }
        error = $"unknown prefab_key '{key}' (not in Resources/StimulusSceneRegistry, no built-in)";
        return false;
    }
}
