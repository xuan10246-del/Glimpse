@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LT_BASE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$base = $env:LT_BASE.TrimEnd('\'); " ^
  "$appName = 'Glimpse ' + ([string][char[]]@(0x77AC,0x8BD1)); " ^
  "$exePath = Join-Path $base 'Glimpse.exe'; " ^
  "$batPath = Join-Path $base (([string][char[]]@(0x542F,0x52A8,0x7FFB,0x8BD1,0x5668)) + '.bat'); " ^
  "$hasExe = Test-Path $exePath; " ^
  "$desk = [Environment]::GetFolderPath('Desktop'); " ^
  "$lnkPath = Join-Path $desk ($appName + '.lnk'); " ^
  "$sc = $ws.CreateShortcut($lnkPath); " ^
  "$sc.TargetPath = if ($hasExe) { $exePath } else { $batPath }; " ^
  "$sc.WorkingDirectory = $base; " ^
  "$sc.IconLocation = Join-Path $base 'assets\AppIcon.ico'; " ^
  "$sc.WindowStyle = if ($hasExe) { 1 } else { 7 }; " ^
  "$sc.Save(); " ^
  "Write-Host ('已创建桌面快捷方式: ' + $lnkPath)"
if "%~1"=="--silent" exit /b 0
pause
