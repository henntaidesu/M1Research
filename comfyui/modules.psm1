param (
    [string]$OriginalScriptPath,
    [string]$LaunchCommandLine,
    [switch]$Help,
    [string]$CorePrefix,
    [switch]$DisableUpdate,
    [switch]$BuildMode,
    [switch]$DisableProxy,
    [string]$UseCustomProxy,
    [switch]$DisablePyPIMirror,
    [switch]$DisableAutoMirror,
    [switch]$DisableHuggingFaceMirror,
    [string]$UseCustomHuggingFaceMirror,
    [switch]$DisableGithubMirror,
    [string]$UseCustomGithubMirror,
    [switch]$DisableUV,
    [switch]$DisableCUDAMalloc,
    [switch]$DisableModelMirror,
    [switch]$DisableHotpatcher,
    [switch]$DisableSnapshot,
    [int]$HotpatcherPort,
    [switch]$HotpatcherPortSpecified,
    [switch]$EnableHotpatcherRuntime,
    [string]$LaunchArg,
    [switch]$EnableShortcut,
    [switch]$DisableEnvCheck,
    [switch]$NoPause
)
# ComfyUI Installer 版本和检查更新间隔
$script:COMFYUI_INSTALLER_VERSION = 558
$script:UPDATE_TIME_SPAN = 3600
# SD WebUI All In One 内核最低版本
$script:CORE_MINIMUM_VER = "2.6.23"

function Convert-WebResponseContentToText {

    [CmdletBinding()]
    param ([Parameter(Mandatory = $true)]$Content)

    if ($Content -is [byte[]]) {
        return [System.Text.Encoding]::UTF8.GetString($Content)
    }
    return [string]$Content

}

function Get-HanafubukiPlatformPackage {

    [CmdletBinding()]
    param ([Parameter(Mandatory = $true)][string]$InstallRoot)

    $platform = Get-CurrentPlatform
    $architecture = Get-CurrentArchitecture
    $manifest_architecture = switch ($architecture) {
        "amd64" { "x86_64" }
        "aarch64" { "aarch64" }
        default { $architecture }
    }

    switch ($platform) {
        "windows" {
            $entry_name = "hanafubuki-launcher.exe"
            $archive_type = "zip"
        }
        "linux" {
            $entry_name = "hanafubuki-launcher"
            $archive_type = "tar.gz"
        }
        "macos" {
            $entry_name = "Hanafubuki Launcher.app"
            $archive_type = "tar.gz"
        }
        default {
            throw "Hanafubuki 不支持当前平台: $platform"
        }
    }

    if ($manifest_architecture -notin @("x86_64", "aarch64")) {
        throw "Hanafubuki 不支持当前架构: $architecture"
    }
    if (($platform -eq "windows") -and ($manifest_architecture -ne "x86_64")) {
        throw "Hanafubuki 暂不支持 Windows $architecture"
    }

    return [PSCustomObject]@{
        Platform = $platform
        Architecture = $manifest_architecture
        ArchiveType = $archive_type
        EntryName = $entry_name
        DestinationPath = Join-NormalizedPath $InstallRoot $entry_name
    }

}

function Expand-HanafubukiPackageArchive {

    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [Parameter(Mandatory = $true)][ValidateSet("zip", "tar.gz")][string]$ArchiveType
    )

    New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
    if ($ArchiveType -eq "zip") {
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $DestinationPath -Force
        return
    }

    $tar_command = Get-Command tar -ErrorAction SilentlyContinue
    if (-not $tar_command) {
        throw "未找到 tar 命令, 无法解压 Hanafubuki 安装包"
    }
    $tar_output = & $tar_command.Source "-xzf" $ArchivePath "-C" $DestinationPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "解压 Hanafubuki 安装包失败: $($tar_output -join [Environment]::NewLine)"
    }

}

function Test-HanafubukiInstallation {

    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet("windows", "linux", "macos")][string]$Platform
    )

    if ($Platform -eq "macos") {
        if (!(Test-Path -LiteralPath $Path -PathType Container)) {
            return $false
        }
        $macos_path = Join-NormalizedPath $Path "Contents" "MacOS"
        $executables = @(Get-ChildItem -LiteralPath $macos_path -File -ErrorAction SilentlyContinue)
        return ($executables.Count -gt 0) -and (($executables | Where-Object Length -gt 0).Count -eq $executables.Count)
    }

    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    return (Get-Item -LiteralPath $Path).Length -gt 0

}

function Install-HanafubukiPackage {

    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$CachePath
    )

    $manifest_urls = @(
        "https://huggingface.co/licyk/sd-webui-all-in-one/resolve/main/hanafubuki-launcher/latest.json",
        "https://modelscope.cn/models/licyks/sd-webui-all-in-one/resolve/master/hanafubuki-launcher/latest.json"
    )

    try {
        $package = Get-HanafubukiPlatformPackage -InstallRoot $InstallRoot
    }
    catch {
        Write-Log $_.Exception.Message -Level ERROR
        return $false
    }

    if (Test-HanafubukiInstallation -Path $package.DestinationPath -Platform $package.Platform) {
        Write-Log "Hanafubuki 启动器已安装, 路径: $([System.IO.Path]::GetFullPath($package.DestinationPath))"
        return $true
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $CachePath -Force | Out-Null

    foreach ($manifest_url in $manifest_urls) {
        $random_string = [Guid]::NewGuid().ToString("N")
        $archive_path = Join-NormalizedPath $CachePath "hanafubuki_$random_string.$($package.ArchiveType)"
        $staging_path = Join-NormalizedPath $CachePath "hanafubuki_$($random_string)_tmp"
        $backup_path = Join-NormalizedPath $CachePath "hanafubuki_$($random_string)_backup"
        $backup_created = $false
        $install_succeeded = $false

        try {
            Write-Log "从 $manifest_url 获取 Hanafubuki 最新版本信息"
            $manifest_request_params = @{
                Uri = $manifest_url
                UseBasicParsing = $true
                TimeoutSec = 15
                ErrorAction = "Stop"
            }
            $manifest_response = Invoke-WebRequest @manifest_request_params
            $manifest_content = Convert-WebResponseContentToText -Content $manifest_response.Content
            $manifest = $manifest_content | ConvertFrom-Json -ErrorAction Stop
            if ([string]::IsNullOrWhiteSpace([string]$manifest.version)) {
                throw "Hanafubuki 版本清单缺少 version"
            }

            $platform_property = $manifest.platforms.PSObject.Properties[$package.Platform]
            if (-not $platform_property) {
                throw "Hanafubuki $($manifest.version) 不支持平台 $($package.Platform)"
            }
            $asset_property = $platform_property.Value.PSObject.Properties[$package.Architecture]
            if (-not $asset_property) {
                throw "Hanafubuki $($manifest.version) 不支持架构 $($package.Architecture)"
            }
            $download_url = [string]$asset_property.Value.url
            [System.Uri]$parsed_download_url = $null
            if ([string]::IsNullOrWhiteSpace($download_url) -or
                !([System.Uri]::TryCreate($download_url, [System.UriKind]::Absolute, [ref]$parsed_download_url)) -or
                ($parsed_download_url.Scheme -notin @("http", "https"))) {
                throw "Hanafubuki 版本清单中的下载链接无效"
            }

            Write-Log "下载 Hanafubuki $($manifest.version) ($($package.Platform)/$($package.Architecture))"
            $download_request_params = @{
                Uri = $download_url
                UseBasicParsing = $true
                OutFile = $archive_path
                TimeoutSec = 120
                ErrorAction = "Stop"
            }
            Invoke-WebRequest @download_request_params
            if (!(Test-Path -LiteralPath $archive_path -PathType Leaf) -or ((Get-Item -LiteralPath $archive_path).Length -le 0)) {
                throw "下载的 Hanafubuki 安装包为空"
            }

            Expand-HanafubukiPackageArchive -ArchivePath $archive_path -DestinationPath $staging_path -ArchiveType $package.ArchiveType
            $staged_entry = Join-NormalizedPath $staging_path $package.EntryName
            if (!(Test-HanafubukiInstallation -Path $staged_entry -Platform $package.Platform)) {
                throw "Hanafubuki 安装包结构不符合预期, 缺少 $($package.EntryName)"
            }

            if (Test-Path -LiteralPath $package.DestinationPath) {
                Move-Item -LiteralPath $package.DestinationPath -Destination $backup_path -Force
                $backup_created = $true
            }

            try {
                Move-Item -LiteralPath $staged_entry -Destination $package.DestinationPath -Force
                if ($package.Platform -eq "linux") {
                    & chmod "+x" "$($package.DestinationPath)"
                    if ($LASTEXITCODE -ne 0) {
                        throw "无法为 Hanafubuki 启动器设置可执行权限"
                    }
                }
                elseif ($package.Platform -eq "macos") {
                    $macos_path = Join-NormalizedPath $package.DestinationPath "Contents" "MacOS"
                    foreach ($executable in @(Get-ChildItem -LiteralPath $macos_path -File -ErrorAction Stop)) {
                        & chmod "+x" "$($executable.FullName)"
                        if ($LASTEXITCODE -ne 0) {
                            throw "无法为 Hanafubuki 启动器设置可执行权限"
                        }
                    }
                }

                if (!(Test-HanafubukiInstallation -Path $package.DestinationPath -Platform $package.Platform)) {
                    throw "Hanafubuki 安装结果验证失败"
                }
                $install_succeeded = $true
            }
            catch {
                if (Test-Path -LiteralPath $package.DestinationPath) {
                    Remove-Item -LiteralPath $package.DestinationPath -Force -Recurse -ErrorAction SilentlyContinue
                }
                if ($backup_created -and (Test-Path -LiteralPath $backup_path)) {
                    Move-Item -LiteralPath $backup_path -Destination $package.DestinationPath -Force
                    $backup_created = $false
                }
                throw
            }

            if ($backup_created -and (Test-Path -LiteralPath $backup_path)) {
                Remove-Item -LiteralPath $backup_path -Force -Recurse
                $backup_created = $false
            }
            Write-Log "Hanafubuki $($manifest.version) 安装成功, 路径: $([System.IO.Path]::GetFullPath($package.DestinationPath))"
            return $true
        }
        catch {
            Write-Log "通过 $manifest_url 安装 Hanafubuki 失败: $($_.Exception.Message)" -Level WARNING
        }
        finally {
            Remove-Item -LiteralPath $archive_path -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $staging_path -Force -Recurse -ErrorAction SilentlyContinue
            if ($install_succeeded -and (Test-Path -LiteralPath $backup_path)) {
                Remove-Item -LiteralPath $backup_path -Force -Recurse -ErrorAction SilentlyContinue
            }
        }
    }

    Write-Log "所有下载源均无法安装 Hanafubuki 启动器" -Level ERROR
    return $false

}

