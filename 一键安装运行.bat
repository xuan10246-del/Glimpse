@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo      Glimpse 瞬译 - 一键初始化与运行向导
echo ========================================================
echo.

set "LT_BASE=%~dp0"
if not exist "%~dp0tools" mkdir "%~dp0tools"
set "UV_EXE=%~dp0tools\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%~dp0tools\python"
set "UV_CACHE_DIR=%~dp0tools\uv-cache"
set "HF_HOME=%~dp0tools\hf_cache"
set "UV_HTTP_TIMEOUT=300"

if not exist "%UV_EXE%" (
    echo [1/5] 正在获取轻量包管理器 uv.exe ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference='Stop'; " ^
      "$base=$env:LT_BASE.TrimEnd('\'); " ^
      "$tmp=Join-Path $env:TEMP 'uv_win.zip'; " ^
      "$u1='https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'; " ^
      "$u2='https://ghfast.top/'+$u1; " ^
      "try { Invoke-WebRequest -Uri $u1 -OutFile $tmp -TimeoutSec 30 } catch { Invoke-WebRequest -Uri $u2 -OutFile $tmp -TimeoutSec 60 }; " ^
      "$ext=Join-Path $env:TEMP 'uv_ext'; Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue; " ^
      "Expand-Archive -Path $tmp -DestinationPath $ext -Force; " ^
      "$found=Get-ChildItem $ext -Filter 'uv.exe' -Recurse | Select-Object -First 1; " ^
      "Copy-Item $found.FullName -Destination (Join-Path $base 'tools\uv.exe') -Force; " ^
      "Remove-Item $tmp,$ext -Recurse -Force -ErrorAction SilentlyContinue"
    if errorlevel 1 (
        echo [错误] uv.exe 下载失败，请检查网络或代理设置后重试。
        pause
        exit /b 1
    )
)

echo [2/5] 配置独立 Python 3.12 运行环境...
"%UV_EXE%" python install 3.12
if errorlevel 1 (
    echo [错误] Python 3.12 下载失败，请检查网络连接。
    pause
    exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
    "%UV_EXE%" venv "%~dp0.venv" --python 3.12
    if errorlevel 1 (
        echo [错误] 创建 .venv 虚拟环境失败。
        pause
        exit /b 1
    )
)

echo [3/5] 安装依赖组件（如直连较慢将自动切换国内清华镜像源）...
"%UV_EXE%" pip install --python "%~dp0.venv\Scripts\python.exe" -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [提示] 切换清华 PyPI 镜像重试安装...
    "%UV_EXE%" pip install --python "%~dp0.venv\Scripts\python.exe" -i https://pypi.tuna.tsinghua.edu.cn/simple -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [错误] Python 依赖安装失败，请查看上方报错。
        pause
        exit /b 1
    )
)

nvidia-smi >nul 2>nul
if not errorlevel 1 (
    echo [硬件] 检测到 NVIDIA 显卡，配置 CUDA 12 运行库...
    "%UV_EXE%" pip install --python "%~dp0.venv\Scripts\python.exe" nvidia-cublas-cu12 nvidia-cudnn-cu12
    if errorlevel 1 (
        "%UV_EXE%" pip install --python "%~dp0.venv\Scripts\python.exe" -i https://pypi.tuna.tsinghua.edu.cn/simple nvidia-cublas-cu12 nvidia-cudnn-cu12
    )
)

echo [4/5] 检查并拉取本地 AI 模型与推理引擎（首次约需 3.4GB，已有文件会自动跳过）...
"%~dp0.venv\Scripts\python.exe" -u "%~dp0setup_and_download.py"
if errorlevel 1 (
    echo [错误] 模型准备失败，请查看上方日志并检查网络后重试。
    pause
    exit /b 1
)

echo [5/5] 创建桌面快捷方式并启动程序...
call "%~dp0创建桌面快捷方式.bat" --silent
call "%~dp0启动翻译器.bat"
exit /b 0
