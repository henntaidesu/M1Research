param (
    [Parameter(HelpMessage=@"
获取 ComfyUI Installer 的帮助信息
"@)][switch]$Help,

    [Parameter(HelpMessage=@"
设置内核的路径前缀, 默认路径前缀为 core
"@)][string]$CorePrefix,

    [Parameter(HelpMessage=@"
禁用 PyPI 软件包镜像源, 使用 PyPI 官方源下载 Python 软件包
"@)][switch]$DisablePyPIMirror,

    [Parameter(HelpMessage=@"
禁用 CLI 自动选择下载镜像源; 禁用后才会遵守 PyPI / GitHub / Hugging Face / 模型下载来源等手动镜像设置
"@)][switch]$DisableAutoMirror,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置 GitHub 镜像源
"@)][switch]$DisableGithubMirror,

    [Parameter(HelpMessage=@"
使用自定义的 GitHub 镜像站地址
"@)][string]$UseCustomGithubMirror,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置代理服务器
"@)][switch]$DisableProxy,

    [Parameter(HelpMessage=@"
使用自定义的代理服务器地址, 例如代理服务器地址为 http://127.0.0.1:10809, 则使用 -UseCustomProxy `"http://127.0.0.1:10809`" 设置代理服务器地址
"@)][string]$UseCustomProxy,

    [Parameter(HelpMessage=@"
禁用 Hugging Face 镜像源, 不使用 Hugging Face 镜像源下载文件
"@)][switch]$DisableHuggingFaceMirror,

    [Parameter(HelpMessage=@"
使用自定义 Hugging Face 镜像源地址, 例如代理服务器地址为 https://hf-mirror.com, 则使用 -UseCustomHuggingFaceMirror `"https://hf-mirror.com`" 设置 Hugging Face 镜像源地址
"@)][string]$UseCustomHuggingFaceMirror,

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
        DisablePyPIMirror = $script:DisablePyPIMirror
        DisableAutoMirror = $script:DisableAutoMirror
        DisableGithubMirror = $script:DisableGithubMirror
        UseCustomGithubMirror = $script:UseCustomGithubMirror
        DisableProxy = $script:DisableProxy
        UseCustomProxy = $script:UseCustomProxy
        DisableHuggingFaceMirror = $script:DisableHuggingFaceMirror
        UseCustomHuggingFaceMirror = $script:UseCustomHuggingFaceMirror
        NoPause = $script:NoPause
    }
    (Import-Module (Join-Path $PSScriptRoot "modules.psm1") -Function "Join-NormalizedPath", "Get-TrimmedTextFile", "Resolve-CorePrefix", "Initialize-EnvPath", "Write-Log", "Format-CommandLineArgumentForLog", "Format-CoreCliCommandForLog", "Write-CoreCliFailureCommand", "Set-CorePrefix", "Get-Version", "Set-Proxy", "Get-CurrentPlatform", "Get-CurrentArchitecture", "Get-NormalizedFilePath", "Get-HelpMessage", "Test-PythonAndGit", "Install-HanafubukiPackage", "Move-PortableLauncherRuntime", "Get-NativeCommandExitCode", "Exit-ManagerScript" -PassThru -Force -ErrorAction Stop).Invoke({
        param ($cfg)
        $script:OriginalScriptPath = $cfg.OriginalScriptPath
        $script:LaunchCommandLine = $cfg.LaunchCommandLine
        $script:Help = $cfg.Help
        $script:CorePrefix = $cfg.CorePrefix
        $script:DisablePyPIMirror = $cfg.DisablePyPIMirror
        $script:DisableAutoMirror = $cfg.DisableAutoMirror
        $script:DisableGithubMirror = $cfg.DisableGithubMirror
        $script:UseCustomGithubMirror = $cfg.UseCustomGithubMirror
        $script:DisableProxy = $cfg.DisableProxy
        $script:UseCustomProxy = $cfg.UseCustomProxy
        $script:DisableHuggingFaceMirror = $cfg.DisableHuggingFaceMirror
        $script:UseCustomHuggingFaceMirror = $cfg.UseCustomHuggingFaceMirror
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


# PyPI 软件包镜像源
$PIP_INDEX_ADDR = "https://mirrors.cloud.tencent.com/pypi/simple"
$PIP_INDEX_ADDR_ORI = "https://pypi.python.org/simple"
$PIP_EXTRA_INDEX_ADDR = "https://mirrors.cernet.edu.cn/pypi/web/simple"
$PIP_EXTRA_INDEX_ADDR_ORI = "https://download.pytorch.org/whl"
$PIP_FIND_ADDR = "https://mirrors.aliyun.com/pytorch-wheels/torch_stable.html"
$PIP_FIND_ADDR_ORI = "https://download.pytorch.org/whl/torch_stable.html"
$USE_PIP_MIRROR = if ((!(Test-Path (Join-NormalizedPath $PSScriptRoot "disable_pypi_mirror.txt"))) -and (!($script:DisablePyPIMirror))) { $true } else { $false }
$PIP_INDEX_MIRROR = if ($USE_PIP_MIRROR) { $PIP_INDEX_ADDR } else { $PIP_INDEX_ADDR_ORI }
$PIP_EXTRA_INDEX_MIRROR = if ($USE_PIP_MIRROR) { $PIP_EXTRA_INDEX_ADDR } else { $PIP_EXTRA_INDEX_ADDR_ORI }
$PIP_FIND_MIRROR = if ($USE_PIP_MIRROR) { $PIP_FIND_ADDR } else { $PIP_FIND_ADDR_ORI }
# 环境变量
$env:PIP_INDEX_URL = "$PIP_INDEX_MIRROR"
$env:PIP_EXTRA_INDEX_URL = if ($PIP_EXTRA_INDEX_MIRROR -ne $PIP_EXTRA_INDEX_MIRROR_PYTORCH) { "$PIP_EXTRA_INDEX_MIRROR $PIP_EXTRA_INDEX_MIRROR_PYTORCH".Trim() } else { $PIP_EXTRA_INDEX_MIRROR }
$env:PIP_FIND_LINKS = "$PIP_FIND_MIRROR"
$env:UV_DEFAULT_INDEX = "$PIP_INDEX_MIRROR"
$env:UV_INDEX = if ($PIP_EXTRA_INDEX_MIRROR -ne $PIP_EXTRA_INDEX_MIRROR_PYTORCH) { "$PIP_EXTRA_INDEX_MIRROR $PIP_EXTRA_INDEX_MIRROR_PYTORCH".Trim() } else { $PIP_EXTRA_INDEX_MIRROR }
$env:UV_FIND_LINKS = "$PIP_FIND_MIRROR"
$env:UV_LINK_MODE = "copy"
$env:UV_HTTP_TIMEOUT = 30
$env:UV_CONCURRENT_DOWNLOADS = 50
$env:UV_INDEX_STRATEGY = "unsafe-best-match"
$env:PIP_DISABLE_PIP_VERSION_CHECK = 1
$env:PIP_NO_WARN_SCRIPT_LOCATION = 0
$env:PIP_TIMEOUT = 30
$env:PIP_RETRIES = 5
$env:PIP_PREFER_BINARY = 1
$env:PIP_YES = 1
$env:PYTHONUTF8 = 1
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = 1
$env:PYTHONNOUSERSITE = 1
$env:PYTHONFAULTHANDLER = 1
$env:PYTHONWARNINGS = "ignore:::torchvision.transforms.functional_tensor,ignore::UserWarning,ignore::FutureWarning,ignore::DeprecationWarning"
$env:GRADIO_ANALYTICS_ENABLED = "False"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = 1
$env:BITSANDBYTES_NOWELCOME = 1
$env:ClDeviceGlobalMemSizeAvailablePercent = 100
$env:CUDA_MODULE_LOADING = "LAZY"
$env:TORCH_CUDNN_V8_API_ENABLED = 1
$env:USE_LIBUV = 0
$env:SYCL_CACHE_PERSISTENT = 1
$env:TF_CPP_MIN_LOG_LEVEL = 3
$env:SAFETENSORS_FAST_GPU = 1
$env:CACHE_HOME = Join-NormalizedPath $PSScriptRoot "cache"
$env:HF_HOME = Join-NormalizedPath $PSScriptRoot "cache" "huggingface"
$env:MATPLOTLIBRC = Join-NormalizedPath $PSScriptRoot "cache"
$env:MODELSCOPE_CACHE = Join-NormalizedPath $PSScriptRoot "cache" "modelscope" "hub"
$env:MS_CACHE_HOME = Join-NormalizedPath $PSScriptRoot "cache" "modelscope" "hub"
$env:SYCL_CACHE_DIR = Join-NormalizedPath $PSScriptRoot "cache" "libsycl_cache"
$env:TORCH_HOME = Join-NormalizedPath $PSScriptRoot "cache" "torch"
$env:U2NET_HOME = Join-NormalizedPath $PSScriptRoot "cache" "u2net"
$env:XDG_CACHE_HOME = Join-NormalizedPath $PSScriptRoot "cache"
$env:PIP_CACHE_DIR = Join-NormalizedPath $PSScriptRoot "cache" "pip"
$env:PYTHONPYCACHEPREFIX = Join-NormalizedPath $PSScriptRoot "cache" "pycache"
$env:TORCHINDUCTOR_CACHE_DIR = Join-NormalizedPath $PSScriptRoot "cache" "torchinductor"
$env:TRITON_CACHE_DIR = Join-NormalizedPath $PSScriptRoot "cache" "triton"
$env:UV_CACHE_DIR = Join-NormalizedPath $PSScriptRoot "cache" "uv"
$env:COMFYUI_PATH = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX
$env:COMFYUI_INSTALLER_ROOT = $PSScriptRoot


# 提示符信息
function global:prompt {
    "$(Write-Host "[ComfyUI Env]" -ForegroundColor Green -NoNewLine) $(Get-Location)> "
}


# 安装绘世启动器
function global:Install-Hanamizuki {
    $urls = @(
        "https://www.modelscope.cn/models/licyks/sd-webui-all-in-one/resolve/master/hanamizuki/hanamizuki.exe",
        "https://huggingface.co/licyk/sd-webui-all-in-one/resolve/main/hanamizuki/hanamizuki.exe",
        "https://github.com/licyk/term-sd/releases/download/archive/hanamizuki.exe",
        "https://gitee.com/licyk/term-sd/releases/download/archive/hanamizuki.exe"
    )
    $i = 0

    if (!(Test-Path (Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX))) {
        Write-Log "内核路径 $((Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX)) 未找到, 无法安装绘世启动器, 请检查 ComfyUI 是否已正确安装, 或者尝试运行 ComfyUI Installer 进行修复" -Level ERROR
        return
    }

    if ((Get-CurrentPlatform) -ne "windows") {
        Write-Log "当前平台不支持安装绘世启动器" -Level WARNING
        return
    }

    New-Item -ItemType Directory -Path $env:CACHE_HOME -Force > $null

    if (Test-Path (Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX "hanamizuki.exe")) {
        Write-Log "绘世启动器已安装, 路径: $([System.IO.Path]::GetFullPath((Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX "hanamizuki.exe")))"
        Write-Log "可以进入该路径启动绘世启动器, 也可运行 hanamizuki.bat 启动绘世启动器"
    } else {
        foreach ($url in $urls) {
            Write-Log "下载绘世启动器中"
            try {
                $web_request_params = @{
                    Uri = $url
                    UseBasicParsing = $true
                    OutFile = (Join-NormalizedPath $env:CACHE_HOME "hanamizuki_tmp.exe")
                    TimeoutSec = 15
                    ErrorAction = "Stop"
                }
                Invoke-WebRequest @web_request_params
                Move-Item -Path (Join-NormalizedPath $env:CACHE_HOME "hanamizuki_tmp.exe") (Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX "hanamizuki.exe") -Force
                Write-Log "绘世启动器安装成功, 路径: $([System.IO.Path]::GetFullPath((Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX "hanamizuki.exe")))"
                Write-Log "可以进入该路径启动绘世启动器, 也可运行 hanamizuki.bat 启动绘世启动器"
                break
            }
            catch {
                $i += 1
                if ($i -lt $urls.Length) {
                    Write-Log "重试下载绘世启动器中" -Level WARNING
                } else {
                    Write-Log "下载绘世启动器失败" -Level ERRO
                    return
                }
            }
        }
    }

    $content = "
@echo off
echo Initialize configuration
setlocal enabledelayedexpansion
set CurrentPath=%~dp0
set DefaultCorePrefix=ComfyUI
if exist `"%~dp0%DefaultCorePrefix%`" (
    set CorePrefix=%DefaultCorePrefix%
) else (
    set CorePrefix=core
)
set CorePrefixFile=%~dp0core_prefix.txt

set ArgIndex=0
set NextIsValue=0
for %%i in (%*) do (
    set /a ArgIndex+=1
    if !NextIsValue!==1 (
        set CorePrefix=%%i
        set NextIsValue=0
        goto :convert
    ) else (
        if `"%%i`"==`"-CorePrefix`" (
            set NextIsValue=1
        )
    )
)

if exist `"%CorePrefixFile%`" (
    for /f `"delims=`" %%i in ('powershell -NoLogo -NoProfile -Command `"`$corePrefixText = Get-Content -Path '%CorePrefixFile%' -Raw; if (-not [string]::IsNullOrWhiteSpace(`$corePrefixText)) { `$corePrefixText.Trim() }`"') do (
        set CorePrefix=%%i
        goto :convert
    )
)

:convert
for /f `"delims=`" %%i in ('powershell -NoLogo -NoProfile -Command `"`$current_path = '%CurrentPath%'.Trim('/').Trim('\'); `$origin_core_prefix = '%CorePrefix%'.Trim('/').Trim('\'); if ([System.IO.Path]::IsPathRooted(`$origin_core_prefix)) { `$to_path = `$origin_core_prefix; `$from_uri = New-Object System.Uri(`$current_path.Replace('\', '/') + '/'); `$to_uri = New-Object System.Uri(`$to_path.Replace('\', '/')); `$origin_core_prefix = `$from_uri.MakeRelativeUri(`$to_uri).ToString().Trim('/') }; Write-Host `$origin_core_prefix`"') do (
    set CorePrefix=%%i
    goto :continue
)

:continue
set RootPath=%~dp0%CorePrefix%
echo CorePrefix: %CorePrefix%
echo RootPath: %RootPath%
if exist `"%RootPath%`" (
    cd /d `"%RootPath%`"
) else (
    echo %CorePrefix% not found
    echo Please check if ComfyUI is installed, or if the CorePrefix is set correctly
    pause
    exit 1
)
if exist .\hanamizuki.exe (
    echo Launch Hanamizuki
    start /B .\hanamizuki.exe
    cd /d `"%CurrentPath%`"
) else (
    echo Hanamizuki not found
    echo Try running terminal.ps1 to open the terminal and execute the Install-Hanamizuki command to install Hanamizuki
    cd /d `"%CurrentPath%`"
    pause
    exit 1
)
    ".Trim()

    Set-Content -Encoding Default -Path (Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT "hanamizuki.bat") -Value $content

    Move-PortableLauncherRuntime -InstallRoot $env:COMFYUI_INSTALLER_ROOT -CorePrefix $env:CORE_PREFIX -LauncherName "绘世启动器" | Out-Null
}


# 安装 Hanafubuki 启动器
function global:Install-Hanafubuki {
    $core_path = Join-NormalizedPath $env:COMFYUI_INSTALLER_ROOT $env:CORE_PREFIX
    if (!(Test-Path -LiteralPath $core_path -PathType Container)) {
        Write-Log "内核路径 $core_path 未找到, 无法安装 Hanafubuki 启动器, 请检查 ComfyUI 是否已正确安装, 或者尝试运行 ComfyUI Installer 进行修复" -Level ERROR
        return
    }

    $installed = Install-HanafubukiPackage -InstallRoot $env:COMFYUI_INSTALLER_ROOT -CachePath $env:CACHE_HOME
    if ($installed) {
        Move-PortableLauncherRuntime -InstallRoot $env:COMFYUI_INSTALLER_ROOT -CorePrefix $env:CORE_PREFIX -LauncherName "Hanafubuki 启动器" | Out-Null
    }
}


function global:pip {
    python -m pip @args
}

function global:sd-webui-all-in-one {
    & python -m sd_webui_all_in_one @args
}

Set-Alias pip3 pip
Set-Alias python3 python


# 列出 ComfyUI Installer 内置命令
function global:List-CMD {
    Write-Host "
==================================
ComfyUI Installer created by licyk
哔哩哔哩：https://space.bilibili.com/46497516
Github：https://github.com/licyk
==================================

当前可用的 ComfyUI Installer 内置命令：

    Install-Hanamizuki
    Install-Hanafubuki
    List-CMD

更多帮助信息可在 ComfyUI Installer 文档中查看: https://licyk.github.io/sd-webui-all-in-one/installer/comfyui/
"
}


# PyPI 软件包镜像源状态
function Get-PyPIMirrorStatus {
    if ($USE_PIP_MIRROR) {
        Write-Log "使用 PyPI 软件包镜像源"
    } else {
        Write-Log "检测到 disable_pypi_mirror.txt 配置文件 / -DisablePyPIMirror, 命令行参数 已将 PyPI 源切换至官方源"
    }
}


# Hugging Face 镜像源
function Set-HuggingFaceMirror {
    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_hf_mirror.txt")) -or ($script:DisableHuggingFaceMirror)) { # 检测是否禁用了自动设置 Hugging Face 镜像源
        Write-Log "检测到本地存在 disable_hf_mirror.txt 镜像源配置文件 / -DisableHuggingFaceMirror 命令行参数, 禁用自动设置 Hugging Face 镜像源"
        return
    }

    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "hf_mirror.txt")) -or ($script:UseCustomHuggingFaceMirror)) { # 本地存在 Hugging Face 镜像源配置
        if ($script:UseCustomHuggingFaceMirror) {
            $hf_mirror_value = $script:UseCustomHuggingFaceMirror
        } else {
            $hf_mirror_value = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "hf_mirror.txt") -Encoding UTF8
        }
        if (-not [string]::IsNullOrWhiteSpace($hf_mirror_value)) {
            $env:HF_ENDPOINT = $hf_mirror_value
            Write-Log "检测到本地存在 hf_mirror.txt 配置文件 / -UseCustomHuggingFaceMirror 命令行参数, 已读取该配置并设置 Hugging Face 镜像源"
        } else {
            $env:HF_ENDPOINT = "https://hf-mirror.com"
            Write-Log "使用默认 Hugging Face 镜像源"
        }
    } else { # 使用默认设置
        $env:HF_ENDPOINT = "https://hf-mirror.com"
        Write-Log "使用默认 Hugging Face 镜像源"
    }
}


# GitHub 镜像源
function Set-GithubMirrorLegecy {
    $env:GIT_CONFIG_GLOBAL = (Join-NormalizedPath $PSScriptRoot ".gitconfig") # 设置 Git 配置文件路径
    if (Test-Path (Join-NormalizedPath $PSScriptRoot ".gitconfig")) {
        Remove-Item -Path (Join-NormalizedPath $PSScriptRoot ".gitconfig") -Force -Recurse
    }
    # 默认 Git 配置
    git config --global --add safe.directory '*'
    git config --global core.longpaths true

    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_gh_mirror.txt")) -or ($script:DisableGithubMirror)) { # 禁用 GitHub 镜像源
        Write-Log "检测到本地存在 disable_gh_mirror.txt GitHub 镜像源配置文件 / -DisableGithubMirror 命令行参数, 禁用 GitHub 镜像源"
        return
    }

    # 使用自定义 GitHub 镜像源
    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "gh_mirror.txt")) -or ($script:UseCustomGithubMirror)) {
        if ($script:UseCustomGithubMirror) {
            $github_mirror = $script:UseCustomGithubMirror
        } else {
            $github_mirror = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "gh_mirror.txt") -Encoding UTF8
        }
        if (-not [string]::IsNullOrWhiteSpace($github_mirror)) {
            git config --global url."$github_mirror".insteadOf "https://github.com"
            Write-Log "检测到本地存在 gh_mirror.txt GitHub 镜像源配置文件 / -UseCustomGithubMirror 命令行参数, 已读取 GitHub 镜像源配置文件并设置 GitHub 镜像源"
        }
    }
}


function Main {
    Get-HelpMessage
    Get-Version
    Set-CorePrefix
    Initialize-EnvPath
    Test-PythonAndGit
    Set-Proxy -Legacy
    Set-HuggingFaceMirror
    Set-GithubMirrorLegecy
    Get-PyPIMirrorStatus

    if (Get-Command Set-PSReadLineKeyHandler -ErrorAction SilentlyContinue) {
        Set-PSReadLineKeyHandler -Chord Ctrl+d -ScriptBlock {
            [Microsoft.PowerShell.PSConsoleReadLine]::RevertLine()
            [Microsoft.PowerShell.PSConsoleReadLine]::Insert("exit")
            [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
        }
    }

    $python_cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($python_cmd) {
        $python_path_prefix = Join-NormalizedPath $PSScriptRoot "python"
        $python_extra_path_prefix = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "python"
        $python_cmd = Get-NormalizedFilePath $python_cmd.Path
        if (($python_cmd) -and (($python_cmd.ToString().StartsWith($python_path_prefix, [System.StringComparison]::OrdinalIgnoreCase)) -or ($python_cmd.ToString().StartsWith($python_extra_path_prefix, [System.StringComparison]::OrdinalIgnoreCase)))) {
            $env:UV_PYTHON = $python_cmd
            $env:PIP_PYTHON = $python_cmd
        }
    }

    Write-Log "激活 ComfyUI Env"
    Write-Log "更多帮助信息可在 ComfyUI Installer 项目地址查看: https://licyk.github.io/sd-webui-all-in-one/installer/comfyui/"
}

###################

Main