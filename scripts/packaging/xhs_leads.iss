; XHS Leads Windows 安装包定义（Inno Setup 6）
;
; 由 build_win.ps1 调用，路径全部通过 /D 传入，勿在此写死：
;   ISCC.exe /DMyDistDir=<dist>\XHSLeads /DMyOutDir=<桌面> /DMyIcon=<ico> xhs_leads.iss
;
; 装到 %LOCALAPPDATA%\Programs\XHSLeads（PrivilegesRequired=lowest，普通用户免管理员），
; 卸载项与开始菜单快捷方式由 Inno 维护；内置 CFT 引擎随包，目标机无需安装 Chrome。

#ifndef MyDistDir
  #define MyDistDir "."
#endif
#ifndef MyOutDir
  #define MyOutDir "."
#endif
#ifndef MyIcon
  #define MyIcon ""
#endif

[Setup]
AppId={{9F4C2A61-8B3D-4E57-9C08-1D6A5B7E2F34}
AppName=XHS Leads
AppVerName=XHS Leads 小红书获客助手 0.1.0
AppVersion=0.1.0
AppPublisher=XHS Leads
DefaultDirName={localappdata}\Programs\XHSLeads
DefaultGroupName=XHS Leads
DisableProgramGroupPage=yes
OutputDir={#MyOutDir}
OutputBaseFilename=XHSLeads-Setup-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayName=XHS Leads 小红书获客助手
UninstallDisplayIcon={app}\XHSLeads.exe
#if FileExists(MyIcon)
SetupIconFile={#MyIcon}
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 (Create a desktop shortcut)"; GroupDescription: "附加任务 (Additional tasks):"

[Files]
; 整目录递归：_internal（扩展三件套/前端产物/内置 CFT 引擎）+ XHSLeads.exe
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\XHS Leads 小红书获客助手"; Filename: "{app}\XHSLeads.exe"
Name: "{autodesktop}\XHS Leads 小红书获客助手"; Filename: "{app}\XHSLeads.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\XHSLeads.exe"; Description: "启动 XHS Leads (Launch XHS Leads)"; Flags: nowait postinstall skipifsilent
