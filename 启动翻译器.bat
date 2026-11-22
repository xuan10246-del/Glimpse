@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "FOR_DISABLE_CONSOLE_CTRL_HANDLER=1"
set "PYTHONNOUSERSITE=1"

set "PORTABLE_PY="
for /d %%D in ("%~dp0tools\python\cpython-3.12*") do (
    if exist "%%~fD\pythonw.exe" set "PORTABLE_PY=%%~fD\pythonw.exe"
)

if defined PORTABLE_PY (
    if exist "%~dp0.venv\Lib\site-packages" (
        set "PYTHONPATH=%~dp0.venv\Lib\site-packages;%~dp0"
        start "" "%PORTABLE_PY%" "%~dp0app\main.py"
        exit /b 0
    )
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\main.py"
    exit /b 0
)

echo [提示] 未检测到本地 Python 运行环境，请先双击运行 “一键安装运行.bat” 完成初始化。
pause
exit /b 1
