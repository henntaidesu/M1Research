// HeadsetViewStreamer.cs
// 把头显「用户视角」推给网页：镜像中央眼相机渲到 RenderTexture → **WebRTC 点对点**
// 直接送到浏览器。画面不经过后端，后端只在建连时转交一次 SDP。
//
// 为什么不再走后端中转：原先是每帧 JPEG POST 到 /api/headset/view、网页再轮询取回，
// 单眼原生分辨率下上下行各几十 Mbps，服务器要扛两遍，外网场景根本跑不动。
//
// 握手（后端 backend/src/use_web/rtc_signal.py 只是个信箱）：
//   本脚本 GET  /api/headset/rtc/watch    ← 有人在网页上看吗？没人看就不建连接、不编码
//   本脚本 GET  /api/headset/rtc/config   ← 取 ICE 服务器表（STUN/TURN，两端共用）
//   本脚本 POST /api/headset/rtc/offer    → 发 offer，拿到 session
//   本脚本 GET  /api/headset/rtc/answer   ← 等网页的 answer
//   之后画面直连，后端再无参与。
//
// vanilla ICE（非 trickle）：等 ICE 收集完再发 offer，候选内嵌在 SDP 里。建连慢
// 1～3 秒，换来信令面无竞态。一次会话只握手一次，划算。
//
// 外网提示：两端都在对称 NAT 后时 STUN 打不通，必须在后端 conf.ini [webrtc] 配 TURN。
//
// 依赖 com.unity.webrtc（已加进 Packages/manifest.json）。
//
// 用法：挂到场景任意物体（cameraRig 留空自动查找 OVRCameraRig）。
//   addressSource 指向场景里的 VREmotionSelectionUI 时，会跟随其运行时地址栏改的地址；
//   留空则用本脚本的 backendBaseUrl。

using System.Collections;
using System.Linq;
using Unity.WebRTC;
using UnityEngine;
using UnityEngine.Networking;

public class HeadsetViewStreamer : MonoBehaviour
{
    [Header("引用（留空自动查找）")]
    public OVRCameraRig cameraRig;
    [Tooltip("指向场景里的 VREmotionSelectionUI，跟随其运行时改的后端地址；留空则用下面的默认值")]
    public VREmotionSelectionUI addressSource;

    [Header("后端（只用于交换 SDP，画面不走这里）")]
    [Tooltip("留空＝用 BackendConfig.BaseUrl（服务器公网地址）")]
    public string backendBaseUrl = "";

    [Header("画面")]
    [Tooltip("编码宽度；高度按头显单眼宽高比自动算（XR 未就绪时才用下面的 height）")]
    public int width = 960;
    public int height = 720;
    [Tooltip("编码帧率上限")]
    public float streamFps = 6f;
    [Tooltip("码率上限（kbps）。外网带宽紧张就调小")]
    public int maxBitrateKbps = 2500;

    private Camera _mirrorCam;
    private RenderTexture _rt;
    private RTCPeerConnection _pc;
    private VideoStreamTrack _track;
    private bool _negotiating;
    private bool _broken;      // 连接断了，等 WatchLoop 收尸（不在回调里销毁自己）

    /// 协商途中连接已作废：每个 yield 之后都要查，否则会拿着已 Dispose 的 pc 继续走
    private bool Dead => _pc == null || _broken;

    private void Start()
    {
        if (cameraRig == null) cameraRig = FindObjectOfType<OVRCameraRig>();
        if (addressSource == null) addressSource = FindObjectOfType<VREmotionSelectionUI>();
        if (cameraRig == null || cameraRig.centerEyeAnchor == null)
        {
            Debug.LogError("[ViewStreamer] 缺 OVRCameraRig.centerEyeAnchor，视角回传未启动");
            enabled = false;
            return;
        }

        var eyeCam = cameraRig.centerEyeAnchor.GetComponent<Camera>();
        if (eyeCam == null)
        {
            Debug.LogError("[ViewStreamer] centerEyeAnchor 上没有 Camera，视角回传未启动");
            enabled = false;
            return;
        }

        // 按单眼宽高比算高度，画面不变形；编码器要求宽高为偶数
        if (UnityEngine.XR.XRSettings.eyeTextureWidth > 0 && UnityEngine.XR.XRSettings.eyeTextureHeight > 0)
        {
            height = Mathf.RoundToInt(
                width * (float)UnityEngine.XR.XRSettings.eyeTextureHeight / UnityEngine.XR.XRSettings.eyeTextureWidth);
        }
        width = Mathf.Max(2, width & ~1);
        height = Mathf.Max(2, height & ~1);

        // BGRA32：Unity WebRTC 从 RenderTexture 取帧要求的格式
        _rt = new RenderTexture(width, height, 24, RenderTextureFormat.BGRA32);
        _rt.Create();

        var go = new GameObject("HeadsetMirrorCam");
        go.transform.SetParent(cameraRig.centerEyeAnchor, false);
        _mirrorCam = go.AddComponent<Camera>();
        _mirrorCam.CopyFrom(eyeCam);                             // 与眼相机同 fov/清屏/剔除，画面一致
        _mirrorCam.stereoTargetEye = StereoTargetEyeMask.None;   // 单目渲染到 RT
        _mirrorCam.targetTexture = _rt;
        _mirrorCam.depth = eyeCam.depth - 1;                     // 在眼相机之前渲，进正常渲染阶段
        _mirrorCam.enabled = false;                              // 没人看时不渲，省 GPU

        StartCoroutine(WebRTC.Update());
        StartCoroutine(WatchLoop());
        Debug.Log($"[ViewStreamer] WebRTC 视角回传就绪 {width}x{height}@{streamFps}fps，信令 → {BaseUrl()}");
    }

