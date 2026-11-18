// KeepHeadsetAwake.cs
// 采集期间不让头显息屏：显示超时关掉，失焦也继续跑。
//
// 为什么需要：一次 FEA 时序采集要连续几分钟到十几分钟，中途息屏会让
// OVRFaceExpressions 停止更新、/api/fea 断流，时间轴上就多出一段空洞
// （切窗脚本会因此断段，白采一段）。
//
// **不需要挂到任何 GameObject**：用 [RuntimeInitializeOnLoadMethod] 在场景加载后
// 自动创建一个常驻对象。之前几次踩过「脚本没挂进场景所以完全没生效」的坑，
// 这种全局性的设置不该依赖人去记得挂。
//
// 能做到与做不到，说清楚：
//   ✔ 关闭显示超时（Screen.sleepTimeout）—— 一直亮着不自动黑屏
//   ✔ 失焦仍继续运行（Application.runInBackground）
//   ✘ **摘下头显时的自动休眠管不了**：那是接近传感器 + 系统电源策略，
//     App 层无权覆盖。需要在设备侧处理：
//       - 头显 设置 → 电源/显示 里把息屏时间调长；
//       - 开发者模式下可试（随系统版本可能已失效，非官方保证）：
//           adb shell am broadcast -a com.oculus.vrpowermanager.prox_close   # 关接近传感器
//           adb shell am broadcast -a com.oculus.vrpowermanager.prox_open    # 恢复
//     采集时如果要摘下头显放桌上，务必先确认上面这条对当前固件仍有效，
//     否则时间轴会断。

using UnityEngine;

public class KeepHeadsetAwake : MonoBehaviour
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Bootstrap()
    {
        var go = new GameObject("~KeepHeadsetAwake");
        go.AddComponent<KeepHeadsetAwake>();
        DontDestroyOnLoad(go);       // 跨场景常驻，切场景也不失效
    }

    private void Awake()
    {
        Apply("startup");
    }

    // 系统在恢复焦点/从后台回来时可能把 wake lock 收回去，这里重新申明一次
    private void OnApplicationFocus(bool hasFocus)
    {
        if (hasFocus) Apply("focus");
    }

    private void OnApplicationPause(bool paused)
    {
        if (!paused) Apply("resume");
    }

    private static void Apply(string reason)
    {
        Screen.sleepTimeout = SleepTimeout.NeverSleep;
        Application.runInBackground = true;
        Debug.Log($"[KeepAwake] 已关闭显示超时（{reason}）；"
                  + "注意：摘下头显触发的休眠属系统电源策略，App 无法覆盖。");
    }
}
