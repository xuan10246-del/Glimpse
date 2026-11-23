[Setup]
AppId={{8F6A2E41-5C3B-4B9D-9E71-2A4C6F8B1D30}
AppName=Glimpse 瞬译
AppVersion=3.1.0
AppVerName=Glimpse 瞬译 v3.1.0
AppPublisher=Glimpse Open Source
AppPublisherURL=https://github.com/xuan10246-del/Glimpse
AppSupportURL=https://github.com/xuan10246-del/Glimpse/issues
AppUpdatesURL=https://github.com/xuan10246-del/Glimpse/releases
DefaultDirName={code:GetDefaultInstallDir}
DefaultGroupName=Glimpse 瞬译
DisableDirPage=no
DisableProgramGroupPage=yes
DirExistsWarning=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=..\assets\AppIcon.ico
UninstallDisplayIcon={app}\Glimpse.exe
UninstallDisplayName=Glimpse 瞬译 v3.1.0
OutputDir=D:\
OutputBaseFilename=Glimpse_v3.1.0_Setup_Win64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式（推荐）"; GroupDescription: "附加任务："

[Files]
Source: "..\Glimpse.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Glimpse_Debug.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\PRODUCT.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\DESIGN.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\setup_and_download.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\一键安装运行.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\启动翻译器.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\启动翻译器(调试日志).bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\创建桌面快捷方式.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,*.pyo"
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\models\ocr\*.onnx"; DestDir: "{app}\models\ocr"; Flags: ignoreversion
Source: "..\tools\uv.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

[Dirs]
Name: "{app}\tools\tmp"
Name: "{app}\tools\hf_cache"

[Icons]
Name: "{autodesktop}\Glimpse 瞬译"; Filename: "{app}\Glimpse.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\AppIcon.ico"; Tasks: desktopicon
Name: "{group}\Glimpse 瞬译"; Filename: "{app}\Glimpse.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\AppIcon.ico"
Name: "{group}\Glimpse 瞬译 (调试日志)"; Filename: "{app}\Glimpse_Debug.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\AppIcon.ico"
Name: "{group}\重新初始化或修复模型"; Filename: "{app}\一键安装运行.bat"; WorkingDir: "{app}"
Name: "{group}\卸载 Glimpse 瞬译"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\一键安装运行.bat"; Parameters: "--from-installer"; WorkingDir: "{app}"; Description: "立即初始化本地 Python 环境与 AI 模型并启动 Glimpse 瞬译（推荐）"; Flags: postinstall nowait skipifsilent

[Code]
function GetDefaultInstallDir(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\Glimpse'
  else if DirExists('E:\') then
    Result := 'E:\Glimpse'
  else
    Result := ExpandConstant('{localappdata}\Glimpse');
end;

procedure InitializeWizard;
begin
  WizardForm.SelectDirLabel.Caption :=
    '选择 Glimpse 瞬译的安装文件夹。推荐安装在 D 盘或其他非系统盘：' + #13#10 +
    '本地 AI 模型、便携 Python 环境与全部临时缓存均严格保存在所选目录内（首次初始化约需 3.5~5 GB），绝不占用 C 盘空间。';
end;

procedure DeleteWildcardFiles(const DirPath, Pattern: String);
var
  FindRec: TFindRec;
begin
  if FindFirst(DirPath + '\' + Pattern, FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
          DeleteFile(DirPath + '\' + FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir: String;
  CleanAll: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    AppDir := ExpandConstant('{app}');
    if UninstallSilent then
      CleanAll := True
    else
      CleanAll := (MsgBox(
        '是否同时彻底清理已下载的本地 AI 模型、便携 Python 运行环境、推理引擎与配置文件（约可释放 3.5~5 GB 空间）？',
        mbConfirmation, MB_YESNO
      ) = IDYES);

    if CleanAll then
    begin
      DelTree(AppDir + '\.venv', True, True, True);
      DelTree(AppDir + '\models', True, True, True);
      DelTree(AppDir + '\bin', True, True, True);
      DelTree(AppDir + '\tools', True, True, True);
      DeleteFile(AppDir + '\config.json');
      DeleteWildcardFiles(AppDir, '*.log');
      RemoveDir(AppDir);
    end;
  end;
end;