function Move-PortableLauncherRuntime {

    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$CorePrefix,
        [Parameter(Mandatory = $true)][string]$LauncherName
    )

    $core_path = Join-NormalizedPath $InstallRoot $CorePrefix
    if (!(Test-Path -LiteralPath $core_path -PathType Container)) {
        Write-Log "内核路径 $core_path 未找到, 无法为 $LauncherName 配置 Python / Git 环境" -Level ERROR
        return $false
    }

    Write-Log "检查 $LauncherName 运行环境"
    $runtime_ready = $true
    $runtime_names = @("python", "git")
    foreach ($runtime_name in $runtime_names) {
        $display_name = if ($runtime_name -eq "python") { "Python" } else { "Git" }
        $source_path = Join-NormalizedPath $InstallRoot $runtime_name
        $destination_path = Join-NormalizedPath $core_path $runtime_name

        if (Test-Path -LiteralPath $destination_path -PathType Container) {
            if (Test-Path -LiteralPath $source_path -PathType Container) {
                Write-Log "$display_name 的根目录和内核目录副本同时存在, 为避免覆盖已跳过移动: $source_path" -Level WARNING
            }
            continue
        }

        if (!(Test-Path -LiteralPath $source_path -PathType Container)) {
            if (($runtime_name -eq "python") -or ((Get-CurrentPlatform) -eq "windows")) {
                Write-Log "环境缺少 $display_name, 无法为 $LauncherName 准备 $display_name 环境, 请重新运行 ComfyUI Installer 修复环境" -Level WARNING
                $runtime_ready = $false
            }
            continue
        }

        try {
            Write-Log "尝试将 $display_name 移动至 $core_path 中"
            Move-Item -LiteralPath $source_path -Destination $core_path -Force -ErrorAction Stop
            Write-Log "$display_name 路径移动成功"
        }
        catch {
            Write-Log "$display_name 路径移动失败, 这将导致 $LauncherName 无法正确识别到 $display_name 环境: $($_.Exception.Message)" -Level ERROR
            Write-Log "请关闭所有占用 $display_name 的进程, 并重新运行安装命令"
            $runtime_ready = $false
        }
    }

    Write-Log "检查 $LauncherName 运行环境结束"
    return $runtime_ready

}

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

function Write-CoreCliFailureCommand {

    param (
        [Parameter(Mandatory = $true)][object]$CommandPrefix,
        [object]$Arguments,
        [Parameter(Mandatory = $true)][int]$ExitCode
    )
    Write-Log "SD WebUI All In One CLI 执行失败, 退出码: $ExitCode" -Level ERROR
    $command_line = Format-CoreCliCommandForLog -CommandPrefix $CommandPrefix -Arguments $Arguments
    Write-Log "失败命令: $command_line" -Level ERROR

}

function Format-CommandLineArgumentForLog {

    param ([AllowNull()][object]$Argument)
    if ($null -eq $Argument) {
        return "''"
    }
    $value = [string]$Argument
    if ($value.Length -eq 0) {
        return "''"
    }
    if ($value -match '[\s''"]') {
        return ([string][char]39) + ($value -replace ([string][char]39), ([string][char]39 + [string][char]39)) + ([string][char]39)
    }
    return $value

}

function Format-CoreCliCommandForLog {

    param (
        [Parameter(Mandatory = $true)][object]$CommandPrefix,
        [object]$Arguments
    )
    $parts = New-Object System.Collections.ArrayList
    foreach ($item in $CommandPrefix) {
        $parts.Add((Format-CommandLineArgumentForLog $item)) | Out-Null
    }
    if ($null -ne $Arguments) {
        foreach ($item in $Arguments) {
            $parts.Add((Format-CommandLineArgumentForLog $item)) | Out-Null
        }
    }
    return ($parts -join ' ')

}

function Get-NativeCommandExitCode {

    param ([bool]$Success)
    if ($Success) {
        return 0
    }
    if (($null -ne $global:LASTEXITCODE) -and ($global:LASTEXITCODE -ne 0)) {
        return [int]$global:LASTEXITCODE
    }
    return 1

}

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

function Get-NormalizedFilePath {

    [CmdletBinding()]
    param ([Parameter(Mandatory = $false)][string]$Filepath)
    if (-not [string]::IsNullOrWhiteSpace($Filepath)) { return Join-NormalizedPath $Filepath }
    return $null

}

function Get-CurrentPlatform {

    if ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
        return "windows"
    }
    elseif ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Linux)) {
        return "linux"
    }
    elseif ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::OSX)) {
        return "macos"
    }
    else {
        return "unknown"
    }

}

function Get-CurrentArchitecture {

    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLower()
    }
    else {
        if ([Environment]::Is64BitOperatingSystem) {
            if ($env:PROCESSOR_ARCHITEW6432) {
                $arch = $env:PROCESSOR_ARCHITEW6432.ToLower()
            }
            else {
                $arch = $env:PROCESSOR_ARCHITECTURE.ToLower()
            }
        }
        else {
            $arch = "x86"
        }
    }
    switch ($arch) {
        "amd64"  { return "amd64" }
        "x64"    { return "amd64" }
        "arm64"  { return "aarch64" }
        "x86_64" { return "amd64" }
        "x86"    { return "x86" }
        "i386"   { return "x86" }
        "i686"   { return "x86" }
        default  { return $arch }
    }

}

function Get-Version {

    $ver = $([string]$script:COMFYUI_INSTALLER_VERSION).ToCharArray()
    $major = ($ver[0..($ver.Length - 3)])
    $minor = $ver[-2]
    $micro = $ver[-1]
    Write-Log "ComfyUI Installer 版本: v${major}.${minor}.${micro}"

}

