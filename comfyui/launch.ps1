param (
    [Parameter(HelpMessage=@"
获取 ComfyUI Installer 的帮助信息
"@)][switch]$Help,

    [Parameter(HelpMessage=@"
设置内核的路径前缀, 默认路径前缀为 core
"@)][string]$CorePrefix,

    [Parameter(HelpMessage=@"
启用 ComfyUI Installer 构建模式
"@)][switch]$BuildMode,

    [Parameter(HelpMessage=@"
禁用 PyPI 软件包镜像源, 使用 PyPI 官方源下载 Python 软件包
"@)][switch]$DisablePyPIMirror,

    [Parameter(HelpMessage=@"
禁用 CLI 自动选择下载镜像源; 禁用后才会遵守 PyPI / GitHub / Hugging Face / 模型下载来源等手动镜像设置
"@)][switch]$DisableAutoMirror,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 更新检查
"@)][switch]$DisableUpdate,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置代理服务器
"@)][switch]$DisableProxy,

    [Parameter(HelpMessage=@"
使用自定义的代理服务器地址, 例如代理服务器地址为 http://127.0.0.1:10809, 则使用 -UseCustomProxy "http://127.0.0.1:10809" 设置代理服务器地址
"@)][string]$UseCustomProxy,

    [Parameter(HelpMessage=@"
禁用 Hugging Face 镜像源, 不使用 Hugging Face 镜像源下载文件
"@)][switch]$DisableHuggingFaceMirror,

    [Parameter(HelpMessage=@"
使用自定义 Hugging Face 镜像源地址, 例如代理服务器地址为 https://hf-mirror.com, 则使用 -UseCustomHuggingFaceMirror "https://hf-mirror.com" 设置 Hugging Face 镜像源地址
"@)][string]$UseCustomHuggingFaceMirror,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置 GitHub 镜像源
"@)][switch]$DisableGithubMirror,

    [Parameter(HelpMessage=@"
使用自定义的 GitHub 镜像站地址
"@)][string]$UseCustomGithubMirror,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 使用 uv 安装 Python 软件包, 使用 Pip 安装 Python 软件包
"@)][switch]$DisableUV,

    [Parameter(HelpMessage=@"
设置 ComfyUI 自定义启动参数, 如启用 --fast 和 --auto-launch, 则使用 -LaunchArg "--fast --auto-launch" 进行启用
"@)][string]$LaunchArg,

    [Parameter(HelpMessage=@"
创建 ComfyUI 启动创建启动快捷方式
"@)][switch]$EnableShortcut,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 通过 PYTORCH_CUDA_ALLOC_CONF / PYTORCH_ALLOC_CONF 环境变量设置 CUDA 内存分配器
"@)][switch]$DisableCUDAMalloc,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 检查 ComfyUI 运行环境中存在的问题, 禁用后可能会导致 ComfyUI 环境中存在的问题无法被发现并修复
"@)][switch]$DisableEnvCheck,

    [Parameter(HelpMessage=@"
禁用 Hotpatcher 补丁系统
"@)][switch]$DisableHotpatcher,

    [Parameter(HelpMessage=@"
指定 Hotpatcher runtime 通信端口, 端口范围为 1..65535
"@)][int]$HotpatcherPort,

    [Parameter(HelpMessage=@"
启用 Hotpatcher runtime host 连接
"@)][switch]$EnableHotpatcherRuntime,

    [Parameter(HelpMessage=@"
脚本执行完成后不暂停, 直接退出
"@)][switch]$NoPause
)
try {
    $config = @{
        OriginalScriptPath = $script:PSCommandPath
        LaunchCommandLine = if ($script:MyInvocation.Line) { $script:MyInvocation.Line } else { $([Environment]::CommandLine) }
        Help = $script:Help
        CorePrefix = $script:CorePrefix
        DisableUV = $script:DisableUV
        DisableProxy = $script:DisableProxy
        UseCustomProxy = $script:UseCustomProxy
        DisablePyPIMirror = $script:DisablePyPIMirror
        DisableAutoMirror = $script:DisableAutoMirror
        DisableHuggingFaceMirror = $script:DisableHuggingFaceMirror
        UseCustomHuggingFaceMirror = $script:UseCustomHuggingFaceMirror
        DisableGithubMirror = $script:DisableGithubMirror
        UseCustomGithubMirror = $script:UseCustomGithubMirror
        DisableCUDAMalloc = $script:DisableCUDAMalloc
        DisableUpdate = $script:DisableUpdate
        BuildMode = $script:BuildMode
        DisableHotpatcher = $script:DisableHotpatcher
        HotpatcherPort = $script:HotpatcherPort
        HotpatcherPortSpecified = $PSBoundParameters.ContainsKey("HotpatcherPort")
        EnableHotpatcherRuntime = $script:EnableHotpatcherRuntime
        LaunchArg = $script:LaunchArg
        EnableShortcut = $script:EnableShortcut
        DisableEnvCheck = $script:DisableEnvCheck
        NoPause = $script:NoPause
    }
    (Import-Module (Join-Path $PSScriptRoot "modules.psm1") -Function "Join-NormalizedPath", "Get-TrimmedTextFile", "Resolve-CorePrefix", "Initialize-EnvPath", "Invoke-WindowsLongPathsStartupCheck", "Write-Log", "Format-CommandLineArgumentForLog", "Format-CoreCliCommandForLog", "Write-CoreCliFailureCommand", "Set-CorePrefix", "Get-Version", "Update-Installer", "Set-Proxy", "Set-PyPIMirror", "Set-HuggingFaceMirror", "Set-GithubMirror", "Set-uv", "Set-PyTorchCUDAMemoryAlloc", "Get-WebUILaunchArgs", "Add-Shortcut", "Test-MSVCPPRedistributable", "Test-WebUIEnv", "Set-Hotpatcher", "Update-SDWebUiAllInOne", "Get-CurrentPlatform", "New-AppShortcut", "Get-HelpMessage", "Test-PythonAndGit", "Get-NativeCommandExitCode", "Exit-ManagerScript" -PassThru -Force -ErrorAction Stop).Invoke({
        param ($cfg)
        $script:OriginalScriptPath = $cfg.OriginalScriptPath
        $script:LaunchCommandLine = $cfg.LaunchCommandLine
        $script:Help = $cfg.Help
        $script:CorePrefix = $cfg.CorePrefix
        $script:DisableUV = $cfg.DisableUV
        $script:DisableProxy = $cfg.DisableProxy
        $script:UseCustomProxy = $cfg.UseCustomProxy
        $script:DisablePyPIMirror = $cfg.DisablePyPIMirror
        $script:DisableAutoMirror = $cfg.DisableAutoMirror
        $script:DisableHuggingFaceMirror = $cfg.DisableHuggingFaceMirror
        $script:UseCustomHuggingFaceMirror = $cfg.UseCustomHuggingFaceMirror
        $script:DisableGithubMirror = $cfg.DisableGithubMirror
        $script:UseCustomGithubMirror = $cfg.UseCustomGithubMirror
        $script:DisableCUDAMalloc = $cfg.DisableCUDAMalloc
        $script:DisableUpdate = $cfg.DisableUpdate
        $script:BuildMode = $cfg.BuildMode
        $script:DisableHotpatcher = $cfg.DisableHotpatcher
        $script:HotpatcherPort = $cfg.HotpatcherPort
        $script:HotpatcherPortSpecified = $cfg.HotpatcherPortSpecified
        $script:EnableHotpatcherRuntime = $cfg.EnableHotpatcherRuntime
        $script:LaunchArg = $cfg.LaunchArg
        $script:EnableShortcut = $cfg.EnableShortcut
        $script:DisableEnvCheck = $cfg.DisableEnvCheck
        $script:NoPause = $cfg.NoPause
    }, $config)
}
catch {
    Write-Error "导入 Installer 模块发生错误: $_"
    Write-Host "这可能是 Installer 文件出现了损坏, 请运行 " -ForegroundColor White -NoNewline
    Write-Host "launch_comfyui_installer.ps1" -ForegroundColor Yellow -NoNewline
    Write-Host " 脚本修复该问题" -ForegroundColor White
    if ((-not $script:BuildMode) -and (-not $script:NoPause)) { Read-Host | Out-Null }
    exit 1
}


# 获取启动 SD WebUI All In One 内核的启动参数
function Get-LaunchCoreArgs {
    $launch_params = New-Object System.Collections.ArrayList
    Set-uv $launch_params
    Set-GithubMirror $launch_params
    Set-PyPIMirror $launch_params
    if (!($script:BuildMode)) {
        Set-HuggingFaceMirror $launch_params
        Get-WebUILaunchArgs $launch_params
        Set-PyTorchCUDAMemoryAlloc $launch_params
        Test-WebUIEnv $launch_params
        Set-Hotpatcher $launch_params
        $launch_params.Add("--interactive") | Out-Null
    }
    return $launch_params
}


function Main {
    Get-HelpMessage
    Get-Version
    Set-CorePrefix
    Initialize-EnvPath
    if (!($script:BuildMode)) {
        Invoke-WindowsLongPathsStartupCheck
    }
    Test-PythonAndGit
    Set-Proxy
    Update-Installer
    Update-SDWebUiAllInOne

    if (!(Test-Path (Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX))) {
        Write-Log "内核路径 $(Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX) 未找到, 请检查 ComfyUI 是否已正确安装, 或者尝试运行 ComfyUI Installer 进行修复" -Level ERROR
        Exit-ManagerScript -ExitCode 1
    }

    if (!($script:BuildMode)) {
        Test-MSVCPPRedistributable
    }
    $launch_args = Get-LaunchCoreArgs
    Add-Shortcut

    if ($script:BuildMode) {
        Write-Log "ComfyUI Installer 构建模式已启用, 仅检查 ComfyUI 运行环境"
        $core_cli_command = @("python", "-m", "sd_webui_all_in_one", "comfyui", "check-env")
        & python -m sd_webui_all_in_one comfyui check-env $launch_args
        $exit_code = Get-NativeCommandExitCode -Success $?
        if ($exit_code -ne 0) { Write-CoreCliFailureCommand -CommandPrefix $core_cli_command -Arguments $launch_args -ExitCode $exit_code }
    } else {
        $core_cli_command = @("python", "-m", "sd_webui_all_in_one", "comfyui", "launch")
        & python -m sd_webui_all_in_one comfyui launch $launch_args
        $exit_code = Get-NativeCommandExitCode -Success $?
        if ($exit_code -ne 0) { Write-CoreCliFailureCommand -CommandPrefix $core_cli_command -Arguments $launch_args -ExitCode $exit_code }
        if ($exit_code -eq 0) {
            Write-Log "ComfyUI 正常退出"
        } else {
            $help_msg = @"


            [问题反馈指南]
            如需反馈问题，请打开 Issue 页面：
            https://github.com/licyk/sd-webui-all-in-one/issues
            请附上：安装器名称、操作系统、问题描述、复现步骤、完整控制台日志。
"@
            Write-Log "ComfyUI 出现异常, 已退出, 请检查控制台日志${help_msg}" -Level ERROR
        }
    }
    Exit-ManagerScript -ExitCode $exit_code
}

###################

Main