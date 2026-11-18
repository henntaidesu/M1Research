@echo off
@setlocal DisableDelayedExpansion
set "__WorkPath__=%~dp0"
if "%__WorkPath__:~-1%"=="\" set "__WorkPath__=%__WorkPath__:~0,-1%"
set "__PowerShell__=powershell"
where pwsh >nul 2>nul
if not errorlevel 1 set "__PowerShell__=pwsh"
"%__PowerShell__%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%__WorkPath__%\version_manager.ps1" %*
exit %errorlevel%