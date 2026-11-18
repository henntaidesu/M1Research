// StartleDoorStimulus.cs
// 内置示例场景（prefab_key = "startle_door"），完全用代码搭，不需要任何美术资产：
//   一间灰色的空房间（地面 / 天花板 / 三面墙 + 前墙中间一个门洞，门后是暗走廊），
//   门开着（向内转开约 80°）。收到 Fire("bang") 时门在 0.12 秒内猛地关上，同时播放一段
//   程序生成的噪声爆裂声（白噪声 + 60 Hz 低频，指数衰减，0.45 秒）。
//
// 安全：没有任何灯光 / 颜色闪烁（只有一次门的运动）；声音峰值归一到 0.9 后再乘音量，
//       音量上限 0.8（params 里填多大也会被夹住），2D 播放，响度不随头部位置变。
// Completed：门关上 2 秒后为 true。
//
// params_json（可选）：{"volume":0.7,"door_distance_m":4.0}
// 坐标约定同 SceneStimulus：原点 = 受试者脚下，+Z = 正前方。

using System.Collections;
using UnityEngine;

public class StartleDoorStimulus : SceneStimulus
{
    [System.Serializable]
    private class Params { public float volume = 0.7f; public float door_distance_m = 4.0f; }

    private const float MaxVolume = 0.8f;
    private const float DoorW = 1.0f, DoorH = 2.1f, RoomH = 3.0f, RoomHalfW = 3.0f;
    private const float OpenAngle = -80f;

    private Params _p = new Params();
    private bool _built;
    private Transform _hinge;
    private AudioSource _audio;
    private AudioClip _clip;
    private float _bangAt = -1f;
    private bool _slammed;
    private readonly System.Collections.Generic.List<Material> _mats = new System.Collections.Generic.List<Material>();

    private Material Mat(Color c) { var m = StimUi.UnlitMaterial(c); _mats.Add(m); return m; }

    public override void Configure(string paramsJson)
    {
        if (!string.IsNullOrEmpty(paramsJson))
        {
            try { JsonUtility.FromJsonOverwrite(paramsJson, _p); }
            catch (System.Exception e) { Debug.LogWarning($"[StimPlayer] startle_door params_json 无法解析：{e.Message}"); }
        }
        Build();                                    // Prepare 阶段（父物体未激活）就搭好，激活时不卡帧
    }

    private void Awake() { Build(); }

    private void Build()
    {
        if (_built) return;
        _built = true;
        float dz = Mathf.Clamp(_p.door_distance_m, 2f, 8f);
        float back = -2f;
        float depth = dz - back, cz = (dz + back) / 2f;

        // 房间：各面灰度略有差别，不打光也能看出空间
        Box("Floor", new Vector3(0, -0.05f, cz), new Vector3(RoomHalfW * 2, 0.1f, depth), 0.30f);
        Box("Ceiling", new Vector3(0, RoomH + 0.05f, cz), new Vector3(RoomHalfW * 2, 0.1f, depth), 0.55f);
        Box("WallL", new Vector3(-RoomHalfW - 0.05f, RoomH / 2, cz), new Vector3(0.1f, RoomH, depth), 0.42f);
        Box("WallR", new Vector3(RoomHalfW + 0.05f, RoomH / 2, cz), new Vector3(0.1f, RoomH, depth), 0.40f);
        Box("WallBack", new Vector3(0, RoomH / 2, back - 0.05f), new Vector3(RoomHalfW * 2, RoomH, 0.1f), 0.45f);

        // 前墙：门洞两侧 + 门楣
        float sideW = RoomHalfW - DoorW / 2f;
        Box("WallFrontL", new Vector3(-(DoorW / 2f + sideW / 2f), RoomH / 2, dz + 0.05f), new Vector3(sideW, RoomH, 0.1f), 0.48f);
        Box("WallFrontR", new Vector3(DoorW / 2f + sideW / 2f, RoomH / 2, dz + 0.05f), new Vector3(sideW, RoomH, 0.1f), 0.48f);
        Box("Lintel", new Vector3(0, (DoorH + RoomH) / 2f, dz + 0.05f), new Vector3(DoorW, RoomH - DoorH, 0.1f), 0.48f);

        // 门后暗走廊（门开着时看到的是一片暗）
        Box("Corridor", new Vector3(0, DoorH / 2f, dz + 1.5f), new Vector3(DoorW + 0.4f, DoorH + 0.2f, 0.1f), 0.06f);
        Box("CorridorL", new Vector3(-DoorW / 2f - 0.2f, DoorH / 2f, dz + 0.8f), new Vector3(0.05f, DoorH + 0.2f, 1.4f), 0.10f);
        Box("CorridorR", new Vector3(DoorW / 2f + 0.2f, DoorH / 2f, dz + 0.8f), new Vector3(0.05f, DoorH + 0.2f, 1.4f), 0.10f);

        // 门：铰链在门洞左边，门板绕它转；开着时向走廊一侧转开
        _hinge = new GameObject("DoorHinge").transform;
        _hinge.SetParent(transform, false);
        _hinge.localPosition = new Vector3(-DoorW / 2f, 0f, dz + 0.02f);
        var door = Box("Door", Vector3.zero, new Vector3(DoorW - 0.02f, DoorH - 0.02f, 0.05f), 0f);
        door.GetComponent<MeshRenderer>().sharedMaterial = Mat(new Color(0.32f, 0.24f, 0.18f));
        door.transform.SetParent(_hinge, false);
        door.transform.localPosition = new Vector3(DoorW / 2f, DoorH / 2f, 0f);
        _hinge.localRotation = Quaternion.Euler(0f, OpenAngle, 0f);

        // 声音
        _audio = gameObject.AddComponent<AudioSource>();
        _audio.playOnAwake = false;
        _audio.spatialBlend = 0f;
        _audio.volume = Mathf.Clamp(_p.volume, 0f, MaxVolume);
        _clip = MakeBang();
    }

