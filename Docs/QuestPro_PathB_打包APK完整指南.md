# Path B 完整指南（Unity 6）：把 FEA 采集打包成 APK 装进 Quest Pro

自包含、从零到跑通，**按 Unity 6（6000.x）逻辑**编写。头显独立运行一个 Unity 应用，
读取 70 维面部数据（FEA），经局域网 POST 到 PC 后端，驱动网页「微情感生成」。

**数据流**
```
[Quest Pro 上的 APK(Unity6)] --读70维FEA--> --POST http://10.0.10.10:9501/api/fea--> [PC 后端]
                                                                        ↓ 规则式判7类情绪
[PC 浏览器 微情感生成页] --轮询 /api/fea/latest--> 实时驱动 ComfyUI 生成
```
- PC 局域网 IP：**10.0.10.10**；后端 **9501**；前端 **9500**。
- Quest Pro 不开放机内摄像头原始图像，只给抽象 FEA——这是唯一路径。

---

## 🚨 踩坑总结（务必先读，都是实际踩过的）

### 坑 0：Unity 版本（最大的坑，直接决定成败）
必须用 **Unity 6.0 LTS = 6000.0.x**，个人版免费。其它两个都不行：

| 版本 | 现象 | 原因 |
|---|---|---|
| **2022.3.76f1** | 新建项目得到**空文件夹**、编辑器闪退（return code 198） | 是 **Extended LTS**，只有付费 Industry/Enterprise 能用；个人版启动即 license 报错、什么都不写 |
| **6000.5.2f1（6.5）** | 一堆红色 **CS0619 ... is obsolete** 编译错误（`GetInstanceID()`、`EntityId`），`Failed to resolve assembly 'meta.xr.mrutilitykit.editor'` | Unity **太新**，Meta XR SDK 还没适配；SDK 用的旧 API 被 6.5 标成「废弃即报错」 |
| ✅ **6000.0.x（6.0 LTS）** | 正常 | 免费 + Meta XR SDK 官方支持的版本 |

> 换版本方法：Hub → 安装 → 安装编辑器 → 选 **6000.0.xxf1**（勾 Android Build Support）；
> Hub → 项目 → 点项目的编辑器版本 ⇅ → 选 6000.0 打开。用 6.0 打开旧项目若出错，就用 6.0 新建项目重来。

### 坑 1：菜单找不到「Build Settings」
Unity 6 改名叫 **File → Build Profiles**。

### 坑 2：不用通用 Android，用「Meta Quest」平台
装了 Meta XR SDK 后 Build Profiles 里有 **Meta Quest** 平台 → **Enable Platform** → **Switch Platform** 设为 Active。它自动配好 XR/ARM64/API 等。

### 坑 3：Package Manager / My Assets 在**编辑器里**，不在 Hub
先去 **assetstore.unity.com 网站**搜 Meta XR All-in-One SDK → **Add to My Assets**（领取）；再回编辑器 Window → Package Manager → My Assets → Import。

### 坑 4：没有「Building Blocks」菜单
不同 SDK 版本不一定有。用传统法：Project 搜 **OVRCameraRig** 预制体拖进场景 → OVRManager 的 **Quest Features → Face Tracking Support = Supported** → 空物体加 **OVRFaceExpressions** 组件。

### 坑 5：Add Component 搜不到自己的脚本（只有「New script」）
= **项目里有编译错误**，所有自定义脚本都不可用。**先看 Console（底部标签）里的红色 Error**，修完才会出现。

### 坑 6：Hub 里项目「未找到」/ 空文件夹残留
是失效记录（多半就是坑 0 造成的）。弹窗点 **从列表移除项目**，或项目行 ⋯ → 移除。

### 坑 7（仅联网方案需要）：明文 HTTP + 权限
- 打包后 http POST 连不上后端 → 必须在 Custom Main Manifest 的 `<application>` 加 `android:usesCleartextTraffic="true"`。
- Manifest 常缺 `<uses-permission android:name="com.oculus.permission.FACE_TRACKING" />`，手动加。
- **别点 Meta → Tools → Android Manifest Tool 的 Create/Update**，它会覆盖你手改的 manifest。

---

## 两种方案（按需求选，别搞混）
| | 方案一：实时联网 | 方案二：自包含采集（推荐做数据集） |
|---|---|---|
| 脚本 | `QuestProFaceCapture.cs`（推 FEA 到 `/api/fea`） | `QuestFeaCollector.cs`（VR 展示刺激图 + 本地记 CSV） |
| 用途 | 情绪**实时驱动** ComfyUI 生成（网页看） | 采集「表情↔时间↔图片」**训练数据** |
| 依赖 | 后端 + 局域网 + 明文 HTTP + 防火墙（阶段一、五、七全套） | **零网络**：图片打进 APK，数据存头显本地，`adb pull` 取走 |
| 复杂度 | 高 | 低 ✅ |

