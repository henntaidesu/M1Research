# Meta Quest Pro 连接指南（FEA → 后端 → 微情感生成）

目标：头显读取 70 维 FEA（面部混合形状）→ 通过 `QuestProFaceCapture.cs` 实时 POST 到 PC 后端
`/api/fea` → 网页「微情感生成」（数据来源选 **Quest Pro 头显 (FEA)**）实时驱动 ComfyUI 生成。

> Quest Pro **不开放机内摄像头原始图像**（隐私），只提供抽象 FEA——这是唯一可行路径。

本机后端地址：`http://10.0.10.10:9501`（以太网 IP）。同机（Quest Link）用 `http://127.0.0.1:9501`。

---

## 0. 后端准备（必做）
1. 后端已加 `/api/fea` 端点，**必须重启**：`cd backend && python main.py`（监听 0.0.0.0:9501）。
2. 放行防火墙入站 9501（管理员 PowerShell，仅 Path B 需要）：
   ```
   netsh advfirewall firewall add rule name="webFER-9501" dir=in action=allow protocol=TCP localport=9501
   ```
3. 自测端点（任意机器）：
   ```
   curl -X POST http://10.0.10.10:9501/api/fea -H "Content-Type: application/json" -d "{\"blendshapes\":[0,0,0,0,0.9,0.9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.9,0.9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]}"
   ```
   返回含 `"dominant_en":"happy"` 即通。

## Path A（推荐先试）：Quest Link/Air Link + Unity 编辑器（从零搭建）
头显作为 PC VR 头显，Unity 直接拿到 Face Tracking，C# POST 到 `127.0.0.1:9501`，**无需打包 APK**。

1. **装 Unity**：Unity Hub → 装 **Unity 2022.3 LTS**（Meta XR SDK 要求 2022.3 或 2021.3）。路线 A 不需要 Android 模块。
2. **建工程**：New Project → **3D (Built-In Render Pipeline)**。
3. **装 Meta XR SDK**：Asset Store 免费领取 **Meta XR Core SDK** + **Meta XR Movement SDK**（Movement 里才有 `OVRFaceExpressions`）→ Package Manager → My Assets → Import。导入时的 **Project Setup Tool** 对 Windows 平台 **Fix All / Apply All**。
4. **配 XR**：Project Settings → XR Plug-in Management → **PC/Standalone** 标签勾 **Oculus**。
5. **Link 的 OpenXR**：Meta Quest Link 桌面软件 → 设置 → 通用 → OpenXR 运行时 **设为活动**；头显开 **Air Link** 连本 PC 并进入 Link 桌面。
6. **搭场景**：删默认 Main Camera；拖 **OVRCameraRig** 预制体进场景；其 **OVRManager** 设 **Face Tracking Support = Supported**（并勾启动请求权限）；新建空物体加 **OVRFaceExpressions** 组件。
7. **挂脚本**：复制 `DataSet/QuestProFaceCapture.cs` 到 `Assets/Scripts/`；新建空物体加 **Quest Pro Face Capture**：
   - `Face Expressions` ← 拖入第 6 步带 OVRFaceExpressions 的物体
   - `Enable Live Stream` ✓；`Backend Url` = `http://127.0.0.1:9501/api/fea`；`Stream Fps` = 12
8. **运行**：确保后端已重启在跑 → Unity **Play** → 戴头显授权面部追踪 → Console 出 `[Stream] 开始实时推送` 即成功。

> 坑：Play 报缺 `OVR...` = Movement SDK 没导入；概率不动 = 没授权/没戴稳/OVRManager 没开 Face Tracking/Link OpenXR 未设活动；网络错 = 后端没起（先开 `http://127.0.0.1:9501/health` 验）。

## Path B：打包 APK 装到头显独立运行
前提：已按 Path A 的 1–3 建好 Unity 工程并装 Meta XR Core + Movement SDK。头显与 PC 同一局域网。

1. **加 Android 模块**：Unity Hub → Installs → 2022.3 → Add Modules → **Android Build Support**（含 OpenJDK/SDK/NDK）。
2. **切平台**：Build Settings → Android → **Switch Platform** → Add Open Scenes。
3. **Setup Tool 修 Android**：Project Settings → Meta XR → 对 **Android** 平台 **Fix All / Apply All**；XR Plug-in Management → **Android** 标签勾 **Oculus**。
4. **Player Settings**（Player → Android）：Scripting Backend **IL2CPP**；Target Architectures 只勾 **ARM64**；Minimum API Level **≥ Android 10 (API 29)**；Package Name 改自己的。
5. **⚠️ 允许明文 HTTP（必做）**：Publishing Settings 勾 **Custom Main Manifest** → 编辑 `Assets/Plugins/Android/AndroidManifest.xml`，给 `<application>` 加 `android:usesCleartextTraffic="true"`（否则 http POST 被 Android 拦，连不上后端）。
6. **脚本**：`Backend Url` = `http://10.0.10.10:9501/api/fea`（PC 的 LAN IP，非 127.0.0.1）。
7. **开发者模式 + 连线**：Meta Horizon 手机 App 开头显开发者模式；USB-C 连 PC，头显里允许 USB 调试。（可选装 Meta Quest Developer Hub。）
8. **打包安装**：Build Settings → **Build And Run**（或 Build 出 apk 后 `adb install` / MQDH / SideQuest 安装）。
9. **运行验证**：先做 §0 第 2 步防火墙放行 9501、确保后端已重启；头显里从「未知来源」启动 App → 允许面部追踪；头显浏览器开 `http://10.0.10.10:9501/health` 出 JSON = 通；PC 网页 `/cn/comfyui/affective` 选 Quest FEA 来源 → 开始。

## 验证
1. 网页打开 `http://10.0.10.10:9500/cn/comfyui/affective`（或 localhost:9500）。
2. 「数据来源」选 **Quest Pro 头显 (FEA)** → 点「开始」。
3. 戴头显做表情 → 页面概率条实时变化；开「自动随情感生成」即随情绪出图。
   （Qwen-only 环境：生成方式选「服务器工作流」。）

## 常见问题
- **页面情绪不动**：确认 Unity 控制台在推、`/api/fea` 自测通、后端已重启、Path B 已放行防火墙。
- **FEA 无效**：头显要戴稳、已授权面部追踪、OVRManager 已启用 Face Tracking（`ValidExpressions=false` 会跳过）。
- **数据采集**：实时驱动之外要存数据集时，仍用按键 `CaptureSample`（写 JSON）+ 外接相机拍下半脸图；离线用 `DataSet/sync_quest_pro_capture.py` 对齐。
