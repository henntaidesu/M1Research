@echo off
@setlocal DisableDelayedExpansion
set "__WorkPath__=%~dp0"
if "%__WorkPath__:~-1%"=="\" set "__WorkPath__=%__WorkPath__:~0,-1%"
set "__PowerShell__=powershell"
where pwsh >nul 2>nul
if not errorlevel 1 set "__PowerShell__=pwsh"
"%__PowerShell__%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$PowerShell = [Environment]::GetEnvironmentVariable('__PowerShell__');" ^
    "if (-not $PowerShell) { $PowerShell = 'powershell'; }" ^
    "$WorkPath = [Environment]::GetEnvironmentVariable('__WorkPath__');" ^
    "if ($WorkPath) { Set-Location -LiteralPath $WorkPath; }" ^
    "$ScriptUrls = @(" ^
    "    'https://github.com/licyk/sd-webui-all-in-one-launcher/releases/download/launcher/install.ps1'," ^
    "    'https://gitee.com/licyk/sd-webui-all-in-one-launcher/releases/download/launcher/install.ps1'," ^
    "    'https://github.com/licyk/sd-webui-all-in-one-launcher/raw/main/install.ps1'," ^
    "    'https://gitlab.com/licyk/sd-webui-all-in-one-launcher/-/raw/main/install.ps1'," ^
    "    'https://gitee.com/licyk/sd-webui-all-in-one-launcher/raw/main/install.ps1'" ^
    ");" ^
    "$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ('sd-webui-all-in-one-launcher-' + [System.Guid]::NewGuid().ToString('N'));" ^
    "$TempScript = Join-Path $TempDir 'install.ps1';" ^
    "$ExitCode = 0;" ^
    "try {" ^
    "    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12;" ^
    "    New-Item -Path $TempDir -ItemType Directory -Force | Out-Null;" ^
    "    $Downloaded = $false;" ^
    "    foreach ($Url in $ScriptUrls) {" ^
    "        try {" ^
    "            Write-Host ('Downloading SD WebUI All In One Launcher installer: ' + $Url);" ^
    "            Invoke-WebRequest -Uri $Url -OutFile $TempScript -UseBasicParsing -TimeoutSec 15;" ^
    "            if ((Test-Path -LiteralPath $TempScript) -and ((Get-Item -LiteralPath $TempScript).Length -gt 0)) { $Downloaded = $true; break; }" ^
    "        } catch {" ^
    "            Write-Warning ('Download failed: ' + $Url + ' - ' + $_.Exception.Message);" ^
    "            Remove-Item -LiteralPath $TempScript -Force -ErrorAction SilentlyContinue;" ^
    "        }" ^
    "    }" ^
    "    if (-not $Downloaded) { throw 'All download urls are unavailable.'; }" ^
    "    & $PowerShell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $TempScript;" ^
    "    if ($null -ne $LASTEXITCODE) { $ExitCode = $LASTEXITCODE; }" ^
    "} catch {" ^
    "    Write-Error $_;" ^
    "    $ExitCode = 1;" ^
    "} finally {" ^
    "    Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue;" ^
    "}" ^
    "exit $ExitCode"
exit %errorlevel%