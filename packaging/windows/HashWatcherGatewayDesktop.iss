#define MyAppName "HashWatcher Gateway Desktop"
#define MyAppExeName "HashWatcherGatewayDesktop.exe"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{B1703C4A-B7D3-4FF1-99F5-8FCED6AB7CA2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\HashWatcherGatewayDesktop
DefaultGroupName={#MyAppName}
OutputDir={#OutDir}
OutputBaseFilename=HashWatcherGatewayDesktop-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