    private string BaseUrl()
    {
        string b = (addressSource != null && !string.IsNullOrEmpty(addressSource.backendBaseUrl))
            ? addressSource.backendBaseUrl : backendBaseUrl;
        // 两处都留空（现在是默认状态）、或是场景里的回环残留，都用全局的服务器公网地址
        return BackendConfig.Resolve(b);
    }

    // ── 有人看才建连接 ────────────────────────────────────────────
    private IEnumerator WatchLoop()
    {
        var wait = new WaitForSeconds(1f);
        while (true)
        {
            yield return wait;
            if (_negotiating) continue;
            if (_broken) Teardown();     // 上一条连接断了，先收拾干净再谈下一条

            bool watching;
            using (var req = UnityWebRequest.Get(BaseUrl() + "/api/headset/rtc/watch"))
            {
                req.timeout = 5;
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success) continue;
                var w = JsonUtility.FromJson<WatchDto>(req.downloadHandler.text);
                watching = w != null && w.watching;
            }

            if (watching)
            {
                if (_pc == null) yield return StartCoroutine(Negotiate());
            }
            else if (_pc != null)
            {
                Teardown();     // 没人看了，拆连接停编码
            }
        }
    }

    private IEnumerator Negotiate()
    {
        _negotiating = true;

        RTCIceServer[] iceServers = null;
        using (var req = UnityWebRequest.Get(BaseUrl() + "/api/headset/rtc/config"))
        {
            req.timeout = 5;
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
            {
                var cfg = JsonUtility.FromJson<IceConfigDto>(req.downloadHandler.text);
                if (cfg != null && cfg.iceServers != null)
                {
                    iceServers = cfg.iceServers.Select(s => new RTCIceServer
                    {
                        urls = s.urls,
                        username = string.IsNullOrEmpty(s.username) ? null : s.username,
                        credential = string.IsNullOrEmpty(s.credential) ? null : s.credential,
                    }).ToArray();
                }
            }
        }

        var config = default(RTCConfiguration);
        config.iceServers = iceServers ?? new[]
        {
            new RTCIceServer { urls = new[] { "stun:stun.l.google.com:19302" } },
        };

        _broken = false;
        _pc = new RTCPeerConnection(ref config);
        _pc.OnConnectionStateChange = state =>
        {
            Debug.Log($"[ViewStreamer] 连接状态 {state}");
            // Disconnected 是暂态，多半自己会恢复；只有真断了才作废（销毁交给 WatchLoop）
            if (state == RTCPeerConnectionState.Failed || state == RTCPeerConnectionState.Closed)
                _broken = true;
        };

        _mirrorCam.enabled = true;
        _track = new VideoStreamTrack(_rt);
        _pc.AddTrack(_track);

        var offerOp = _pc.CreateOffer();
        yield return offerOp;
        if (offerOp.IsError)
        {
            Debug.LogWarning($"[ViewStreamer] CreateOffer 失败：{offerOp.Error.message}");
            Teardown();
            _negotiating = false;
            yield break;
        }

        var desc = offerOp.Desc;
        var setLocal = _pc.SetLocalDescription(ref desc);
        yield return setLocal;
        if (setLocal.IsError)
        {
            Debug.LogWarning($"[ViewStreamer] SetLocalDescription 失败：{setLocal.Error.message}");
            Teardown();
            _negotiating = false;
            yield break;
        }

        // vanilla ICE：等候选收集完（TURN 不可达时会卡住，所以最多等 3 秒）
        float deadline = Time.realtimeSinceStartup + 3f;
        while (!Dead && _pc.GatheringState != RTCIceGatheringState.Complete
               && Time.realtimeSinceStartup < deadline)
            yield return null;
        if (Dead) { _negotiating = false; yield break; }

        int session = -1;
        using (var req = Post(BaseUrl() + "/api/headset/rtc/offer",
                              JsonUtility.ToJson(new SdpDto { sdp = _pc.LocalDescription.sdp })))
        {
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"[ViewStreamer] 发送 offer 失败：{req.error}");
                Teardown();
                _negotiating = false;
                yield break;
            }
            var r = JsonUtility.FromJson<OfferAckDto>(req.downloadHandler.text);
            session = r != null ? r.session : -1;
        }

        // 等网页回 answer（网页 1s 一轮询，给 15 秒余量）
        string answerSdp = null;
        for (int i = 0; i < 30 && answerSdp == null && !Dead; i++)
        {
            yield return new WaitForSeconds(0.5f);
            using (var req = UnityWebRequest.Get(BaseUrl() + $"/api/headset/rtc/answer?session={session}"))
            {
                req.timeout = 5;
                yield return req.SendWebRequest();
                if (req.result == UnityWebRequest.Result.Success && req.responseCode == 200)
                {
                    var r = JsonUtility.FromJson<SdpDto>(req.downloadHandler.text);
                    if (r != null && !string.IsNullOrEmpty(r.sdp)) answerSdp = r.sdp;
                }
            }
        }
        if (Dead || answerSdp == null)
        {
            if (answerSdp == null) Debug.LogWarning("[ViewStreamer] 等 answer 超时，放弃本次协商");
            Teardown();
            _negotiating = false;
            yield break;
        }

        var answer = new RTCSessionDescription { type = RTCSdpType.Answer, sdp = answerSdp };
        var setRemote = _pc.SetRemoteDescription(ref answer);
        yield return setRemote;
        if (Dead || setRemote.IsError)
        {
            if (setRemote.IsError)
                Debug.LogWarning($"[ViewStreamer] SetRemoteDescription 失败：{setRemote.Error.message}");
            Teardown();
            _negotiating = false;
            yield break;
        }

        ApplySenderLimits();
        _negotiating = false;
    }

    /// 码率/帧率上限：外网带宽有限，不加限制编码器会一路顶到底
    private void ApplySenderLimits()
    {
        if (_pc == null) return;
        var sender = _pc.GetSenders().FirstOrDefault();
        if (sender == null) return;
        var parameters = sender.GetParameters();
        if (parameters.encodings == null) return;
        foreach (var e in parameters.encodings)
        {
            e.maxFramerate = (uint)Mathf.Max(1f, streamFps);
            e.maxBitrate = (ulong)Mathf.Max(1, maxBitrateKbps) * 1000UL;
        }
        // SetParameters 返回的是 RTCError 结构体（errorType + message），
        // 不能直接和 RTCErrorType 枚举比较，要取它的 errorType 字段。
        var err = sender.SetParameters(parameters);
        if (err.errorType != RTCErrorType.None)
            Debug.LogWarning($"[ViewStreamer] 设码率上限失败：{err.errorType} {err.message}");
    }

    private static UnityWebRequest Post(string url, string json)
    {
        var req = new UnityWebRequest(url, "POST")
        {
            uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(json)),
            downloadHandler = new DownloadHandlerBuffer(),
            timeout = 5,
        };
        req.SetRequestHeader("Content-Type", "application/json");
        return req;
    }

    private void Teardown()
    {
        _broken = false;
        if (_mirrorCam != null) _mirrorCam.enabled = false;
        if (_track != null) { _track.Dispose(); _track = null; }
        if (_pc != null)
        {
            _pc.OnConnectionStateChange = null;
            _pc.Close();
            _pc.Dispose();
            _pc = null;
        }
    }

    private void OnDestroy()
    {
        Teardown();
        if (_mirrorCam != null) _mirrorCam.targetTexture = null;
        if (_rt != null) { _rt.Release(); _rt = null; }
    }

    // ── 信令 DTO（JsonUtility 用）────────────────────────────────
    [System.Serializable] private class WatchDto { public bool watching; public int session; }
    [System.Serializable] private class SdpDto { public string sdp; public int session; }
    [System.Serializable] private class OfferAckDto { public bool ok; public int session; }
    [System.Serializable] private class IceServerDto { public string[] urls; public string username; public string credential; }
    [System.Serializable] private class IceConfigDto { public IceServerDto[] iceServers; }
}
