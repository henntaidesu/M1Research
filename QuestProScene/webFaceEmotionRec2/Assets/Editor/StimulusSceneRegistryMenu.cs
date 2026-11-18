// StimulusSceneRegistryMenu.cs
// 菜单 M1/Create Stimulus Scene Registry：创建（或选中已有的）Assets/Resources/StimulusSceneRegistry.asset。
// 手写 .asset 的 YAML 容易出错，所以用编辑器脚本生成。创建后在 Inspector 里往 entries 加 {key, prefab}。
// 注册表不存在时运行时按空表处理，内置场景（startle_door）照样能用。

using System.IO;
using UnityEditor;
using UnityEngine;

public static class StimulusSceneRegistryMenu
{
    private const string Dir = "Assets/Resources";
    private const string AssetPath = Dir + "/" + StimulusSceneRegistry.ResourcePath + ".asset";

    [MenuItem("M1/Create Stimulus Scene Registry")]
    public static void Create()
    {
        var existing = AssetDatabase.LoadAssetAtPath<StimulusSceneRegistry>(AssetPath);
        if (existing != null)
        {
            Selection.activeObject = existing;
            EditorGUIUtility.PingObject(existing);
            Debug.Log("[M1] 注册表已存在：" + AssetPath);
            return;
        }
        if (!Directory.Exists(Dir)) AssetDatabase.CreateFolder("Assets", "Resources");
        var reg = ScriptableObject.CreateInstance<StimulusSceneRegistry>();
        AssetDatabase.CreateAsset(reg, AssetPath);
        AssetDatabase.SaveAssets();
        Selection.activeObject = reg;
        EditorGUIUtility.PingObject(reg);
        Debug.Log("[M1] 已创建 " + AssetPath + "（内置场景 startle_door 不需要登记）");
    }
}
