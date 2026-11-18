// AssetCache.cs
// 刺激素材的本地缓存（从 StimulusPlayer 原样搬出）。
//
// 刺激先下载到 persistentDataPath/stimcache/<id>_<sha>.<ext> 再播放（外网下视频几十 MB，
// 不能边下边播）。后端给出 prefetch 列表，空闲时按顺序预下载；sha 变了自动重新下载。
// 同一时刻只有一个下载；当前要播的刺激（Ensure）优先，预缓存让路。
// scene 类刺激打包在 APK 里，没有 url，不走这里。

using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;

public class AssetCache
{
    private readonly string _dir;
    private readonly HashSet<string> _cached = new HashSet<string>();
    private string _downloadingId = "";

    /// <summary>当前要播的刺激正在等下载：预缓存暂停。</summary>
    public bool Loading { get; private set; }

    /// <summary>最近一次下载失败的原因（也通过 OnError 交给播放器写进 status.error）。</summary>
    public string LastError { get; private set; } = "";
    public System.Action<string> OnError;

    public IEnumerable<string> CachedIds => _cached;

    public AssetCache(string dir)
    {
        _dir = dir;
        Directory.CreateDirectory(_dir);
    }

    public string PathFor(StimSpec s)
    {
        string ext = s.media == "video" ? ".mp4" : ".jpg";
        string safe = s.id.Replace(":", "_").Replace("/", "_");
        string sha = string.IsNullOrEmpty(s.sha) ? "nosha" : s.sha;
        return Path.Combine(_dir, safe + "_" + sha + ext);
    }

    public bool IsCached(StimSpec s, string path)
    {
        bool ok = File.Exists(path) && new FileInfo(path).Length > 0;
        if (ok) _cached.Add(s.id);
        return ok;
    }

    public bool IsCached(StimSpec s) => IsCached(s, PathFor(s));

    /// <summary>
    /// 确保当前要播的刺激已在本地：等别的下载结束（同一个就直接等它），没有就下载。
    /// 结束后看 File.Exists(PathFor(s)) 判断成败。
    /// </summary>
    public IEnumerator Ensure(StimSpec s)
    {
        string path = PathFor(s);
        Loading = true;                                  // 预缓存让路给当前要播的刺激
        if (!IsCached(s, path))
        {
            while (!string.IsNullOrEmpty(_downloadingId) && _downloadingId != s.id) yield return null;
            if (!IsCached(s, path))
            {
                if (_downloadingId == s.id) { while (_downloadingId == s.id) yield return null; }
                else yield return Download(s, path);
            }
        }
        Loading = false;
    }

    private IEnumerator Download(StimSpec s, string path)
    {
        _downloadingId = s.id;
        string tmp = path + ".part";
        using (var req = new UnityWebRequest(BackendConfig.BaseUrl + s.url, "GET"))
        {
            req.downloadHandler = new DownloadHandlerFile(tmp) { removeFileOnAbort = true };
            req.timeout = 900;
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
            {
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);
                _cached.Add(s.id);
                Debug.Log($"[StimPlayer] 已缓存 {s.id}");
            }
            else
            {
                LastError = $"download {s.id}: {req.error}";
                OnError?.Invoke(LastError);
                Debug.LogWarning("[StimPlayer] " + LastError);
            }
        }
        _downloadingId = "";
    }

    /// <summary>空闲时按后端 prefetch 顺序预下载，一次一个。没有 url 的（scene）跳过。</summary>
    public IEnumerator PrefetchLoop(System.Func<StimSpec[]> list)
    {
        var wait = new WaitForSeconds(1f);
        while (true)
        {
            var l = list();
            if (l != null && string.IsNullOrEmpty(_downloadingId) && !Loading)
            {
                foreach (var s in l)
                {
                    if (!StimSpec.Has(s) || !s.HasMedia) continue;
                    string p = PathFor(s);
                    if (IsCached(s, p)) continue;
                    yield return Download(s, p);
                    break;
                }
            }
            yield return wait;
        }
    }
}
