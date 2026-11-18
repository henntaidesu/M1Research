@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "BACKEND_DIR=%SCRIPT_DIR%\CoreCode\backend"
set "WEBSITE_DIR=%SCRIPT_DIR%\CoreCode\frontend"
set "CONDA_ENV_NAME=webFaceEmotionRec"
set "MINICONDA_DIR=%USERPROFILE%\miniconda3"
set "NODE_VERSION=22.12.0"
set "NODE_PORTABLE_DIR=%LOCALAPPDATA%\Programs\node-v%NODE_VERSION%-win-x64"

REM ===================== conda =====================
call :find_conda
if not defined CONDA_BASE call :install_miniconda
if not defined CONDA_BASE (
    echo 未找到 conda 且自动安装 Miniconda 失败，请手动安装后重试
    exit /b 1
)
set "CONDA_BAT=%CONDA_BASE%\condabin\conda.bat"
set "ENV_DIR=%CONDA_BASE%\envs\%CONDA_ENV_NAME%"

REM 环境不存在（或上次创建到一半）则重建（conda-forge，避免 defaults 频道的 ToS 确认）
if not exist "%ENV_DIR%\python.exe" (
    if exist "%ENV_DIR%" (
        echo conda 环境 %CONDA_ENV_NAME% 不完整，正在删除重建...
        call "%CONDA_BAT%" env remove -y -n "%CONDA_ENV_NAME%" >nul 2>nul
        if exist "%ENV_DIR%" rmdir /s /q "%ENV_DIR%"
    )
    echo 未找到 conda 环境 %CONDA_ENV_NAME%，正在创建 Python 3.11 环境...
    call "%CONDA_BAT%" create -y -n "%CONDA_ENV_NAME%" -c conda-forge --override-channels python=3.11 pip
    if not exist "%ENV_DIR%\python.exe" (
        REM 国内网络下 conda 访问官方 conda-forge 会被 403，改用清华镜像重试
        echo 官方源失败，改用清华镜像重试...
        call "%CONDA_BAT%" create -y -n "%CONDA_ENV_NAME%" -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge --override-channels python=3.11 pip
    )
    if not exist "%ENV_DIR%\python.exe" (
        echo 创建 conda 环境失败: %CONDA_ENV_NAME%
        exit /b 1
    )
    if exist "%ENV_DIR%\.webFaceEmotionRec_deps_ok" del "%ENV_DIR%\.webFaceEmotionRec_deps_ok"
)

call "%CONDA_BASE%\Scripts\activate.bat" "%CONDA_ENV_NAME%"
if %errorlevel% neq 0 (
    echo 激活 conda 环境失败: %CONDA_ENV_NAME%
    exit /b 1
)

REM 依赖：标记文件缺失或关键包导入不到（环境被改坏）都重新安装
set "DEPS_MARKER=%CONDA_PREFIX%\.webFaceEmotionRec_deps_ok"
set "NEED_PIP=0"
if not exist "%DEPS_MARKER%" set "NEED_PIP=1"
python -c "import importlib.util as u,sys; sys.exit(any(u.find_spec(m) is None for m in ('fastapi','uvicorn','multipart','torch','torchvision','timm','cv2','numpy','hsemotion','facenet_pytorch','requests','tqdm')))" >nul 2>nul
if errorlevel 1 set "NEED_PIP=1"
if "%NEED_PIP%"=="1" (
    echo 正在安装后端依赖（首次较慢）...
    python -m pip install --upgrade pip
    REM PyTorch CUDA 版需单独装；facenet-pytorch 2.6.0 锁死 torch 2.2，用 --no-deps 防止它拉回 CPU 版
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    if !errorlevel! neq 0 goto :pip_failed
    findstr /v /i /c:"facenet-pytorch" "%BACKEND_DIR%\requirements.txt" > "%TEMP%\wfer_requirements.txt"
    python -m pip install -r "%TEMP%\wfer_requirements.txt"
    if !errorlevel! neq 0 goto :pip_failed
    python -m pip install --no-deps facenet-pytorch==2.6.0
    if !errorlevel! neq 0 goto :pip_failed
    python -m pip install requests tqdm
    if !errorlevel! neq 0 goto :pip_failed
    echo ok> "%DEPS_MARKER%"
)

REM ===================== Node.js / Vue =====================
if exist "%NODE_PORTABLE_DIR%\npm.cmd" set "PATH=%NODE_PORTABLE_DIR%;%PATH%"
call :check_node
if errorlevel 1 call :install_node
call :check_node
if errorlevel 1 (
    echo 未能安装 Node.js 18+，请手动安装 https://nodejs.org 后重试
    exit /b 1
)