function Update-SDWebUiAllInOne {

    $content = "
import re
from importlib.metadata import version


def compare_versions(version1: str, version2: str) -> int:
    try:
        nums1 = re.sub(r'[a-zA-Z]+', '', version1).replace('-', '.').replace('+', '.').split('.')
        nums2 = re.sub(r'[a-zA-Z]+', '', version2).replace('-', '.').replace('+', '.').split('.')
    except Exception:
        return 0
    for i in range(max(len(nums1), len(nums2))):
        num1 = int(nums1[i]) if i < len(nums1) else 0
        num2 = int(nums2[i]) if i < len(nums2) else 0
        if num1 == num2:
            continue
        elif num1 > num2:
            return 1
        else:
            return -1
    return 0


def is_core_need_update(core_minimum_ver: str) -> bool:
    try:
        core_ver = version('sd-webui-all-in-one')
    except Exception:
        return True
    return compare_versions(core_ver, core_minimum_ver) < 0


if __name__ == '__main__':
    print(is_core_need_update('$script:CORE_MINIMUM_VER'))
".Trim()

    $pip_index_url = "https://pypi.python.org/simple"
    if ((!($script:DisablePyPIMirror)) -and (!(Test-Path (Join-NormalizedPath $PSScriptRoot "disable_pypi_mirror.txt")))) {
        $pip_index_url = "https://mirrors.cloud.tencent.com/pypi/simple"
    }
    Write-Log "检测 SD WebUI All In One 内核是否需要更新"
    $status = $(python -c "$content")
    if ($status -ne "True") {
        Write-Log "SD WebUI All In One 内核无需更新"
        return
    }
    Write-Log "更新 SD WebUI All In One 内核中"
    & python -m pip install -U "sd-webui-all-in-one>=$script:CORE_MINIMUM_VER" --index-url $pip_index_url
    if (!($?)) { & python -m pip install -U "sd-webui-all-in-one>=$script:CORE_MINIMUM_VER" }
    if (!($?)) {
        Write-Log "SD WebUI All In One 内核更新失败, Installer 部分功能将无法使用" -Level ERROR
        if ((-not $script:BuildMode) -and (-not $script:NoPause)) { Read-Host | Out-Null }
        exit 1
    }
    Write-Log "SD WebUI All In One 内核更新成功"

}

function Set-AutoMirror {

    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)

    if (($script:DisableAutoMirror) -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_auto_mirror.txt"))) {
        if (-not $ArrayList.Contains("--no-auto-mirror")) {
            $ArrayList.Add("--no-auto-mirror") | Out-Null
        }
        if (-not $script:AutoMirrorStatusLogged) {
            Write-Log "检测到 disable_auto_mirror.txt 配置文件 / -DisableAutoMirror 命令行参数, 已禁用 CLI 自动选择下载镜像源, 将遵守手动镜像源设置"
            $script:AutoMirrorStatusLogged = $true
        }
        return $true
    }

    if (-not $script:AutoMirrorStatusLogged) {
        Write-Log "CLI 自动选择下载镜像源已启用, 将由 Python CLI 根据网络检测结果强制覆盖镜像源相关参数"
        $script:AutoMirrorStatusLogged = $true
    }
    return $false

}

function Set-uv {

    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if (($script:DisableUV) -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_uv.txt"))) {
        Write-Log "检测到 disable_uv.txt 配置文件 / -DisableUV 命令行参数, 已禁用 uv, 使用 Pip 作为 Python 包管理器"
        $ArrayList.Add("--no-uv") | Out-Null
    } else {
        Write-Log "默认启用 uv 作为 Python 包管理器, 加快 Python 软件包的安装速度"
        Write-Log "当 uv 安装 Python 软件包失败时, 将自动切换成 Pip 重试 Python 软件包的安装"
    }

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
        $target_prefix = "core"
    }
    return $target_prefix

}

function Set-PyPIMirror {

    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if (!(Set-AutoMirror $ArrayList)) { return }
    if ((!(Test-Path (Join-NormalizedPath $PSScriptRoot "disable_pypi_mirror.txt"))) -and (!($script:DisablePyPIMirror))) {
        Write-Log "使用 PyPI 软件包镜像源"
    } else {
        Write-Log "检测到 disable_pypi_mirror.txt 配置文件 / -DisablePyPIMirror 命令行参数, 已将 PyPI 源切换至官方源"
        $ArrayList.Add("--no-pypi-mirror") | Out-Null
    }

}

function Write-FileWithStreamWriter {

    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $false)][ValidateSet("GBK", "UTF8", "UTF8BOM")][string]$Encoding = "UTF8"
    )
    process {
        try {
            $encode = $null
            switch ($Encoding) {
                "GBK" {
                    if ($PSVersionTable.PSVersion.Major -ge 6) {
                        [System.Text.Encoding]::RegisterProvider([System.Text.CodePagesEncodingProvider]::Instance)
                    }
                    $encode = [System.Text.Encoding]::GetEncoding("GBK")
                }
                "UTF8" {
                    $encode = New-Object System.Text.UTF8Encoding($false)
                }
                "UTF8BOM" {
                    $encode = New-Object System.Text.UTF8Encoding($true)
                }
            }
            $absolute_path = [System.IO.Path]::GetFullPath($Path)
            $writer = New-Object System.IO.StreamWriter($absolute_path, $false, $encode)
            try {
                $writer.Write($Value)
            }
            finally {
                if ($null -ne $writer) {
                    $writer.Close()
                    $writer.Dispose()
                }
            }
        }
        catch {
            Write-Log "写入文件时发生错误: $($_.Exception.Message)" -Level ERROR
        }
    }

}


# 初始化环境变量
function Initialize-EnvPath {
    Write-Log "初始化环境变量"
    $python_path = Join-NormalizedPath $PSScriptRoot "python"
    $python_extra_path = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "python"
    $python_scripts_path = Join-NormalizedPath $PSScriptRoot "python" "Scripts"
    $python_scripts_extra_path = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "python" "Scripts"
    $python_bin_path = Join-NormalizedPath $PSScriptRoot "python" "bin"
    $python_bin_extra_path = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "python" "bin"
    $git_path = Join-NormalizedPath $PSScriptRoot "git" "bin"
    $git_extra_path = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "git" "bin"
    $sep = $([System.IO.Path]::PathSeparator)
    $env:PATH = "${python_bin_extra_path}${sep}${python_extra_path}${sep}${python_scripts_extra_path}${sep}${git_extra_path}${sep}${python_bin_path}${sep}${python_path}${sep}${python_scripts_path}${sep}${git_path}${sep}${env:PATH}"

    $env:UV_CONFIG_FILE = Join-NormalizedPath $PSScriptRoot "cache" "uv.toml"
    $env:PIP_CONFIG_FILE = Join-NormalizedPath $PSScriptRoot "cache" "pip.ini"
    $env:PIP_DISABLE_PIP_VERSION_CHECK = 1
    $env:PIP_NO_WARN_SCRIPT_LOCATION = 0
    $env:UV_LINK_MODE = "copy"
    $env:PYTHONUTF8 = 1
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUNBUFFERED = 1
    $env:PYTHONNOUSERSITE = 1
    $env:PYTHONFAULTHANDLER = 1
    $env:CACHE_HOME = Join-NormalizedPath $PSScriptRoot "cache"
    $env:COMFYUI_PATH = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX
    $env:COMFYUI_ROOT = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX
    $env:SD_WEBUI_ALL_IN_ONE_LAUNCH_PATH = $PSScriptRoot
    $env:SD_WEBUI_ALL_IN_ONE_LOGGER_NAME = "ComfyUI Installer"
    $env:SD_WEBUI_ALL_IN_ONE_LOGGER_LEVEL = 20
    $env:SD_WEBUI_ALL_IN_ONE_LOGGER_COLOR = 1
    $env:SD_WEBUI_ALL_IN_ONE_RETRY_TIMES = 3
    $env:SD_WEBUI_ALL_IN_ONE_EXTRA_PYPI_MIRROR = 0
    $env:SD_WEBUI_ALL_IN_ONE_SET_CACHE_PATH = 1
    $env:SD_WEBUI_ALL_IN_ONE_SET_CONFIG = 1
    $env:SD_WEBUI_ALL_IN_ONE_RAISE_WEBUI_RUNTIME_ERROR = 0
    $env:SD_WEBUI_ALL_IN_ONE_RAISE_CHECK_ENV_ERROR_ON_LAUNCH = 0

    New-Item -ItemType Directory -Path $env:CACHE_HOME -Force > $null
    Write-FileWithStreamWriter -Path (Join-NormalizedPath $env:CACHE_HOME "uv.toml") -Value "" -Encoding UTF8
    Write-FileWithStreamWriter -Path (Join-NormalizedPath $env:CACHE_HOME "pip.ini") -Value "" -Encoding UTF8
}


# 按退出码退出管理脚本
function Exit-ManagerScript {
    param ([int]$ExitCode)
    if ((-not $script:BuildMode) -and (-not $script:NoPause)) { Read-Host | Out-Null }
    exit $ExitCode
}