> **只想采集数据训练模型 → 用方案二**：跳过本文阶段一（后端）与阶段五第 16 步（明文 HTTP）；
> 阶段六改挂 **Quest Fea Collector**；图片放 `Assets/Resources/stimulus/`；
> Graphics → Always Included Shaders 加 `Skybox/Panoramic`；打包运行后
> `adb pull /sdcard/Android/data/<包名>/files/` 取 `fea_session_*.csv`。

---

## 阶段一：PC 后端准备（先做，1 分钟，纯 PC 端）

1. **重启后端**（新端点 `/api/fea` 才生效）：
   ```
   cd backend
   python main.py            # 监听 0.0.0.0:9501
   ```
2. **放行防火墙 9501**（管理员 PowerShell）：
   ```
   netsh advfirewall firewall add rule name="webFER-9501" dir=in action=allow protocol=TCP localport=9501
   ```
3. **自测端点**（PC 上执行，返回含 `"dominant_en":"happy"` 即正常）：
   ```
   curl -X POST http://10.0.10.10:9501/api/fea -H "Content-Type: application/json" -d "{\"blendshapes\":[0,0,0,0,0.9,0.9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.9,0.9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]}"
   ```
   通了再动 Unity——这样后面排查能确定「后端没问题，只查头显侧」。

---

## 阶段二：安装 Unity 6（含 Android 模块）

4. 装 **Unity Hub**（unity.com/download）。
5. Hub → **安装** → **安装编辑器** → 选 **Unity 6（6000.x）LTS**（个人版免费；**不要**用 2022.3 的 Extended LTS 版）。
6. 勾选模块：**Android Build Support**，并展开勾其子项 **OpenJDK**、**Android SDK & NDK Tools**。
7. 等安装完成。

---

## 阶段三：建工程 + 装 Meta XR SDK

8. Hub → **新建项目** → 编辑器版本选 **Unity 6** → 模板 **3D (Built-In Render Pipeline)** →
   位置填 `E:\webFaceEmotionRec\Unity`，名称如 `AffectiveFEA` → 创建，等它打开编辑器。
   - 判断成功：项目文件夹里生成了 `Assets/`、`ProjectSettings/`。若还是空文件夹，多半是编辑器版本仍是 Extended LTS，回阶段二换 Unity 6。
9. **装 SDK**：
   - 浏览器 assetstore.unity.com → 用你的 Unity 账号登录 → 搜 **Meta XR All-in-One SDK** → **Add to My Assets**（免费）。
   - 回编辑器 → **Window → Package Manager** → 左上下拉切 **My Assets** → 找到它 → **Download → Import**。
   - 导入完成后，编辑器菜单会出现 **Meta**，且下一步 Build Profiles 里会出现 **Meta Quest** 平台。

---

## 阶段四：Build Profiles → 启用并激活 Meta Quest 平台
> **Unity 6 把「Build Settings」改名为「Build Profiles」**。别再找 Build Settings。

10. 菜单 **File → Build Profiles**。
11. 左侧 **Platforms** 列表里选 **Meta Quest**（Meta XR SDK 提供的、给 Quest 预配好的 Android 目标）。
12. 右侧点 **Enable Platform**。
    - 若提示缺 Android 模块 → Hub → 安装 → Unity 6 → **Add Modules** → 勾 Android Build Support（含 OpenJDK/SDK/NDK）→ 回来再点。
13. 启用后，点 **Switch Platform**（把 Meta Quest 设为 **Active**，取代 Windows）。切换会重导资源，等几分钟。
14. **跑 Meta XR 一键修正**：Edit → Project Settings → **Meta XR**（Project Setup Tool）→ 对 **Android/Meta Quest** 平台点 **Fix All**、**Apply All**（自动配好 XR 插件、ARM64、最低 API、面部追踪权限等）。

---

## 阶段五：Player Settings 关键项

15. 打开 Player Settings：Build Profiles 窗口右上 **Player Settings** 按钮，或 Edit → Project Settings → **Player**（Android 标签）。
    - **Other Settings**：
      - Scripting Backend = **IL2CPP**
      - Target Architectures = 只勾 **ARM64**
      - Minimum API Level = **Android 10 (API 29)** 或更高
      （Meta Quest 平台 + Setup Tool 通常已设好，核对即可。）
    - **Identification**：Package Name 改成自己的，如 `com.yourname.affectivefea`。

16. **⚠️ 允许明文 HTTP（最容易漏、漏了必失败，Meta 配置不会自动做）**
    - Android 默认禁 `http://`，我们 POST 到 `http://10.0.10.10:9501`，必须放开：
    - Player → **Publishing Settings** → 勾 **Custom Main Manifest**。
    - 打开生成的 `Assets/Plugins/Android/AndroidManifest.xml`，给 `<application ...>` 加属性：
      ```xml
      <application
          ...
          android:usesCleartextTraffic="true">
      ```
    - 顺便确认含面部追踪权限（Setup Tool 通常已加）：
      ```xml
      <uses-permission android:name="com.oculus.permission.FACE_TRACKING" />
      ```