REM node_modules 缺失或不完整（没有 vite）则安装前端依赖
if not exist "%WEBSITE_DIR%\node_modules\.bin\vite.cmd" (
    echo 正在安装前端依赖 npm install...
    pushd "%WEBSITE_DIR%"
    call npm install
    if !errorlevel! neq 0 (
        echo 官方 npm 源失败，改用 npmmirror 镜像重试...
        call npm install --registry=https://registry.npmmirror.com
    )
    popd
    if not exist "%WEBSITE_DIR%\node_modules\.bin\vite.cmd" (
        echo 前端依赖安装失败，请检查网络后重新运行本脚本
        exit /b 1
    )
)

REM ===================== 端口 =====================
REM 上次运行的后端/前端没退干净会占着端口，导致本次启动失败
call :free_port 9501
if errorlevel 1 exit /b 1
call :free_port 9500
if errorlevel 1 exit /b 1

echo 正在启动后端服务（后台运行）...
start /b "" cmd /c "cd /d "%BACKEND_DIR%" && call "%CONDA_BASE%\Scripts\activate.bat" "%CONDA_ENV_NAME%" && python main.py"

echo 正在启动前端服务（当前窗口运行，Ctrl+C 退出）...
cd /d "%WEBSITE_DIR%"
call npm run dev

endlocal
exit /b 0

:pip_failed
echo 后端依赖安装失败，请检查网络后重新运行本脚本
exit /b 1

REM ===================== 子过程 =====================

:find_conda
set "CONDA_BASE="
where conda >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%i in ('conda info --base 2^>nul') do set "CONDA_BASE=%%i"
)
if defined CONDA_BASE if exist "%CONDA_BASE%\condabin\conda.bat" exit /b 0
set "CONDA_BASE="
if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"& exit /b 0
if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\anaconda3"& exit /b 0
if exist "%ProgramData%\miniconda3\condabin\conda.bat" set "CONDA_BASE=%ProgramData%\miniconda3"& exit /b 0
if exist "%ProgramData%\anaconda3\condabin\conda.bat" set "CONDA_BASE=%ProgramData%\anaconda3"& exit /b 0
exit /b 0

:install_miniconda
echo 未找到 conda，正在自动安装 Miniconda 到 %MINICONDA_DIR% ...
set "MC_EXE=%TEMP%\Miniconda3-latest-Windows-x86_64.exe"
curl -L --fail -o "%MC_EXE%" https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe
if errorlevel 1 (
    echo 官方源下载失败，改用清华镜像...
    curl -L --fail -o "%MC_EXE%" https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Windows-x86_64.exe
)
if not exist "%MC_EXE%" exit /b 1
REM 静默安装；/D 必须放最后且不能带引号
start /wait "" "%MC_EXE%" /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=%MINICONDA_DIR%
del "%MC_EXE%" >nul 2>nul
call :find_conda
exit /b 0

:check_node
REM Vite 5 需要 Node 18+
where npm >nul 2>nul
if errorlevel 1 exit /b 1
set "NODE_MAJOR=0"
for /f "delims=" %%v in ('node -p "process.versions.node.split('.')[0]" 2^>nul') do set "NODE_MAJOR=%%v"
if %NODE_MAJOR% lss 18 exit /b 1
exit /b 0

:install_node
echo 未找到 Node.js 18+，正在自动安装...
where winget >nul 2>nul
if not errorlevel 1 (
    winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
    REM winget 改的 PATH 对当前窗口不生效，手动补上
    if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"
    call :check_node
    if not errorlevel 1 exit /b 0
)
REM winget 不可用或失败：下载便携版到 %LOCALAPPDATA%\Programs，无需管理员权限
echo 改为下载便携版 Node.js v%NODE_VERSION% ...
set "NODE_ZIP=%TEMP%\node-v%NODE_VERSION%-win-x64.zip"
curl -L --fail -o "%NODE_ZIP%" https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip
if errorlevel 1 (
    echo 官方源下载失败，改用 npmmirror 镜像...
    curl -L --fail -o "%NODE_ZIP%" https://npmmirror.com/mirrors/node/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip
)
if not exist "%NODE_ZIP%" exit /b 1
if not exist "%LOCALAPPDATA%\Programs" mkdir "%LOCALAPPDATA%\Programs"
tar -xf "%NODE_ZIP%" -C "%LOCALAPPDATA%\Programs"
del "%NODE_ZIP%" >nul 2>nul
if exist "%NODE_PORTABLE_DIR%\npm.cmd" set "PATH=%NODE_PORTABLE_DIR%;%PATH%"
exit /b 0

:free_port
set "PORT_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%~1 .*LISTENING"') do set "PORT_PID=%%p"
if not defined PORT_PID exit /b 0
echo 端口 %~1 已被占用（PID %PORT_PID%），多半是上次启动的服务没有退出：
tasklist /fi "PID eq %PORT_PID%" /nh
choice /c YN /t 15 /d N /m "是否结束该进程后继续（15 秒无操作视为 N）"
if errorlevel 2 (
    echo 已取消启动，请先关闭占用端口 %~1 的程序
    exit /b 1
)
taskkill /pid %PORT_PID% /t /f >nul
timeout /t 1 /nobreak >nul
exit /b 0