# ComfyUI Installer 更新检测
function Update-Installer {
    [CmdletBinding()]
    param([switch]$DisableRestart)
    $urls = @(
        "https://github.com/licyk/sd-webui-all-in-one/raw/main/installer/comfyui_installer.ps1",
        "https://gitee.com/licyk/sd-webui-all-in-one/raw/main/installer/comfyui_installer.ps1",
        "https://github.com/licyk/sd-webui-all-in-one/releases/download/comfyui_installer/comfyui_installer.ps1",
        "https://gitee.com/licyk/sd-webui-all-in-one/releases/download/comfyui_installer/comfyui_installer.ps1",
        "https://gitlab.com/licyk/sd-webui-all-in-one/-/raw/main/installer/comfyui_installer.ps1"
    )
    $i = 0

    New-Item -ItemType Directory -Path $env:CACHE_HOME -Force | Out-Null

    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_update.txt")) -or ($script:DisableUpdate)) {
        Write-Log "检测到 disable_update.txt 更新配置文件 / -DisableUpdate 命令行参数, 已禁用 ComfyUI Installer 的自动检查 Installer 更新功能"
        return
    }

    if ($script:BuildMode) {
        Write-Log "ComfyUI Installer 构建模式已启用, 跳过 ComfyUI Installer 更新检查"
        return
    }

    # 获取更新时间间隔
    try {
        $last_update_time_text = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "update_time.txt") -Encoding UTF8 2> $null
        if ([string]::IsNullOrWhiteSpace($last_update_time_text)) { throw "Missing update time" }
        $last_update_time = Get-Date $last_update_time_text -Format "yyyy-MM-dd HH:mm:ss"
    }
    catch {
        $last_update_time = Get-Date 0 -Format "yyyy-MM-dd HH:mm:ss"
    }
    finally {
        $update_time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $time_span = New-TimeSpan -Start $last_update_time -End $update_time
    }

    if ($time_span.TotalSeconds -gt $script:UPDATE_TIME_SPAN) {
        Set-Content -Encoding UTF8 -Path (Join-NormalizedPath $PSScriptRoot "update_time.txt") -Value $(Get-Date -Format "yyyy-MM-dd HH:mm:ss") # 记录更新时间
    } else {
        return
    }

    foreach ($url in $urls) {
        Write-Log "检查 ComfyUI Installer 更新中"
        try {
            $web_request_params = @{
                Uri = $url
                UseBasicParsing = $true
                OutFile = (Join-NormalizedPath $env:CACHE_HOME "comfyui_installer.ps1")
                TimeoutSec = 15
                ErrorAction = "Stop"
            }
            Invoke-WebRequest @web_request_params
            $latest_version = [int]$(
                Get-Content (Join-NormalizedPath $env:CACHE_HOME "comfyui_installer.ps1") -Encoding UTF8 |
                Select-String -Pattern "COMFYUI_INSTALLER_VERSION" |
                ForEach-Object { $_.ToString() }
            )[0].Split("=")[1].Trim()
            break
        }
        catch {
            $i += 1
            if ($i -lt $urls.Length) {
                Write-Log "重试检查 ComfyUI Installer 更新中" -Level WARNING
            } else {
                Write-Log "检查 ComfyUI Installer 更新失败" -Level ERROR
                return
            }
        }
    }

    if ($latest_version -le $script:COMFYUI_INSTALLER_VERSION) {
        Write-Log "ComfyUI Installer 已是最新版本"
        return
    }

    Write-Log "调用 ComfyUI Installer 进行更新中"
    & (Join-NormalizedPath $env:CACHE_HOME "comfyui_installer.ps1") -InstallPath $PSScriptRoot -UseUpdateMode

    if ($DisableRestart) {
        Write-Log "更新结束, 已禁用自动重新启动"
        return
    }

    $raw_params = $script:LaunchCommandLine -replace '^.*?\.dll\s*["'']?\s*', '' -replace '^.*\.ps1\s*["'']?\s*', ''
    Write-Log "更新结束, 重新启动 ComfyUI Installer 管理脚本中, 使用的命令行参数: $raw_params"
    try { Invoke-Expression "& `"$script:OriginalScriptPath`" $raw_params" -ErrorAction Stop; exit $LASTEXITCODE }
    catch { exit 1 }
}



# 检测 Windows 长路径支持是否启用
function Test-WindowsLongPathsEnabled {
    if ((Get-CurrentPlatform) -ne "windows") {
        return $true
    }

    try {
        $reg = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction Stop
        $property = $reg.PSObject.Properties["LongPathsEnabled"]
        if ($null -eq $property) {
            Write-Log "Windows 长路径支持注册表值缺失" -Level WARNING
            return $false
        }
        $enabled = ([int]$property.Value -eq 1)
        if ($enabled) {
            Write-Log "Windows 长路径支持已启用"
        } else {
            Write-Log "Windows 长路径支持未启用" -Level WARNING
        }
        return $enabled
    } catch {
        Write-Log "读取 Windows 长路径支持状态失败: $($_.Exception.Message)" -Level WARNING
        return $false
    }
}


# 获取当前 PowerShell 可执行文件
function Get-CurrentPowerShellExecutable {
    try {
        $process = Get-Process -Id $PID -ErrorAction Stop
        $path = [string]$process.Path
        if ((-not [string]::IsNullOrWhiteSpace($path)) -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            return $path
        }
    } catch {
        Write-Log "当前 PowerShell 可执行文件查找失败: $($_.Exception.Message)" -Level WARNING
    }

    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwsh) {
        return $pwsh.Source
    }
    $powershell = Get-Command powershell -ErrorAction SilentlyContinue
    if ($powershell) {
        return $powershell.Source
    }
    return $null
}


function Quote-ProcessArgument {
    param ([AllowNull()][string]$Argument)
    if ($null -eq $Argument) {
        return ([string][char]34 + [string][char]34)
    }
    if ($Argument.Length -eq 0) {
        return ([string][char]34 + [string][char]34)
    }
    $quote = [string][char]34
    $needs_quote = ($Argument -match '\s') -or $Argument.Contains($quote)
    if (-not $needs_quote) {
        return $Argument
    }
    $escaped = $Argument -replace $quote, ([string][char]96 + $quote)
    return ($quote + $escaped + $quote)
}


function Join-ProcessArguments {
    param ([AllowNull()][string[]]$Arguments)
    if ($null -eq $Arguments) {
        return [string]::Empty
    }
    return (($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join ' ')
}


# 以管理员权限启用 Windows 长路径支持
function Enable-WindowsLongPathsElevated {
    $id = [guid]::NewGuid().ToString("N")
    $temp_dir = [System.IO.Path]::GetTempPath()
    $worker_path = Join-Path $temp_dir "sd-webui-all-in-one-enable-long-paths-$id.ps1"
    $status_path = Join-Path $temp_dir "sd-webui-all-in-one-enable-long-paths-$id.status"
    $worker_script = @'
param(
    [Parameter(Mandatory=$true)][string]$StatusPath
)
$ErrorActionPreference = 'Stop'
try {
    New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -PropertyType DWORD -Force | Out-Null
    Set-Content -LiteralPath $StatusPath -Encoding UTF8 -Value 'SUCCESS'
    exit 0
} catch {
    try { Set-Content -LiteralPath $StatusPath -Encoding UTF8 -Value ('FAILURE: ' + $_.Exception.Message) } catch {}
    exit 1
}
'@

    try {
        Set-Content -LiteralPath $worker_path -Encoding UTF8 -Value $worker_script
        $command = Get-CurrentPowerShellExecutable
        if ([string]::IsNullOrWhiteSpace($command)) {
            throw "未找到 PowerShell 可执行文件"
        }
        $argument_line = Join-ProcessArguments @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $worker_path, "-StatusPath", $status_path)
        Write-Log "正在请求管理员权限以启用 Windows 长路径支持"
        $process = Start-Process -FilePath $command -ArgumentList $argument_line -Verb RunAs -Wait -PassThru -ErrorAction Stop
        $exit_code = if ($null -ne $process) { [int]$process.ExitCode } else { -1 }
        $status = $null
        if (Test-Path -LiteralPath $status_path -PathType Leaf) {
            $status = Get-Content -LiteralPath $status_path -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        }
        if (Test-WindowsLongPathsEnabled) {
            Write-Log "Windows 长路径支持已成功启用"
            return [PSCustomObject]@{ Success = $true; Canceled = $false; Message = "Windows 长路径支持已启用。"; ExitCode = $exit_code }
        }
        $message = if (-not [string]::IsNullOrWhiteSpace($status)) { $status.Trim() } else { "管理员进程未能启用 Windows 长路径支持。退出代码: $exit_code" }
        Write-Log "Windows 长路径支持启用失败: $message" -Level WARNING
        return [PSCustomObject]@{ Success = $false; Canceled = $false; Message = $message; ExitCode = $exit_code }
    } catch {
        $exception = $_.Exception
        $is_canceled = $false
        if ($exception -is [System.ComponentModel.Win32Exception] -and $exception.NativeErrorCode -eq 1223) {
            $is_canceled = $true
        }
        if ($exception.Message -match "cancel|取消") {
            $is_canceled = $true
        }
        if ($is_canceled) {
            Write-Log "用户取消了启用 Windows 长路径支持的管理员权限请求" -Level WARNING
            return [PSCustomObject]@{ Success = $false; Canceled = $true; Message = "用户取消了管理员权限请求。"; ExitCode = -1 }
        }
        Write-Log "启动管理员进程启用 Windows 长路径支持失败: $($exception.Message)" -Level WARNING
        return [PSCustomObject]@{ Success = $false; Canceled = $false; Message = $exception.Message; ExitCode = -1 }
    } finally {
        Remove-Item -LiteralPath $worker_path -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $status_path -Force -ErrorAction SilentlyContinue
    }
}


# 启动时检查 Windows 长路径支持
function Invoke-WindowsLongPathsStartupCheck {
    if ((Get-CurrentPlatform) -ne "windows") {
        return
    }
    if ($script:BuildMode) {
        Write-Log "构建模式已启用, 跳过 Windows 长路径支持弹窗检查"
        return
    }
    if (Test-WindowsLongPathsEnabled) {
        return
    }

    Write-Log "检测到 Windows 长路径支持未启用, 建议启用以避免路径过长导致安装或启动失败" -Level WARNING
    try {
        Add-Type -AssemblyName PresentationFramework
        $msg_title = "启用 Windows 长路径支持"
        $msg_text = "检测到当前系统尚未启用 Windows 长路径支持。`n`nAI WebUI / 训练工具的依赖、扩展、模型缓存目录可能很深，未启用时可能导致下载、解压、安装或启动失败。`n`n是否现在以管理员权限启用 Windows 长路径支持？"
        $result = [System.Windows.MessageBox]::Show($msg_text, $msg_title, [System.Windows.MessageBoxButton]::YesNo, [System.Windows.MessageBoxImage]::Warning)
        if ($result -ne [System.Windows.MessageBoxResult]::Yes) {
            Write-Log "已跳过启用 Windows 长路径支持。之后可以运行 configure_env.bat, 或手动执行 New-ItemProperty 命令启用 LongPathsEnabled" -Level WARNING
            return
        }

        $enable_result = Enable-WindowsLongPathsElevated
        if ($enable_result.Success) {
            [System.Windows.MessageBox]::Show("Windows 长路径支持已启用。`n`n部分程序可能需要重新启动后才能完全识别该系统设置。", "启用完成", [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Information) | Out-Null
            return
        }

        if ($enable_result.Canceled) {
            Write-Log "管理员权限请求已取消, Windows 长路径支持未启用。之后可以运行 configure_env.bat 或手动执行 New-ItemProperty 命令启用 LongPathsEnabled" -Level WARNING
            [System.Windows.MessageBox]::Show("未获得管理员权限，Windows 长路径支持尚未启用。`n`n启动脚本会继续运行；之后可以运行 configure_env.bat，或手动执行注册表命令启用。", "未启用长路径支持", [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Warning) | Out-Null
            return
        }

        Write-Log "启用 Windows 长路径支持失败: $($enable_result.Message)" -Level WARNING
        [System.Windows.MessageBox]::Show("启用 Windows 长路径支持失败。`n`n$($enable_result.Message)`n`n启动脚本会继续运行；之后可以运行 configure_env.bat，或手动执行注册表命令启用。", "启用失败", [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Warning) | Out-Null
    } catch {
        Write-Log "Windows 长路径支持弹窗检查失败: $($_.Exception.Message)" -Level WARNING
    }
}


# 获取帮助信息
function Get-HelpMessage {
    if (!($script:Help)) { return }
    $script = Get-Command $script:OriginalScriptPath
    $common = [System.Management.Automation.Internal.CommonParameters].GetProperties().Name
    $display_params = $script.Parameters.Values | Where-Object { $_.Name -notin $common } | ForEach-Object {
        $p_name = $_.Name
        $p_type = $_.ParameterType.Name
        if ($_.ParameterType -eq [switch]) {
            $format = "-$p_name"
        }
        else {
            # 处理数组类型的显示逻辑
            # 如果是数组, PowerShell 习惯在类型名后加 []
            if ($_.ParameterType.IsArray) {
                # 移除原类型名中的 [] 或 System. 前缀, 统一格式
                $clean_type = "$($_.ParameterType.GetElementType().Name)[]"
            } else {
                $clean_type = $p_type
            }
            $format = "-$p_name <$clean_type>"
        }
        $help_msg = $_.Attributes.HelpMessage
        [PSCustomObject]@{
            Name = $format
            HelpMessage = $help_msg
        }
    }
    $usage = @"
使用:
    $((Get-Process -Id $PID).Path) ${script:OriginalScriptPath} $(foreach ($i in $display_params.Name) { "[$i]" })
"@
    $param_info = @"
参数:
$(
    foreach ($i in $display_params) {
        $text = "    $($i.Name)"
        if ($i.HelpMessage) {
            $indented_help = ($i.HelpMessage -split "`?`
" | ForEach-Object { "        $_" }) -join "`
"
            $text += "`
$indented_help"
        }
        $text + "`
