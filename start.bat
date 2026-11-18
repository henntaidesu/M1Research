@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "BACKEND_DIR=%SCRIPT_DIR%\CoreCode\backend"
set "WEBSITE_DIR=%SCRIPT_DIR%\CoreCode\frontend"
set "CONDA_ENV_NAME=webFaceEmotionRec"

REM 查找并激活 conda 环境
where conda >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%i in ('conda info --base') do set "CONDA_BASE=%%i"
) else (
    if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" (
        set "CONDA_BASE=%USERPROFILE%\miniconda3"
    ) else if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" (
        set "CONDA_BASE=%USERPROFILE%\anaconda3"
    ) else (
        echo 未找到 conda，请先安装并确保可以使用环境: %CONDA_ENV_NAME%
        exit /b 1
    )
)

set "CONDA_BAT=%CONDA_BASE%\condabin\conda.bat"

REM 环境不存在则自动创建（conda-forge，避免 defaults 频道的 ToS 确认）
call "%CONDA_BAT%" env list | findstr /b /i /c:"%CONDA_ENV_NAME% " >nul
if %errorlevel% neq 0 (
    echo 未找到 conda 环境 %CONDA_ENV_NAME%，正在创建 Python 3.11 环境...
    call "%CONDA_BAT%" create -y -n "%CONDA_ENV_NAME%" -c conda-forge --override-channels python=3.11 pip
    if !errorlevel! neq 0 (
        REM 部分网络下 conda 访问官方 conda-forge 会被 403，改用清华镜像重试
        echo 官方源失败，改用清华镜像重试...
        call "%CONDA_BAT%" create -y -n "%CONDA_ENV_NAME%" -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge --override-channels python=3.11 pip
    )
    if !errorlevel! neq 0 (
        echo 创建 conda 环境失败: %CONDA_ENV_NAME%
        exit /b 1
    )
)

call "%CONDA_BASE%\Scripts\activate.bat" "%CONDA_ENV_NAME%"
if %errorlevel% neq 0 (
    echo 激活 conda 环境失败: %CONDA_ENV_NAME%
    exit /b 1
)

REM 后端依赖：以环境内标记文件判断是否已装完，安装失败下次启动会重试
set "DEPS_MARKER=%CONDA_PREFIX%\.webFaceEmotionRec_deps_ok"
if not exist "%DEPS_MARKER%" (
    echo 正在安装后端依赖（首次较慢）...
    python -m pip install --upgrade pip
    REM PyTorch CUDA 版需单独装；facenet-pytorch 2.6.0 锁死 torch 2.2，用 --no-deps 防止被降级成 CPU 版
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

REM 前端依赖检查
if exist "%WEBSITE_DIR%\package.json" (
    if not exist "%WEBSITE_DIR%\node_modules" (
        echo 检测到前端依赖未安装，正在执行 npm install...
        pushd "%WEBSITE_DIR%"
        call npm install
        popd
    )
)

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