    private GameObject Box(string name, Vector3 pos, Vector3 size, float gray)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
        go.name = name;
        var col = go.GetComponent<Collider>();
        if (col != null) Destroy(col);              // 不挡自评面板射线
        go.transform.SetParent(transform, false);
        go.transform.localPosition = pos;
        go.transform.localScale = size;
        go.GetComponent<MeshRenderer>().sharedMaterial = Mat(new Color(gray, gray, gray));
        return go;
    }

    // 0.45 秒：白噪声（衰减快）+ 60 Hz 低频（衰减慢），峰值归一到 0.9
    private static AudioClip MakeBang()
    {
        const int sr = 44100;
        int n = Mathf.RoundToInt(sr * 0.45f);
        var data = new float[n];
        var rng = new System.Random(12345);         // 固定种子：每次声音完全一样
        float peak = 1e-6f;
        for (int i = 0; i < n; i++)
        {
            float t = (float)i / sr;
            float attack = Mathf.Clamp01(t / 0.003f);   // 3 ms 起音，避免爆音
            float noise = (float)(rng.NextDouble() * 2.0 - 1.0) * Mathf.Exp(-t / 0.05f);
            float thump = Mathf.Sin(2f * Mathf.PI * 60f * t) * Mathf.Exp(-t / 0.12f);
            data[i] = attack * (0.7f * noise + 0.6f * thump);
            peak = Mathf.Max(peak, Mathf.Abs(data[i]));
        }
        float g = 0.9f / peak;
        for (int i = 0; i < n; i++) data[i] *= g;
        var clip = AudioClip.Create("startle_bang", n, 1, sr, false);
        clip.SetData(data, 0);
        return clip;
    }

    public override void Fire(string eventKey)
    {
        if (eventKey != "bang") { base.Fire(eventKey); return; }
        if (_bangAt >= 0f) return;                   // 一个试次只响一次
        _bangAt = Time.realtimeSinceStartup;
        if (_audio != null && _clip != null) _audio.PlayOneShot(_clip, 1f);
        StartCoroutine(Slam());
    }

    private IEnumerator Slam()
    {
        const float dur = 0.12f;
        float t0 = Time.realtimeSinceStartup;
        while (true)
        {
            float k = Mathf.Clamp01((Time.realtimeSinceStartup - t0) / dur);
            _hinge.localRotation = Quaternion.Euler(0f, Mathf.Lerp(OpenAngle, 0f, k * k), 0f);   // 加速关上
            if (k >= 1f) break;
            yield return null;
        }
        _slammed = true;
        Emit("door_closed");
    }

    public override bool Completed => _slammed && Time.realtimeSinceStartup - _bangAt > 2f;

    private void OnDestroy()
    {
        if (_clip != null) Destroy(_clip);
        foreach (var m in _mats) if (m != null) Destroy(m);   // 运行时 new 的材质不会随物体销毁
    }
}
