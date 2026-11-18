@echo off
echo Initialize configuration
setlocal enabledelayedexpansion
set CurrentPath=%~dp0
set DefaultCorePrefix=ComfyUI
if exist "%~dp0%DefaultCorePrefix%" (
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
        if "%%i"=="-CorePrefix" (
            set NextIsValue=1
        )
    )
)

if exist "%CorePrefixFile%" (
    for /f "delims=" %%i in ('powershell -NoLogo -NoProfile -Command "$corePrefixText = Get-Content -Path '%CorePrefixFile%' -Raw; if (-not [string]::IsNullOrWhiteSpace($corePrefixText)) { $corePrefixText.Trim() }"') do (
        set CorePrefix=%%i
        goto :convert
    )
)

:convert
for /f "delims=" %%i in ('powershell -NoLogo -NoProfile -Command "$current_path = '%CurrentPath%'.Trim('/').Trim('\'); $origin_core_prefix = '%CorePrefix%'.Trim('/').Trim('\'); if ([System.IO.Path]::IsPathRooted($origin_core_prefix)) { $to_path = $origin_core_prefix; $from_uri = New-Object System.Uri($current_path.Replace('\', '/') + '/'); $to_uri = New-Object System.Uri($to_path.Replace('\', '/')); $origin_core_prefix = $from_uri.MakeRelativeUri($to_uri).ToString().Trim('/') }; Write-Host $origin_core_prefix"') do (
    set CorePrefix=%%i
    goto :continue
)

:continue
set RootPath=%~dp0%CorePrefix%
echo CorePrefix: %CorePrefix%
echo RootPath: %RootPath%
if exist "%RootPath%" (
    cd /d "%RootPath%"
) else (
    echo %CorePrefix% not found
    echo Please check if ComfyUI is installed, or if the CorePrefix is set correctly
    pause
    exit 1
)
if exist .\hanamizuki.exe (
    echo Launch Hanamizuki
    start /B .\hanamizuki.exe
    cd /d "%CurrentPath%"
) else (
    echo Hanamizuki not found
    echo Try running terminal.ps1 to open the terminal and execute the Install-Hanamizuki command to install Hanamizuki
    cd /d "%CurrentPath%"
    pause
    exit 1
)