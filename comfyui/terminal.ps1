param(
    [switch]$Help,
    [switch]$NoPause,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)
try {
    $config = @{
        OriginalScriptPath = $script:PSCommandPath
        LaunchCommandLine = if ($script:MyInvocation.Line) { $script:MyInvocation.Line } else { $([Environment]::CommandLine) }
        Help = $script:Help
        NoPause = $script:NoPause
    }
    (Import-Module (Join-Path $PSScriptRoot "modules.psm1") -Function "Join-NormalizedPath", "Write-Log" -PassThru -Force -ErrorAction Stop).Invoke({
        param ($cfg)
        $script:OriginalScriptPath = $cfg.OriginalScriptPath
        $script:LaunchCommandLine = $cfg.LaunchCommandLine
        $script:Help = $cfg.Help
        $script:NoPause = $cfg.NoPause
    }, $config)
}
catch {
    Write-Error "导入 Installer 模块发生错误: $_"
    Write-Host "这可能是 Installer 文件出现了损坏, 请运行 " -ForegroundColor White -NoNewline
    Write-Host "launch_comfyui_installer.ps1" -ForegroundColor Yellow -NoNewline
    Write-Host " 脚本修复该问题" -ForegroundColor White
    if (!($script:NoPause)) { Read-Host | Out-Null }
    exit 1
}
$pass_args = @()
if ($script:NoPause) { $pass_args += "-NoPause" }
if ($script:Help) { $pass_args += "-Help" }
if ($Arguments) { $pass_args += $Arguments }
if ($script:Help) {
    & "$((Get-Process -Id $PID).Path)" -File "$(Join-NormalizedPath $PSScriptRoot "activate.ps1")" @pass_args
} else {
    Write-Log "执行 ComfyUI Installer 激活环境脚本"
    & "$((Get-Process -Id $PID).Path)" -NoExit -File "$(Join-NormalizedPath $PSScriptRoot "activate.ps1")" @pass_args
}