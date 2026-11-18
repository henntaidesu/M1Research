@echo off
@setlocal DisableDelayedExpansion
set "__WorkPath__=%~dp0"
if "%__WorkPath__:~-1%"=="\" set "__WorkPath__=%__WorkPath__:~0,-1%"
cmd /c "%__WorkPath__%\hanamizuki.bat" %*
exit %errorlevel%