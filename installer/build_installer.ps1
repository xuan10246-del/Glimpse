$ErrorActionPreference = "Stop"

$InstDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $InstDir

$Csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $Csc)) {
    throw "未找到 .NET Framework csc.exe: $Csc"
}

$IconPath = Join-Path $RootDir "assets\AppIcon.ico"
$ManifestPath = Join-Path $InstDir "launcher\app.manifest"
$LauncherCs = Join-Path $InstDir "launcher\GlimpseLauncher.cs"

Write-Host "[1/4] 编译 Glimpse.exe 与 Glimpse_Debug.exe ..."
& $Csc /utf8output /nologo /target:winexe /optimize+ /platform:x64 `
    "/win32icon:$IconPath" "/win32manifest:$ManifestPath" `
    /r:System.Windows.Forms.dll "/out:$RootDir\Glimpse.exe" $LauncherCs
if ($LASTEXITCODE -ne 0) { throw "Glimpse.exe 编译失败" }

& $Csc /utf8output /nologo /define:DEBUG_CONSOLE /target:exe /optimize+ /platform:x64 `
    "/win32icon:$IconPath" "/win32manifest:$ManifestPath" `
    /r:System.Windows.Forms.dll "/out:$RootDir\Glimpse_Debug.exe" $LauncherCs
if ($LASTEXITCODE -ne 0) { throw "Glimpse_Debug.exe 编译失败" }

Write-Host "[2/4] 检查简体中文语言包 (ChineseSimplified.isl) ..."
$IslPath = Join-Path $InstDir "ChineseSimplified.isl"
if (-not (Test-Path $IslPath)) {
    $urls = @(
        "https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/ChineseSimplified.isl",
        "https://ghfast.top/https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/ChineseSimplified.isl"
    )
    $ok = $false
    foreach ($u in $urls) {
        try {
            $resp = Invoke-WebRequest -Uri $u -TimeoutSec 20 -UseBasicParsing
            if ($resp.Content -and $resp.Content.Contains("[LangOptions]")) {
                $utf8Bom = New-Object System.Text.UTF8Encoding($true)
                $lf = [char]10; $cr = [char]13; $crlfStr = "$cr$lf"
                $norm = ($resp.Content.Replace($crlfStr, [string]$lf)).Replace([string]$lf, $crlfStr)
                [System.IO.File]::WriteAllText($IslPath, $norm, $utf8Bom)
                $ok = $true
                break
            }
        } catch {}
    }
    if (-not $ok) { throw "未能获取 ChineseSimplified.isl" }
}

Write-Host "[3/4] 检查批处理换行符与 .iss UTF-8 BOM 编码 ..."
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
$lf = [char]10; $cr = [char]13; $crlfStr = "$cr$lf"

foreach ($name in @("一键安装运行.bat", "启动翻译器.bat", "启动翻译器(调试日志).bat", "创建桌面快捷方式.bat", "使用说明.txt")) {
    $fp = Join-Path $RootDir $name
    if (Test-Path $fp) {
        $raw = [System.IO.File]::ReadAllText($fp, [System.Text.Encoding]::UTF8)
        $norm = ($raw.Replace($crlfStr, [string]$lf)).Replace([string]$lf, $crlfStr)
        [System.IO.File]::WriteAllText($fp, $norm, $utf8NoBom)
    }
}

$IssPath = Join-Path $InstDir "glimpse_setup.iss"
$issRaw = [System.IO.File]::ReadAllText($IssPath, [System.Text.Encoding]::UTF8)
$issNorm = ($issRaw.Replace($crlfStr, [string]$lf)).Replace([string]$lf, $crlfStr)
[System.IO.File]::WriteAllText($IssPath, $issNorm, $utf8Bom)

Write-Host "[4/4] 编译 Inno Setup 6 安装包..."
$Iscc = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    throw "未找到 Inno Setup 6 (ISCC.exe)。请先执行: winget install JRSoftware.InnoSetup"
}

& $Iscc $IssPath
if ($LASTEXITCODE -ne 0) { throw "ISCC.exe 编译安装程序失败" }

Write-Host "[完成] 安装程序已生成于 D:\Glimpse_v3.1.0_Setup_Win64.exe"
