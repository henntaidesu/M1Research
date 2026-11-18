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
禁用 ComfyUI Installer 更新检查
"@)][switch]$DisableUpdate,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置代理服务器
"@)][switch]$DisableProxy,

    [Parameter(HelpMessage=@"
使用自定义的代理服务器地址, 例如代理服务器地址为 http://127.0.0.1:10809, 则使用 -UseCustomProxy `"http://127.0.0.1:10809`" 设置代理服务器地址
"@)][string]$UseCustomProxy,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置 GitHub 镜像源
"@)][switch]$DisableGithubMirror,

    [Parameter(HelpMessage=@"
禁用 CLI 自动选择下载镜像源; 禁用后才会遵守 PyPI / GitHub / Hugging Face / 模型下载来源等手动镜像设置
"@)][switch]$DisableAutoMirror,

    [Parameter(HelpMessage=@"
使用自定义的 GitHub 镜像站地址
"@)][string]$UseCustomGithubMirror,

    [Parameter(HelpMessage=@"
禁用自动快照, 包括安装结束后的结果快照以及管理脚本执行前的自动快照
"@)][switch]$DisableSnapshot,

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
        DisableProxy = $script:DisableProxy
        UseCustomProxy = $script:UseCustomProxy
        DisableGithubMirror = $script:DisableGithubMirror
        DisableAutoMirror = $script:DisableAutoMirror
        UseCustomGithubMirror = $script:UseCustomGithubMirror
        DisableUpdate = $script:DisableUpdate
        BuildMode = $script:BuildMode
        DisableSnapshot = $script:DisableSnapshot
        NoPause = $script:NoPause
    }
    (Import-Module (Join-Path $PSScriptRoot "modules.psm1") -Function "Join-NormalizedPath", "Get-TrimmedTextFile", "Resolve-CorePrefix", "Initialize-EnvPath", "Write-Log", "Format-CommandLineArgumentForLog", "Format-CoreCliCommandForLog", "Write-CoreCliFailureCommand", "Set-CorePrefix", "Get-Version", "Update-Installer", "Set-Proxy", "Set-GithubMirror", "Update-SDWebUiAllInOne", "Set-SnapshotCliArgs", "Get-HelpMessage", "Test-PythonAndGit", "Get-NativeCommandExitCode", "Exit-ManagerScript" -PassThru -Force -ErrorAction Stop).Invoke({
        param ($cfg)
        $script:OriginalScriptPath = $cfg.OriginalScriptPath
        $script:LaunchCommandLine = $cfg.LaunchCommandLine
        $script:Help = $cfg.Help
        $script:CorePrefix = $cfg.CorePrefix
        $script:DisableProxy = $cfg.DisableProxy
        $script:UseCustomProxy = $cfg.UseCustomProxy
        $script:DisableGithubMirror = $cfg.DisableGithubMirror
        $script:DisableAutoMirror = $cfg.DisableAutoMirror
        $script:UseCustomGithubMirror = $cfg.UseCustomGithubMirror
        $script:DisableUpdate = $cfg.DisableUpdate
        $script:BuildMode = $cfg.BuildMode
        $script:DisableSnapshot = $cfg.DisableSnapshot
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
    Set-GithubMirror $launch_params
    return $launch_params
}


function Main {
    Get-HelpMessage
    Get-Version
    Set-CorePrefix
    Initialize-EnvPath
    Test-PythonAndGit
    Set-Proxy
    Update-Installer
    Update-SDWebUiAllInOne

    if (!(Test-Path (Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX))) {
        Write-Log "内核路径 $(Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX) 未找到, 请检查 ComfyUI 是否已正确安装, 或者尝试运行 ComfyUI Installer 进行修复" -Level ERROR
        Exit-ManagerScript -ExitCode 1
    }

    $launch_args = Get-LaunchCoreArgs
    $core_cli_command = @("python", "-m", "sd_webui_all_in_one", "comfyui", "update")
    Set-SnapshotCliArgs $launch_args
    & python -m sd_webui_all_in_one comfyui update $launch_args
    $exit_code = Get-NativeCommandExitCode -Success $?
    if ($exit_code -ne 0) { Write-CoreCliFailureCommand -CommandPrefix $core_cli_command -Arguments $launch_args -ExitCode $exit_code }

    Write-Log "退出 ComfyUI 更新脚本"
    Exit-ManagerScript -ExitCode $exit_code
}

###################

Main