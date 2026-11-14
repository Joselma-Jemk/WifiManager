; Installateur Windows de WifiManager (Inno Setup 6).
; Construit par le CI :
;   iscc /DAppVersion=0.1.0 /DAppDir=dist\bin packaging\wifimanager.iss
; AppDir est le dossier produit par Nuitka en mode « standalone » (pas de fichier unique).
;
; Droits administrateur OBLIGATOIRES, et installation dans Program Files : le service de blocage tourne avec les droits
; élevés et lance l'exécutable installé. Si ce dossier était modifiable par un simple utilisateur, n'importe quel
; programme pourrait y glisser son code et le faire exécuter en administrateur.
; Les données de l'utilisateur (%APPDATA%\WifiManager) ne sont JAMAIS supprimées à la désinstallation.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppDir
  #define AppDir "..\dist\bin"
#endif

[Setup]
AppId={{B2D0C2A6-6F3B-4B38-9E57-4D5C8A1E7F21}
AppName=WifiManager
AppVersion={#AppVersion}
AppVerName=WifiManager {#AppVersion}
AppPublisher=Joselma00
AppPublisherURL=https://github.com/Joselma-Jemk/WifiManager
AppSupportURL=https://github.com/Joselma-Jemk/WifiManager/issues
AppUpdatesURL=https://github.com/Joselma-Jemk/WifiManager/releases
AppContact=contact@joselma.online
DefaultDirName={autopf}\WifiManager
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\wifimanager.exe
UninstallDisplayName=WifiManager {#AppVersion}
PrivilegesRequired=admin
LicenseFile=..\LICENSE
SetupIconFile=..\src\wifimanager\assets\wifimanager.ico
OutputDir=..\dist
OutputBaseFilename=wifimanager-{#AppVersion}-windows-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\WifiManager"; Filename: "{app}\wifimanager.exe"
Name: "{autodesktop}\WifiManager"; Filename: "{app}\wifimanager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\wifimanager.exe"; Description: "{cm:LaunchProgram,WifiManager}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  TaskName = 'WifiManagerService';

var
  ServiceWasInstalled: Boolean;

{ Lance une commande PowerShell cachée et attend sa fin. Le code de retour est ignoré : ces commandes sont facultatives. }
procedure Ps(Command: String);
var
  Code: Integer;
begin
  Exec('powershell.exe', '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Command + '"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ Arrête le service de blocage (tâche planifiée et processus) pour libérer les fichiers à remplacer ou à supprimer. }
procedure StopService;
var
  Code: Integer;
begin
  Ps('Stop-ScheduledTask -TaskName ''' + TaskName + ''' -ErrorAction SilentlyContinue');
  Exec('taskkill.exe', '/F /IM wifimanager.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function TaskExists: Boolean;
var
  Code: Integer;
begin
  Result := Exec('schtasks.exe', '/Query /TN ' + TaskName, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ServiceWasInstalled := TaskExists;
  if ServiceWasInstalled then
    StopService;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Mise à jour : le service reprend avec la nouvelle version, sans que l'utilisateur ait à le relancer. }
  if (CurStep = ssPostInstall) and ServiceWasInstalled then
    Ps('Start-ScheduledTask -TaskName ''' + TaskName + ''' -ErrorAction SilentlyContinue');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService;
    Ps('Unregister-ScheduledTask -TaskName ''' + TaskName + ''' -Confirm:$false -ErrorAction SilentlyContinue');
  end;
end;