---

## 阶段六：搭场景 + 挂脚本

17. **相机装置**：删除场景默认 **Main Camera**；Project 里搜预制体 **OVRCameraRig**，拖进 Hierarchy。
18. **开面部追踪**：选中 OVRCameraRig → Inspector 的 **OVRManager** 组件 → **Face Tracking Support = Supported**
    （要眼动则 Eye Tracking Support 也设 Supported）；勾启动时请求权限。
19. **表情数据源**：Hierarchy 右键 → Create Empty，命名 `FaceTracking` → Add Component → **OVR Face Expressions**。
20. **放脚本**：把仓库 `DataSet/QuestProFaceCapture.cs` 复制到工程 `Assets/Scripts/`。
21. **挂脚本**：Create Empty 命名 `FEAStreamer` → Add Component → **Quest Pro Face Capture** → Inspector：
    - `Face Expressions` ← 拖入第 19 步的 `FaceTracking`
    - `Enable Live Stream` = ✓
    - `Backend Url` = **`http://10.0.10.10:9501/api/fea`**（PC 的 LAN IP，非 127.0.0.1）
    - `Stream Fps` = 12

---

## 阶段七：Quest 开发者模式 + 连接 PC

22. **开发者模式**：developer.oculus.com 登录并**创建一个组织**（免费）；手机 **Meta Horizon** App → 你的头显 → **开发者模式** → 打开。
23. **连线**：头显用 **USB-C** 接 PC → 戴上头显弹「允许 USB 调试」→ 允许。
24. （建议）装 **Meta Quest Developer Hub (MQDH)**：看设备是否在线、装 APK、看日志。

---

## 阶段八：打包 + 安装（Unity 6）

25. **方式一（最简单）**：File → **Build Profiles** → 确认 Meta Quest 为 Active → 右下 **Build And Run**（头显连着 USB）→ 选输出 apk 路径 → 自动编译、安装并启动。
26. **方式二**：Build Profiles → **Build** 生成 `.apk`，再任选：MQDH 拖入 / `adb install -r 你的.apk`（adb 在 Unity 的 Android SDK 的 platform-tools 里）/ SideQuest。

---

## 阶段九：在头显运行 + 验证

27. 头显：App 库 → 右上筛选 **未知来源 (Unknown Sources)** → 启动你的 App。
28. 首次运行弹**面部追踪权限** → 允许。
29. **验证 LAN 通**：头显浏览器打开 `http://10.0.10.10:9501/health` → 出 JSON = 头显能连到 PC 后端。
30. **看效果**：PC 浏览器 `http://localhost:9500/cn/comfyui/affective` →「数据来源」选 **Quest Pro 头显 (FEA)** → 点「开始」→ 戴头显做表情，7 类概率条实时变；开「自动随情感生成」即随情绪出图。
    - Qwen-only（无 SD checkpoint）：生成方式选 **服务器工作流**。

---

## 常见坑速查（Unity 6）
| 现象 | 原因 / 解决 |
|---|---|
| 建项目得到空文件夹、编辑器闪退 | 用了 2022.3 Extended LTS，个人版无授权 → 换 **Unity 6** |
| 找不到 Build Settings | Unity 6 改名 **Build Profiles**（File → Build Profiles）|
| 没有 Meta Quest 平台 | Meta XR All-in-One SDK 没导入成功，重做阶段三 |
| 头显能上网但 POST 失败 | **忘了阶段五第 16 步明文 HTTP**（`usesCleartextTraffic=true`）——最常见 |
| 概率条不动 / 生成不触发 | 头显 App 没在推：看 MQDH 日志有无 `[Stream] 开始实时推送`；`/health` 是否可达 |
| `ValidExpressions` 一直 false | 没授权面部追踪 / 头显没戴稳 / OVRManager 没设 Face Tracking Supported |
| Build And Run 找不到设备 | 没开开发者模式 / USB 调试没允许 / 换线 / MQDH 里确认设备在线 |
| `/health` 打不开 | 防火墙没放行 9501 / 头显与 PC 不同网段 / 后端没重启 |

## 采集数据（可选，独立于实时驱动）
本 APK 除实时推送外，按头显手柄 **A 键** 会 `CaptureSample` 写一帧 JSON 到头显本地
（`Application.persistentDataPath/capture/<split>/`）。做数据集时配外接相机拍下半脸图，
离线用 `DataSet/sync_quest_pro_capture.py` + `DataSet/prepare_quest_pro.py` 处理。
