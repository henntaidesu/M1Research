// BackendConfig.cs
// 后端地址的**唯一真源**。以前 4 个脚本各写死一份 http://127.0.0.1:9501，
// 那个地址只在 adb 反向隧道下成立；改成外网部署后必须换成服务器的公网地址，
// 分散写死就会漏改，所以集中到这里。
//
// 为什么是 9500 而不是 9501：对外只开放 9500（网页端口），后端 9501 不暴露。
// Vite 已经把 /api、/ws、/health 反代到 9501，而头显要的路径全在 /api 下，
// 所以填 9500 就能到后端。
//
// 运行时可覆盖：进应用后若在 PlayerPrefs 里存过地址就优先用它（键 BackendBaseUrl），
// 换场地/换域名不必重新打包。没存过就用下面的默认值。

using UnityEngine;

public static class BackendConfig
{
    /// <summary>打包时的默认后端地址（服务器公网地址 + 网页端口）。</summary>
    public const string DefaultBaseUrl = "http://nas.makurochan.com:9500";

    private const string PrefKey = "BackendBaseUrl";

    /// <summary>当前后端地址，末尾不带斜杠。优先取 PlayerPrefs 覆盖值。</summary>
    public static string BaseUrl
    {
        get
        {
            string v = PlayerPrefs.GetString(PrefKey, "");
            if (string.IsNullOrWhiteSpace(v)) v = DefaultBaseUrl;
            return v.TrimEnd('/');
        }
    }

    /// <summary>改后端地址并立即持久化；传空串＝恢复默认。</summary>
    public static void SetBaseUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) PlayerPrefs.DeleteKey(PrefKey);
        else PlayerPrefs.SetString(PrefKey, url.Trim().TrimEnd('/'));
        PlayerPrefs.Save();
    }

    /// <summary>
    /// 解析脚本字段里填的后端地址：留空就用 BaseUrl。
    ///
    /// 还要挡一种**很难查的情况**：Unity 把 public 字段的值序列化进场景/预制体，改代码里的
    /// 默认值**不会**动已经存好的场景。于是「运行时 new 出来的组件」用新地址、「场景里挂着的
    /// 组件」still 用旧地址，表现为控制通道通、图片/FEA/视角全断这种半通不通的样子——查过一次。
    /// 在头显上跑时指向本机回环的地址一定是这种残留（那只在 adb 反向隧道下成立），直接忽略。
    /// 编辑器里不管：用 Quest Link 调试时 127.0.0.1 是合法的。
    /// </summary>
    public static string Resolve(string fieldValue)
    {
        string v = (fieldValue ?? "").Trim();
        if (v.Length == 0) return BaseUrl;
        if (!Application.isEditor && (v.Contains("127.0.0.1") || v.Contains("localhost")))
        {
            Debug.LogWarning($"[BackendConfig] 忽略场景里残留的回环地址 {v}，改用 {BaseUrl}");
            return BaseUrl;
        }
        return v.TrimEnd('/');
    }
}
