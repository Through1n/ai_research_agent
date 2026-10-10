; =============================================================
; AI4S 科研助手 (AI4SResearchAgent) - Inno Setup 脚本
;
; 构建步骤:
;   1. 先在项目根运行 build_desktop.bat, 产出 dist\AI4SResearchAgent\
;      (其中应包含 AI4SResearchAgent.exe 及其 onedir 依赖)
;   2. 用 Inno Setup Compiler (ISCC) 打开本脚本编译:
;        ISCC.exe installer.iss
;      产物默认输出到 Output\AI4SResearchAgent-Setup-1.0.exe
;
; 注意: 本文件含中文, 以 UTF-8 with BOM 保存, 供 Inno Setup 6 读取。
; =============================================================

[Setup]
; 固定 AppId, 每次构建保持不变 (升级/卸载识别依赖它)
AppId={{F62546FC-E8C5-4A13-B93F-174C70AF90F6}}
AppName=AI4S 科研助手
AppVersion=1.0
AppPublisher=AI4S 科研助手团队
DefaultDirName={autopf}\AI4SResearchAgent
DefaultGroupName=AI4S 科研助手
DisableProgramGroupPage=yes
OutputBaseFilename=AI4SResearchAgent-Setup-1.0
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\AI4SResearchAgent.exe

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "dist\AI4SResearchAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AI4S 科研助手"; Filename: "{app}\AI4SResearchAgent.exe"; IconFilename: "{app}\AI4SResearchAgent.exe"
Name: "{group}\卸载 AI4S 科研助手"; Filename: "{uninstallexe}"; Comment: "{cm:UninstallProgram,AI4S 科研助手}"
Name: "{autodesktop}\AI4S 科研助手"; Filename: "{app}\AI4SResearchAgent.exe"; IconFilename: "{app}\AI4SResearchAgent.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AI4SResearchAgent.exe"; Description: "{cm:LaunchProgram,AI4S 科研助手}"; Flags: nowait postinstall skipifsilent
