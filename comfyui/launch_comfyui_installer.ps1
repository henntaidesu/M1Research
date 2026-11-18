param (
    [string]$InstallPath,
    [switch]$DisableProxy,
    [string]$UseCustomProxy,
    [switch]$DisablePyPIMirror,
    [switch]$DisableUV,
    [switch]$DisableGithubMirror,
    [string]$UseCustomGithubMirror,
    [string]$CorePrefix,
    [switch]$RestoreFromSnapshot,
    [string]$SnapshotPath,
    [switch]$DisableSnapshot,
    [switch]$NoPause,
    [Parameter(ValueFromRemainingArguments=$true)]$ExtraArgs
)

function Join-NormalizedPath {
    $joined = $args[0]
    for ($i = 1; $i -lt $args.Count; $i++) { $joined = Join-Path $joined $args[$i] }
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($joined).TrimEnd('\', '/')
}


function Get-TrimmedTextFile {
    param (
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Encoding
    )
    if (!(Test-Path $Path)) {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($Encoding)) {
        $content = Get-Content -Path $Path -Raw
    } else {
        $content = Get-Content -Path $Path -Raw -Encoding $Encoding
    }
    if ([string]::IsNullOrWhiteSpace($content)) {
        return $null
    }
    return $content.Trim()
}


function Resolve-CorePrefix {
    param (
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string[]]$PrefixList,
        [AllowNull()][string]$ConfiguredPrefix
    )
    $target_prefix = $null
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPrefix)) {
        $origin_core_prefix = $ConfiguredPrefix.TrimEnd('\', '/')
        if ([System.IO.Path]::IsPathRooted($origin_core_prefix)) {
            $from_uri = New-Object System.Uri($BasePath.Replace('\', '/') + '/')
            $to_uri = New-Object System.Uri($origin_core_prefix.Replace('\', '/'))
            $target_prefix = $from_uri.MakeRelativeUri($to_uri).ToString().Trim('/')
        } else {
            $target_prefix = $origin_core_prefix
        }
    }
    if ([string]::IsNullOrWhiteSpace($target_prefix)) {
        foreach ($i in $PrefixList) {
            $found_dir = Get-ChildItem -Path $BasePath -Directory -Filter $i -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found_dir) {
                $target_prefix = $found_dir.Name
                break
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($target_prefix)) {
        $target_prefix = 'core'
    }
    return $target_prefix
}

if ($null -eq $script:InstallPath) {
    $script:InstallPath = $PSScriptRoot
} else {
    $script:InstallPath = Join-NormalizedPath $script:InstallPath
}

& {
    $prefix_list = @("core", "ComfyUI*")
    $core_prefix_file = Join-NormalizedPath $PSScriptRoot "core_prefix.txt"
    $origin_core_prefix = if ($script:CorePrefix) {
        $script:CorePrefix
    } else {
        Get-TrimmedTextFile $core_prefix_file -Encoding UTF8
    }
    $env:CORE_PREFIX = Resolve-CorePrefix -BasePath $PSScriptRoot -PrefixList $prefix_list -ConfiguredPrefix $origin_core_prefix
}
if (-not $script:InstallPath) {
    $script:InstallPath = $PSScriptRoot
}



# 消息输出
function Write-Log {
    [CmdletBinding()]
    param(
        [string]$Message,
        [ValidateSet("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")]
        [string]$Level = "INFO",
        [string]$Name = "ComfyUI Installer"
    )
    Write-Host "[" -NoNewline
    Write-Host $Name -ForegroundColor Blue -NoNewline
    Write-Host "]-|" -NoNewline
    Write-Host (Get-Date -Format "HH:mm:ss") -ForegroundColor Gray -NoNewline
    Write-Host "|-" -NoNewline
    switch ($Level) {
        "DEBUG"    { Write-Host "DEBUG" -ForegroundColor Cyan -NoNewline }
        "INFO"     { Write-Host "INFO" -ForegroundColor Green -NoNewline }
        "WARNING"  { Write-Host "WARNING" -ForegroundColor Yellow -NoNewline }
        "ERROR"    { Write-Host "ERROR" -ForegroundColor Red -NoNewline }
        "CRITICAL" { Write-Host "CRITICAL" -ForegroundColor White -BackgroundColor Red -NoNewline }
    }
    Write-Host ": $Message"
}


# 下载 ComfyUI Installer
function Download-Installer {
    # 可用的下载源
    $urls = @(
        "https://github.com/licyk/sd-webui-all-in-one/raw/main/installer/comfyui_installer.ps1",
        "https://gitee.com/licyk/sd-webui-all-in-one/raw/main/installer/comfyui_installer.ps1",
        "https://github.com/licyk/sd-webui-all-in-one/releases/download/comfyui_installer/comfyui_installer.ps1",
        "https://gitee.com/licyk/sd-webui-all-in-one/releases/download/comfyui_installer/comfyui_installer.ps1",
        "https://gitlab.com/licyk/sd-webui-all-in-one/-/raw/main/installer/comfyui_installer.ps1"
    )
    $i = 0

    New-Item -ItemType Directory -Path (Join-NormalizedPath $PSScriptRoot "cache") -Force > $null

    foreach ($url in $urls) {
        Write-Log "正在下载最新的 ComfyUI Installer 脚本"
        $web_request_params = @{
            Uri = $url
            UseBasicParsing = $true
            OutFile = (Join-NormalizedPath $PSScriptRoot "cache" "comfyui_installer.ps1")
            TimeoutSec = 15
            ErrorAction = "Stop"
        }
        try {
            Invoke-WebRequest @web_request_params
            Write-Log "下载 ComfyUI Installer 脚本成功"
            break
        } catch {
            Write-Log "下载 ComfyUI Installer 脚本失败: $($_.Exception.Message)" -Level ERROR
            $i += 1
            if ($i -lt $urls.Length) {
                Write-Log "重试下载 ComfyUI Installer 脚本" -Level WARNING
            } else {
                Write-Log "下载 ComfyUI Installer 脚本失败, 可尝试重新运行 ComfyUI Installer 下载脚本" -Level ERROR
                return $false
            }
        }
    }
    return $true
}


# 获取本地配置文件参数
function Get-LocalSetting {
    $arg = @{}
    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_pypi_mirror.txt")) -or ($script:DisablePyPIMirror)) {
        $arg.Add("-DisablePyPIMirror", $true)
    }

    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt")) -or ($script:DisableProxy)) {
        $arg.Add("-DisableProxy", $true)
    } else {
        if ((Test-Path (Join-NormalizedPath $PSScriptRoot "proxy.txt")) -or ($script:UseCustomProxy)) {
            if ($script:UseCustomProxy) {
                $proxy_value = $script:UseCustomProxy
            } else {
                $proxy_value = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "proxy.txt") -Encoding UTF8
            }
            if (-not [string]::IsNullOrWhiteSpace($proxy_value)) {
                $arg.Add("-UseCustomProxy", $proxy_value)
            }
        }
    }

    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_uv.txt")) -or ($script:DisableUV)) {
        $arg.Add("-DisableUV", $true)
    }

    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_gh_mirror.txt")) -or ($script:DisableGithubMirror)) {
        $arg.Add("-DisableGithubMirror", $true)
    } else {
        if ((Test-Path (Join-NormalizedPath $PSScriptRoot "gh_mirror.txt")) -or ($script:UseCustomGithubMirror)) {
            if ($script:UseCustomGithubMirror) {
                $github_mirror = $script:UseCustomGithubMirror
            } else {
                $github_mirror = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "gh_mirror.txt") -Encoding UTF8
            }
            if (-not [string]::IsNullOrWhiteSpace($github_mirror)) {
                $arg.Add("-UseCustomGithubMirror", $github_mirror)
            }
        }
    }

    $arg.Add("-InstallPath", $script:InstallPath)
    $arg.Add("-CorePrefix", $script:CorePrefix)
    if ($script:RestoreFromSnapshot) {
        $arg.Add("-RestoreFromSnapshot", $true)
    }
    if (-not [string]::IsNullOrWhiteSpace($script:SnapshotPath)) {
        $arg.Add("-SnapshotPath", $script:SnapshotPath)
    }
    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_snapshot.txt")) -or ($script:DisableSnapshot)) {
        $arg.Add("-DisableSnapshot", $true)
    }

    return $arg
}


# 处理额外命令行参数
function Get-ExtraArgs {
    $extra_args = New-Object System.Collections.ArrayList

    foreach ($a in $ExtraArgs) {
        $extra_args.Add($a) | Out-Null
    }

    $params = $extra_args.ForEach{
        if ($_ -match '\s|"') { "'{0}'" -f ($_ -replace "'", "''") }
        else { $_ }
    } -join ' '

    return $params
}


function Main {
    $status = Download-Installer

    if ($status) {
        Write-Log "运行 ComfyUI Installer 中"
        $arg = Get-LocalSetting
        $extra_args = Get-ExtraArgs
        $script_path = Join-NormalizedPath $PSScriptRoot "cache" "comfyui_installer.ps1"
        try {
            Invoke-Expression "& `"$script_path`" $extra_args @arg" -ErrorAction Stop
        }
        catch {
            Write-Log "运行 ComfyUI Installer 时出现了错误: $_" -Level ERROR
            if (!($script:NoPause)) { Read-Host | Out-Null }
            exit 1
        }
    } else {
        if (!($script:NoPause)) { Read-Host | Out-Null }
        exit 1
    }
}

###################

Main