// SessionUploader.cs
// FEA 时序闭环采集的「网络客户端」：把头显四路信号批量 POST 到 PC 后端。
// 对接 backend/src/use_web/app.py 的：
//   POST /api/session/start   {"subject_id","fea_rate_hz","sync_offset_ms"} → {"session_id"}
//   POST /api/session/ingest  {"session_id","fea":[[ts,f0..f69]],"gaze":[[ts,x,y,z]],
//                              "stimulus":[{...}],"selfreport":[{...}]}
//   POST /api/session/stop    {"session_id","sync_offset_ms"}
// 见 docs/任务书_VR微情感时序采集与情绪预测.md 的 M-A（A4）。
//
// 用 StringBuilder 手工拼 JSON（FEA 是数组套数组，JsonUtility 处理不了）；
// 数值一律 InvariantCulture，避免设备区域设置把小数点写成逗号。
// 采样与 flush 都在主线程协程里跑，缓冲用普通 List 即可，无需加锁。

using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

public class SessionUploader : MonoBehaviour
{
    [Header("后端")]
    [Tooltip("留空＝用 BackendConfig.BaseUrl（服务器公网地址）。只在编辑器里临时改才填。")]
    public string backendBase = "";
    /// <summary>实际用的后端地址（留空或场景里的回环残留 → 全局 BackendConfig）。</summary>
    private string Base => BackendConfig.Resolve(backendBase);
    [Tooltip("批量上传间隔（秒）")]
    public float flushInterval = 1.0f;

    public string SessionId { get; private set; }
    public bool Ready => !string.IsNullOrEmpty(SessionId);

    private readonly List<string> _fea = new List<string>();
    private readonly List<string> _gaze = new List<string>();
    private readonly List<string> _stimulus = new List<string>();
    private readonly List<string> _selfreport = new List<string>();
    private bool _flushing;

    private static string F(float v) => v.ToString("0.#####", CultureInfo.InvariantCulture);

    // ── 会话生命周期 ──────────────────────────────────────────────
    public IEnumerator StartSession(string subjectId, float feaRateHz, int syncOffsetMs = 0)
    {
        string body = "{\"subject_id\":\"" + Esc(subjectId) + "\",\"fea_rate_hz\":" +
                      F(feaRateHz) + ",\"sync_offset_ms\":" + syncOffsetMs + "}";
        yield return Post("/api/session/start", body, resp =>
        {
            var parsed = JsonUtility.FromJson<StartResp>(resp);
            SessionId = parsed != null ? parsed.session_id : null;
            Debug.Log($"[Uploader] 会话开始 session_id={SessionId}");
        });
        if (Ready) StartCoroutine(FlushLoop());
    }

    public IEnumerator StopSession(int syncOffsetMs = 0)
    {
        if (!Ready) yield break;
        yield return FlushOnce();
        string body = "{\"session_id\":\"" + Esc(SessionId) + "\",\"sync_offset_ms\":" + syncOffsetMs + "}";
        yield return Post("/api/session/stop", body, _ => Debug.Log("[Uploader] 会话结束"));
        SessionId = null;
    }

    // ── 采集入队（主线程调用）────────────────────────────────────
    public void AddFea(long tsMs, IList<float> bs)
    {
        var sb = new StringBuilder("[").Append(tsMs);
        for (int i = 0; i < bs.Count; i++) sb.Append(',').Append(F(bs[i]));
        sb.Append(']');
        _fea.Add(sb.ToString());
    }

    public void AddGaze(long tsMs, float x, float y, float z)
    {
        _gaze.Add("[" + tsMs + "," + F(x) + "," + F(y) + "," + F(z) + "]");
    }

    public void AddStimulus(long onsetMs, string imageId, string targetEmotion, long offsetMs, long dwellMs)
    {
        _stimulus.Add("{\"onset_ms\":" + onsetMs + ",\"image_id\":\"" + Esc(imageId) +
                      "\",\"target_emotion\":\"" + Esc(targetEmotion) + "\",\"offset_ms\":" + offsetMs +
                      ",\"dwell_ms\":" + dwellMs + "}");
    }

    public void AddSelfReport(long tsMs, string imageId, string label7, int valence, int arousal)
    {
        _selfreport.Add("{\"timestamp_ms\":" + tsMs + ",\"image_id\":\"" + Esc(imageId) +
                        "\",\"label7\":\"" + Esc(label7) + "\",\"valence\":" + valence +
                        ",\"arousal\":" + arousal + "}");
    }

    // ── 批量上传 ──────────────────────────────────────────────────
    private IEnumerator FlushLoop()
    {
        var wait = new WaitForSeconds(Mathf.Max(0.2f, flushInterval));
        while (Ready)
        {
            yield return wait;
            yield return FlushOnce();
        }
    }

    private IEnumerator FlushOnce()
    {
        if (_flushing || !Ready) yield break;
        if (_fea.Count == 0 && _gaze.Count == 0 && _stimulus.Count == 0 && _selfreport.Count == 0)
            yield break;
        _flushing = true;

        // 取走当前缓冲（失败则重新入队头部）
        var fea = Drain(_fea); var gaze = Drain(_gaze);
        var stim = Drain(_stimulus); var self = Drain(_selfreport);

        var sb = new StringBuilder("{\"session_id\":\"").Append(Esc(SessionId)).Append('"');
        Append(sb, "fea", fea);
        Append(sb, "gaze", gaze);
        Append(sb, "stimulus", stim);
        Append(sb, "selfreport", self);
        sb.Append('}');

        bool ok = false;
        yield return Post("/api/session/ingest", sb.ToString(), _ => ok = true);
        if (!ok)   // 失败：把这批塞回队首，下次重传
        {
            _fea.InsertRange(0, fea); _gaze.InsertRange(0, gaze);
            _stimulus.InsertRange(0, stim); _selfreport.InsertRange(0, self);
        }
        _flushing = false;
    }

    private static List<string> Drain(List<string> src)
    {
        var copy = new List<string>(src);
        src.Clear();
        return copy;
    }

    private static void Append(StringBuilder sb, string key, List<string> rows)
    {
        if (rows.Count == 0) return;
        sb.Append(",\"").Append(key).Append("\":[").Append(string.Join(",", rows)).Append(']');
    }

    private IEnumerator Post(string path, string json, System.Action<string> onOk)
    {
        using (var req = new UnityWebRequest(Base + path, "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
                onOk?.Invoke(req.downloadHandler.text);
            else
                Debug.LogWarning($"[Uploader] POST {path} 失败：{req.error}");
        }
    }

    private static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");

    [System.Serializable]
    private class StartResp { public bool ok; public string session_id; public string dir; }
}