`
"
    }
)
"@
    $docs_url = "更多的帮助信息请阅读 ComfyUI Installer 使用文档: https://licyk.github.io/sd-webui-all-in-one/installer/comfyui/"
    Write-Host $($usage + "`
`
" + $param_info + "`
" + $docs_url)
    exit 0
}


# 设置 Installer 内核路径前缀
function Set-CorePrefix {
    $prefix_list = @("core", "ComfyUI*")
    $core_prefix_file = Join-NormalizedPath $PSScriptRoot "core_prefix.txt"
    $origin_core_prefix = if ($script:CorePrefix) {
        $script:CorePrefix
    } else {
        Get-TrimmedTextFile $core_prefix_file -Encoding UTF8
    }
    if (-not [string]::IsNullOrWhiteSpace($origin_core_prefix)) {
        Write-Log "检测到 core_prefix.txt 配置文件 / -CorePrefix 命令行参数, 使用自定义 Installer 内核路径前缀"
        $normalized_core_prefix = $origin_core_prefix.TrimEnd('\', '/')
        if ([System.IO.Path]::IsPathRooted($normalized_core_prefix)) {
            $from_uri = New-Object System.Uri($PSScriptRoot.Replace('\', '/') + '/')
            $to_uri = New-Object System.Uri($normalized_core_prefix.Replace('\', '/'))
            $target_prefix = $from_uri.MakeRelativeUri($to_uri).ToString().Trim('/')
            Write-Log "转换绝对路径为 Installer 内核路径前缀: $origin_core_prefix -> $target_prefix"
        }
    }
    $target_prefix = Resolve-CorePrefix -BasePath $PSScriptRoot -PrefixList $prefix_list -ConfiguredPrefix $origin_core_prefix
    $env:CORE_PREFIX = $target_prefix
    $full_core_path = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX
    Write-Log "当前 Installer 内核路径前缀: $env:CORE_PREFIX"
    Write-Log "完整内核路径: $full_core_path"
}


# 代理配置
function Set-Proxy {
    [CmdletBinding()]
    param ([Parameter()][switch]$Legacy)
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
    if ($script:DisableProxy -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt"))) {
        Write-Log "检测到本地存在 disable_proxy.txt 代理配置文件 / -DisableProxy 命令行参数, 禁用自动设置代理"
        return
    }
    if ($script:UseCustomProxy -or (Test-Path (Join-NormalizedPath $PSScriptRoot "proxy.txt"))) {
        if ($script:UseCustomProxy) {
            $proxy_value = $script:UseCustomProxy
        } else {
            $proxy_value = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "proxy.txt") -Encoding UTF8
        }
        if (-not [string]::IsNullOrWhiteSpace($proxy_value)) {
            $env:HTTP_PROXY = $proxy_value
            $env:HTTPS_PROXY = $proxy_value
            Write-Log "检测到本地存在 proxy.txt 代理配置文件 / -UseCustomProxy 命令行参数, 已读取代理配置文件并设置代理"
            return
        }
    }
    if ($Legacy) {
        $proxy_value = & python -m sd_webui_all_in_one self-manager get proxy
        if (![string]::IsNullOrWhiteSpace($proxy_value)) {
            $env:HTTP_PROXY = $proxy_value
            $env:HTTPS_PROXY = $proxy_value
            Write-Log "检测到系统设置了代理, 已读取系统中的代理配置并设置代理"
        }
    } else {
        $env:SD_WEBUI_ALL_IN_ONE_PROXY = 1
        Write-Log "使用自动检测代理模式进行代理配置"
    }
}


# 设置模型下载来源
function Set-ModelMirror {
    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if (!(Set-AutoMirror $ArrayList)) { return }
    $ArrayList.Add("--source") | Out-Null
    if ((!(Test-Path (Join-NormalizedPath $PSScriptRoot "disable_model_mirror.txt"))) -and (!($script:DisableModelMirror))) {
        Write-Log "使用 ModelScope 模型下载来源"
        $ArrayList.Add("modelscope") | Out-Null
    } else {
        Write-Log "检测到 disable_model_mirror.txt 配置文件 / -DisableModelMirror 命令行参数, 已将模型下载来源切换至 Hugging Face 源"
        $ArrayList.Add("huggingface") | Out-Null
    }
}


# Hugging Face 镜像源
function Set-HuggingFaceMirror {
    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if (!(Set-AutoMirror $ArrayList)) { return }
    if ($script:DisableHuggingFaceMirror -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_hf_mirror.txt"))) {
        Write-Log "检测到本地存在 disable_hf_mirror.txt 镜像源配置文件 / -DisableHuggingFaceMirror 命令行参数, 禁用自动设置 Hugging Face 镜像源"
        $ArrayList.Add("--no-hf-mirror") | Out-Null
        return
    }
    if ($script:UseCustomHuggingFaceMirror -or (Test-Path (Join-NormalizedPath $PSScriptRoot "hf_mirror.txt"))) {
        if ($script:UseCustomHuggingFaceMirror) {
            $hf_mirror_value = $script:UseCustomHuggingFaceMirror
        } else {
            $hf_mirror_value = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "hf_mirror.txt") -Encoding UTF8
        }
        if (-not [string]::IsNullOrWhiteSpace($hf_mirror_value)) {
            $ArrayList.Add("--custom-hf-mirror") | Out-Null
            $ArrayList.Add($hf_mirror_value) | Out-Null
            Write-Log "检测到本地存在 hf_mirror.txt 配置文件 / -UseCustomHuggingFaceMirror 命令行参数, 已读取该配置并设置 Hugging Face 镜像源"
            return
        }
    }
    Write-Log "使用默认 Hugging Face 镜像源"
}


# 设置 GitHub 镜像源
function Set-GithubMirror {
    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if (Test-Path (Join-NormalizedPath $PSScriptRoot ".gitconfig")) {
        Remove-Item -Path (Join-NormalizedPath $PSScriptRoot ".gitconfig") -Force -Recurse
    }
    if (!(Set-AutoMirror $ArrayList)) { return }
    if ($script:DisableGithubMirror -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_gh_mirror.txt"))) {
        Write-Log "检测到本地存在 disable_gh_mirror.txt GitHub 镜像源配置文件 / -DisableGithubMirror 命令行参数, 禁用 GitHub 镜像源"
        $ArrayList.Add("--no-github-mirror") | Out-Null
        return
    }
    if ($script:UseCustomGithubMirror -or (Test-Path (Join-NormalizedPath $PSScriptRoot "gh_mirror.txt"))) {
        if ($script:UseCustomGithubMirror) {
            $github_mirror = $script:UseCustomGithubMirror
        } else {
            $github_mirror = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "gh_mirror.txt") -Encoding UTF8
        }
        if (-not [string]::IsNullOrWhiteSpace($github_mirror)) {
            Write-Log "检测到本地存在 gh_mirror.txt GitHub 镜像源配置文件 / -UseCustomGithubMirror 命令行参数, 已读取 GitHub 镜像源配置文件并设置 GitHub 镜像源"
            $ArrayList.Add("--custom-github-mirror") | Out-Null
            $ArrayList.Add($github_mirror) | Out-Null
        }
    }
}


# 设置 CUDA 内存分配器
function Set-PyTorchCUDAMemoryAlloc {
    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if ($script:DisableCUDAMalloc -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_set_pytorch_cuda_memory_alloc.txt"))) {
        Write-Log "检测到 disable_set_pytorch_cuda_memory_alloc.txt 配置文件 / -DisableCUDAMalloc 命令行参数, 已禁用自动设置 CUDA 内存分配器"
        $ArrayList.Add("--no-cuda-malloc") | Out-Null
    }
}


# 创建 Windows 创建启动快捷方式
function Add-WindowsShortcut {
    [CmdletBinding()]
    param (
        [string]$Name,
        [string]$IconPath
    )
    $shell = New-Object -ComObject WScript.Shell
    $desktop = $([System.Environment]::GetFolderPath("Desktop"))
    $shortcut_path = Join-NormalizedPath $desktop "${Name}.lnk"
    $shortcut = $shell.CreateShortcut($shortcut_path)
    $shortcut.TargetPath = (Get-Process -Id $PID).Path
    $launch_script_path = Join-NormalizedPath $PSScriptRoot "launch.ps1"
    $shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$launch_script_path`""
    $shortcut.IconLocation = $IconPath
    $shortcut.Save()
    Copy-Item -Path $shortcut_path -Destination (Join-NormalizedPath $([System.Environment]::GetFolderPath("ApplicationData")) "Microsoft" "Windows" "Start Menu" "Programs") -Force
}


# 创建 Linux 创建启动快捷方式
function Add-LinuxShortcut {
    [CmdletBinding()]
    param (
        [string]$Name,
        [string]$IconPath
    )
    $pwsh_bin = (Get-Process -Id $PID).Path
    $launch_script_path = Join-NormalizedPath $PSScriptRoot "launch.ps1"
    $desktop = $([System.Environment]::GetFolderPath("Desktop"))
    $shortcut_path = Join-NormalizedPath $desktop "${Name}.desktop"
    $content = "
[Desktop Entry]
Encoding=UTF-8
Version=1.0
Name=$Name
Comment=Installer 启动脚本
Icon=$IconPath
Exec=`"$pwsh_bin`" -ExecutionPolicy Bypass -File `"$launch_script_path`" %f
Terminal=true
startupNotify=true
Type=Application
".Trim()
    Write-FileWithStreamWriter -Path $shortcut_path -Encoding UTF8 -Value $content
    $local_app_path = Join-NormalizedPath $([System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile)) ".local" "share" "applications"
    $local_app_shortcut_path = Join-NormalizedPath $local_app_path "${Name}.desktop"
    New-Item -ItemType Directory -Path $local_app_path -Force | Out-Null
    Copy-Item -Path $shortcut_path -Destination $local_app_shortcut_path -Force
    & chmod +x "$shortcut_path"
    & chmod +x "$local_app_shortcut_path"
}


# 创建 MacOS 创建启动快捷方式
function Add-MacOSShortcut {
    [CmdletBinding()]
    param (
        [string]$Name,
        [string]$IconPath
    )

    $pwsh_bin = (Get-Process -Id $PID).Path
    $launch_script_path = Join-NormalizedPath $PSScriptRoot "launch.ps1"
    $desktop = $([System.Environment]::GetFolderPath("Desktop"))

    $app_path = Join-NormalizedPath $desktop "${Name}.app"
    $contents_path = Join-NormalizedPath $app_path "Contents"
    $macos_path = Join-NormalizedPath $contents_path "MacOS"
    $resources_path = Join-NormalizedPath $contents_path "Resources"

    New-Item -ItemType Directory -Path $macos_path -Force | Out-Null
    New-Item -ItemType Directory -Path $resources_path -Force | Out-Null

    $working_dir_for_shell = $PSScriptRoot.Replace('"', '\"').Replace('$', '\$')
    $pwsh_bin_for_shell = $pwsh_bin.Replace('"', '\"').Replace('$', '\$')
    $launch_script_for_shell = $launch_script_path.Replace('"', '\"').Replace('$', '\$')

    $executable_path = Join-NormalizedPath $macos_path "launcher"
    $sh_content = @"
#!/bin/bash

osascript <<'APPLESCRIPT'
tell application "Terminal"
    activate
    do script "cd \"$working_dir_for_shell\" || exit 1; exec \"$pwsh_bin_for_shell\" -NoExit -ExecutionPolicy Bypass -File \"$launch_script_for_shell\""
end tell
APPLESCRIPT
"@
    $sh_content = $sh_content.Replace("``
", "`
").Replace("`", "`
")
    Write-FileWithStreamWriter -Path $executable_path -Encoding UTF8 -Value $sh_content
    & chmod +x "$executable_path"

    $plist_path = Join-NormalizedPath $contents_path "Info.plist"
    $plist_content = @"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon.icns</string>
    <key>CFBundleName</key>
    <string>${Name}</string>
    <key>CFBundleDisplayName</key>
    <string>${Name}</string>
    <key>CFBundleIdentifier</key>
    <string>local.sdwebuiallinone.$($Name.ToLower() -replace '[^a-z0-9]', '')</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>LSBackgroundOnly</key>
    <false/>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
</dict>
</plist>
"@
    Write-FileWithStreamWriter -Path $plist_path -Encoding UTF8 -Value $plist_content

    if (Test-Path $IconPath) {
        Copy-Item -Path $IconPath -Destination (Join-NormalizedPath $resources_path "AppIcon.icns") -Force
    }

    $applications_folder = "/Applications"
    if (Test-Path $applications_folder) {
        Copy-Item -Path $app_path -Destination $applications_folder -Recurse -Force
    }
}


# 下载应用图标
function Get-AppIcon {
    [CmdletBinding()]
    param ([Parameter(Mandatory=$true)][Hashtable]$IconMap)
    $platform = Get-CurrentPlatform
    if (-not $IconMap.ContainsKey($platform)) {
        Write-Log "未找到平台 [$platform] 的图标配置" -Level WARNING
        return $null
    }
    $config = $IconMap[$platform]
    $fileName = $config.FileName
    $localIconPath = Join-NormalizedPath $PSScriptRoot $fileName
    if (Test-Path $localIconPath) { return $localIconPath }
    foreach ($url in $config.Urls) {
        try {
            Write-Log "正在下载 $platform 图标: $url"
            $web_request_params = @{
                Uri = $url
                UseBasicParsing = $true
                OutFile = $localIconPath
                TimeoutSec = 15
                ErrorAction = "Stop"
            }
            Invoke-WebRequest @web_request_params
            if (Test-Path $localIconPath) { return $localIconPath }
        }
        catch {
            Write-Log "链接失效: $url" -Level WARNING
        }
    }
    return $null
}


# 创建创建启动快捷方式
function New-AppShortcut {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][Hashtable]$IconMap
    )
    $finalIconPath = Get-AppIcon -IconMap $IconMap
    if (-not $finalIconPath) {
        Write-Log "图标获取失败，跳过创建创建启动快捷方式" -Level ERROR
        return
    }
    $platform = Get-CurrentPlatform
    switch ($platform) {
        "windows" { Add-WindowsShortcut -Name $Name -IconPath $finalIconPath }
        "linux"   { Add-LinuxShortcut -Name $Name -IconPath $finalIconPath }
        "macos"   { Add-MacOSShortcut -Name $Name -IconPath $finalIconPath }
    }
}


# 测试 Python 和 Git 可用性
function Test-PythonAndGit {
    # Python
    $python_cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($python_cmd) {
        $python_cmd = Get-NormalizedFilePath $python_cmd.Path
        $python_path_prefix = Join-NormalizedPath $PSScriptRoot "python"
        $python_extra_path_prefix = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "python"
        if (-not (($python_cmd) -and (($python_cmd.ToString().StartsWith($python_path_prefix, [System.StringComparison]::OrdinalIgnoreCase)) -or ($python_cmd.ToString().StartsWith($python_extra_path_prefix, [System.StringComparison]::OrdinalIgnoreCase))))) {
            Write-Log "检测到当前使用的 Python 路径为 ${python_cmd}, 但未在 ${python_path_prefix} 或 ${python_extra_path_prefix} 这两个受 ComfyUI Installer 管理的 Python 路径, 即当前正在使用外部的 Python 环境, 这可能会导致一些运行环境问题, 可尝试运行 launch_comfyui_install.ps1 修复运行环境" -Level ERROR
        }
    } else {
        Write-Log "检测到当前环境中未安装任何 Python, 这将导致运行时发生异常, 请运行 launch_comfyui_install.ps1 修复运行环境" -Level ERROR
    }

    # Git
    $git_cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($git_cmd) {
        if ((Get-CurrentPlatform) -eq "windows") {
            $git_cmd = Get-NormalizedFilePath $git_cmd.Path
            $git_path_prefix = Join-NormalizedPath $PSScriptRoot "git"
            $git_extra_path_prefix = Join-NormalizedPath $PSScriptRoot $env:CORE_PREFIX "git"
            if (-not (($git_cmd) -and (($git_cmd.ToString().StartsWith($git_path_prefix, [System.StringComparison]::OrdinalIgnoreCase)) -or ($git_cmd.ToString().StartsWith($git_extra_path_prefix, [System.StringComparison]::OrdinalIgnoreCase))))) {
                Write-Log "检测到当前使用的 Git 路径为 ${git_cmd}, 但未在 ${git_path_prefix} 或 ${git_extra_path_prefix} 这两个受 ComfyUI Installer 管理的 Git 路径, 即当前正在使用外部的 Git 环境, 这可能会导致一些运行环境问题, 可尝试运行 launch_comfyui_install.ps1 修复运行环境" -Level ERROR
            }
        }
    } else {
        Write-Log "检测到当前环境中未安装任何 Git, 这将导致运行时发生异常, 请运行 launch_comfyui_install.ps1 修复运行环境" -Level ERROR
    }
}


# 获取启动参数
function Get-WebUILaunchArgs {
    param ([System.Collections.ArrayList]$ArrayList)
    if (($script:LaunchArg) -or (Test-Path (Join-NormalizedPath $PSScriptRoot "launch_args.txt"))) {
        if ($script:LaunchArg) {
            $launch_args = $script:LaunchArg.Trim()
        } else {
            $launch_args = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "launch_args.txt") -Encoding UTF8
        }
        if ([string]::IsNullOrEmpty($launch_args)) {
            return
        }
        $ArrayList.Add("--launch-args") | Out-Null
        $ArrayList.Add($launch_args + " ") | Out-Null
        Write-Log "检测到本地存在 launch_args.txt 启动参数配置文件 / -LaunchArg 命令行参数, 已读取该启动参数配置文件并应用启动参数"
        Write-Log "使用的启动参数: $launch_args"
    }
}


# 设置快捷启动方式
function Add-Shortcut {
    $filename = "ComfyUI"
    $IconConfig = @{
        "windows" = @{
            FileName = "comfyui_icon.ico"
            Urls     = @("https://modelscope.cn/models/licyks/sd-webui-all-in-one/resolve/master/icon/comfyui_icon.ico", "https://huggingface.co/licyk/sd-webui-all-in-one/resolve/main/icon/comfyui_icon.ico")
        }
        "linux"   = @{
            FileName = "comfyui_icon.png"
            Urls     = @("https://modelscope.cn/models/licyks/sd-webui-all-in-one/resolve/master/icon/comfyui_icon.png", "https://huggingface.co/licyk/sd-webui-all-in-one/resolve/main/icon/comfyui_icon.png")
        }
        "macos"   = @{
            FileName = "comfyui_icon.icns"
            Urls     = @("https://modelscope.cn/models/licyks/sd-webui-all-in-one/resolve/master/icon/comfyui_icon.icns", "https://huggingface.co/licyk/sd-webui-all-in-one/resolve/main/icon/comfyui_icon.icns")
        }
    }
    if ((!(Test-Path (Join-NormalizedPath $PSScriptRoot "enable_shortcut.txt"))) -and (!($script:EnableShortcut))) {
        return
    }
    Write-Log "检测到 enable_shortcut.txt 配置文件 / -EnableShortcut 命令行参数, 开始检查 ComfyUI 快捷启动方式中"
    New-AppShortcut -IconMap $IconConfig -Name $filename
}


# 检测 Microsoft Visual C++ Redistributable
function Test-MSVCPPRedistributable {
    if ((Get-CurrentPlatform) -ne "windows") {
        Write-Log "非 Windows 系统，跳过 Microsoft Visual C++ Redistributable 检测"
        return
    }

    Write-Log "检测 Microsoft Visual C++ Redistributable 是否缺失"

    $vc_runtime_dll_names = @(
        "concrt140.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll",
        "vcamp140.dll",
        "vccorlib140.dll",
        "vcomp140.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "vcruntime140_threads.dll"
    )

    if ([string]::IsNullOrEmpty($env:SYSTEMROOT)) {
        $windows_dir = Join-NormalizedPath "C:/" "Windows"
    } else {
        $windows_dir = $env:SYSTEMROOT
    }

    if ([Environment]::Is64BitOperatingSystem -and (-not [Environment]::Is64BitProcess)) {
        $vc_runtime_dir = Join-NormalizedPath $windows_dir "Sysnative"
    } else {
        $vc_runtime_dir = Join-NormalizedPath $windows_dir "System32"
    }

    $missing_vc_runtime_dll_names = @()
    foreach ($vc_runtime_dll_name in $vc_runtime_dll_names) {
        $vc_runtime_dll_path = Join-NormalizedPath $vc_runtime_dir $vc_runtime_dll_name
        if (-not (Test-Path -LiteralPath $vc_runtime_dll_path -PathType Leaf)) {
            $missing_vc_runtime_dll_names += $vc_runtime_dll_name
        }
    }

    if ($missing_vc_runtime_dll_names.Count -eq 0) {
        Write-Log "Microsoft Visual C++ Redistributable 未缺失"
        return
    }

    $missing_vc_runtime_dll_list = $missing_vc_runtime_dll_names -join ", "
    Write-Log "检测到 Microsoft Visual C++ Redistributable 缺失的核心 DLL: $missing_vc_runtime_dll_list" -Level WARNING
    Write-Log "检测到 Microsoft Visual C++ Redistributable 缺失, 这可能导致 PyTorch 无法正常识别 GPU 导致报错" -Level WARNING
    Write-Log "请下载并安装 Microsoft Visual C++ Redistributable 后重新启动"

    Add-Type -AssemblyName PresentationFramework
    $msg_text = "检测到系统缺失 Microsoft Visual C++ Redistributable, 这可能导致程序无法正常运行。请下载并安装该组件, 并重新启动以解决问题。`n`n是否立即打开下载页面?"
    $msg_title = "缺失系统组件"
    $result = [System.Windows.MessageBox]::Show($msg_text, $msg_title, [System.Windows.MessageBoxButton]::YesNo, [System.Windows.MessageBoxImage]::Warning)

    if ($result -eq [System.Windows.MessageBoxResult]::Yes) {
        $download_url = "https://aka.ms/vc14/vc_redist.x64.exe"
        Write-Log "正在打开下载链接: $download_url"
        Start-Process $download_url
    }
}


# 检查运行环境
function Test-WebUIEnv {
    param ([System.Collections.ArrayList]$ArrayList)
    if (($script:DisableEnvCheck) -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_check_env.txt"))) {
        Write-Log "检测到 disable_check_env.txt 配置文件 / -DisableEnvCheck 命令行参数, 已禁用 ComfyUI 启动前环境检测, 这可能会导致 ComfyUI 运行环境中存在的问题无法被发现并解决" -Level WARNING
        $ArrayList.Add("--no-check-env") | Out-Null
    }
}


# 自动快照开关状态
function Test-SnapshotDisabled {
    if ((Test-Path (Join-NormalizedPath $PSScriptRoot "disable_snapshot.txt")) -or ($script:DisableSnapshot)) {
        return $true
    }
    return $false
}


# 为会触发自动快照的 CLI 命令配置快照参数
function Set-SnapshotCliArgs {
    [CmdletBinding()]
    param ([System.Collections.ArrayList]$ArrayList)
    if (Test-SnapshotDisabled) {
        if (-not $ArrayList.Contains("--no-snapshot")) {
            $ArrayList.Add("--no-snapshot") | Out-Null
        }
        Write-Log "检测到 disable_snapshot.txt 配置文件 / -DisableSnapshot 命令行参数, 已禁用自动快照"
    }
}

# 设置 Hotpatcher 补丁系统
function Set-Hotpatcher {
    param ([System.Collections.ArrayList]$ArrayList)
    if (($script:DisableHotpatcher) -or (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_hotpatcher.txt"))) {
        $ArrayList.Add("--no-hotpatcher") | Out-Null
        Write-Log "检测到 disable_hotpatcher.txt 配置文件 / -DisableHotpatcher 命令行参数, 已禁用 Hotpatcher 补丁系统"
        return
    }

    $default_config_path = Join-NormalizedPath $PSScriptRoot "patcher_config.json"
    if (!(Test-Path $default_config_path)) {
        Write-Log "未检测到默认 Hotpatcher 配置文件, 正在导出默认配置: $default_config_path"
        & python -m sd_webui_all_in_one self-manager patcher export-config *> $null
        $exit_code = Get-NativeCommandExitCode -Success $?
        if ($exit_code -ne 0) {
            Write-Log "导出 Hotpatcher 默认配置失败" -Level ERROR
            Exit-ManagerScript -ExitCode $exit_code
        }
    }
    $hotpatcher_runtime_enabled = $script:EnableHotpatcherRuntime -or (Test-Path (Join-NormalizedPath $PSScriptRoot "enable_hotpatcher_runtime.txt"))
    if ($hotpatcher_runtime_enabled) {
        $ArrayList.Add("--hotpatcher-runtime") | Out-Null
        Write-Log "检测到 enable_hotpatcher_runtime.txt 配置文件 / -EnableHotpatcherRuntime 命令行参数, 已启用 Hotpatcher runtime host 连接"
    } elseif ($script:HotpatcherPortSpecified -or (Test-Path (Join-NormalizedPath $PSScriptRoot "hotpatcher_port.txt"))) {
        Write-Log "检测到 Hotpatcher 端口配置, 但未启用 Hotpatcher runtime, 已忽略该端口配置" -Level WARNING
    }

    $hotpatcher_port = $null
    if ($script:HotpatcherPortSpecified) {
        if (($script:HotpatcherPort -ge 1) -and ($script:HotpatcherPort -le 65535)) {
            $hotpatcher_port = $script:HotpatcherPort
            Write-Log "检测到 -HotpatcherPort 命令行参数, 使用 Hotpatcher runtime 通信端口: $hotpatcher_port"
        } else {
            Write-Log "HotpatcherPort 端口范围应为 1..65535, 已忽略无效端口: $($script:HotpatcherPort)" -Level WARNING
        }
    } elseif (Test-Path (Join-NormalizedPath $PSScriptRoot "hotpatcher_port.txt")) {
        $port_text = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot "hotpatcher_port.txt") -Encoding UTF8
        $port_value = 0
        if (([int]::TryParse($port_text, [ref]$port_value)) -and ($port_value -ge 1) -and ($port_value -le 65535)) {
            $hotpatcher_port = $port_value
            Write-Log "检测到 hotpatcher_port.txt 配置文件, 使用 Hotpatcher runtime 通信端口: $hotpatcher_port"
        } else {
            Write-Log "hotpatcher_port.txt 中的端口范围应为 1..65535, 已忽略无效端口: $port_text" -Level WARNING
        }
    }

    if ($hotpatcher_runtime_enabled -and ($null -ne $hotpatcher_port)) {
        $ArrayList.Add("--hotpatcher-port") | Out-Null
        $ArrayList.Add($hotpatcher_port) | Out-Null
    }

    Write-Log "Hotpatcher 补丁系统默认启用"
}


Export-ModuleMember -Function `
    Initialize-EnvPath, `
    Write-Log, `
    Format-CommandLineArgumentForLog, `
    Format-CoreCliCommandForLog, `
    Write-CoreCliFailureCommand, `
    Write-FileWithStreamWriter, `
    Update-SDWebUiAllInOne, `
    Update-Installer, `
    Get-Version, `
    Get-HelpMessage, `
    Set-CorePrefix, `
    Set-Proxy, `
    Set-PyPIMirror, `
    Set-ModelMirror, `
    Set-HuggingFaceMirror, `
    Set-GithubMirror, `
    Set-uv, `
    Set-PyTorchCUDAMemoryAlloc, `
    Get-WebUILaunchArgs, `
    Add-Shortcut, `
    Test-MSVCPPRedistributable, `
    Test-WebUIEnv, `
    Test-SnapshotDisabled, `
    Set-SnapshotCliArgs, `
    Set-Hotpatcher, `
    Join-NormalizedPath, `
    Get-NormalizedFilePath, `
    Get-CurrentPlatform, `
    Invoke-WindowsLongPathsStartupCheck, `
    Get-CurrentArchitecture, `
    New-AppShortcut, `
    Test-PythonAndGit, `
    Install-HanafubukiPackage, `
    Move-PortableLauncherRuntime, `
    Get-NativeCommandExitCode, `
    Exit-ManagerScript, `
    Get-TrimmedTextFile