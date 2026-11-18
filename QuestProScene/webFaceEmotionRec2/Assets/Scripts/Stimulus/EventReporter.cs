// EventReporter.cs
// 头显打点事件（场景里的惊吓、模仿片段播完、视频卡顿……）的批量上传。
//
// 事件时刻用头显时钟 headset_ms 记（呈现那一帧的时刻），每 0.5 秒批量
// POST /api/stimlib/events，带上当前时钟偏移 offset_ms，后端写 events.csv 时换算成
// ts_ms = headset_ms + offset_ms、event = "evt:" + key。
// 失败就放回队首下次重发（同 FeaUploadLoop）；对时还没完成时先攒着不发，免得偏移是 0。

using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

public class EventReporter
{
    private struct Ev { public long ms; public string key, stim, detail; }

    private readonly List<Ev> _q = new List<Ev>();
    private const int BacklogMax = 2000;

    public void Add(string stimId, string key, string detail, long headsetMs)
    {
        _q.Add(new Ev { ms = headsetMs > 0 ? headsetMs : StimNet.NowMs(), key = key, stim = stimId ?? "", detail = detail ?? "" });
        if (_q.Count > BacklogMax) _q.RemoveRange(0, _q.Count - BacklogMax);
        Debug.Log($"[StimPlayer] 事件 evt:{key} {stimId} {detail}");
    }

    /// <param name="offset">当前时钟偏移；未知时返回 null</param>
    public IEnumerator Loop(System.Func<long?> offset)
    {
        var wait = new WaitForSeconds(0.5f);
        while (true)
        {
            yield return wait;
            if (_q.Count == 0) continue;
            long? off = offset();
            if (off == null) continue;
            var batch = new List<Ev>(_q);
            _q.Clear();
            var sb = new StringBuilder(64 + batch.Count * 96);
            sb.Append("{\"offset_ms\":").Append(off.Value).Append(",\"events\":[");
            for (int i = 0; i < batch.Count; i++)
            {
                var e = batch[i];
                if (i > 0) sb.Append(',');
                sb.Append("{\"headset_ms\":").Append(e.ms)
                  .Append(",\"key\":\"").Append(StimNet.Esc(e.key))
                  .Append("\",\"stimulus_id\":\"").Append(StimNet.Esc(e.stim))
                  .Append("\",\"detail\":\"").Append(StimNet.Esc(e.detail)).Append("\"}");
            }
            sb.Append("]}");
            bool ok = false;
            yield return StimNet.Post("/api/stimlib/events", sb.ToString(), (code, _) => ok = code == 200);
            if (!ok)                                     // 失败放回队首，下次连同新事件一起重发
            {
                _q.InsertRange(0, batch);
                if (_q.Count > BacklogMax) _q.RemoveRange(0, _q.Count - BacklogMax);
            }
        }
    }
}
