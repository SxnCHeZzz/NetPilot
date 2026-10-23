#define MyAppName "NetPilot"
#define MyAppVersion "0.5.4-rc5"
#define MyAppExe "NetPilot.exe"

[Setup]
AppId={{D7F845AB-D1A6-4284-A948-7B6E880A5632}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=NetPilot contributors
DefaultDirName={localappdata}\Programs\NetPilot
DefaultGroupName=NetPilot
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=..\assets\netpilot.ico
OutputDir=..\dist\installer
OutputBaseFilename=NetPilotSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
DisableProgramGroupPage=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked
Name: "autostart"; Description: "Запускать NetPilot вместе с Windows (в трее)"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked

[Files]
Source: "..\dist\NetPilot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\NetPilot"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\NetPilot"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NetPilot"; ValueData: """{app}\{#MyAppExe}"" --tray"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Запустить NetPilot после установки"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; User configs and downloaded networking tools are deliberately retained by default.
