#define MyAppName "RackNova Local"
#define MyAppVersion "0.1.8-native-f1"
#define MyAppPublisher "RackNova"

[Setup]
AppId={{A6464E3C-1357-4E4A-B5D0-5ED3ED9441F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RackNova
DefaultGroupName=RackNova
OutputDir=output
OutputBaseFilename=RackNova_Setup_Native_F1_7
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=racknova.ico
DisableProgramGroupPage=yes
SetupLogging=yes
UninstallDisplayIcon={app}\racknova.ico

[Files]
Source: "..\..\dist\RackNovaLocalService\RackNovaLocalService.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\RackNovaLocalService\_service_internal\*"; DestDir: "{app}\_service_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "racknova.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\RackNovaCtl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "postgresql_portable\*"; DestDir: "{app}\PostgreSQL"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "configure_install.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "uninstall_runtime.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\RackNova\Config"
Name: "{commonappdata}\RackNova\Logs"
Name: "{commonappdata}\RackNova\Backups"
Name: "{commonappdata}\RackNova\Diagnostics"
Name: "{commonappdata}\RackNova\PostgreSQL"

[INI]
Filename: "{commondesktop}\RackNova.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:8000/ui/"
Filename: "{commondesktop}\RackNova.url"; Section: "InternetShortcut"; Key: "IconFile"; String: "{app}\racknova.ico"
Filename: "{commondesktop}\RackNova.url"; Section: "InternetShortcut"; Key: "IconIndex"; String: "0"
Filename: "{group}\RackNova.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:8000/ui/"
Filename: "{group}\RackNova.url"; Section: "InternetShortcut"; Key: "IconFile"; String: "{app}\racknova.ico"
Filename: "{group}\RackNova.url"; Section: "InternetShortcut"; Key: "IconIndex"; String: "0"

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\uninstall_runtime.ps1"""; \
    Flags: runhidden waituntilterminated; RunOnceId: "RackNovaRemoveServices"

[UninstallDelete]
Type: files; Name: "{commondesktop}\RackNova.url"
Type: files; Name: "{group}\RackNova.url"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;

  if not IsWin64 then
  begin
    MsgBox('RackNova Local requiere Windows de 64 bits.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PowerShellExe: String;
  Args: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Configurando RackNova Local...';

    PowerShellExe := ExpandConstant(
      '{sys}\WindowsPowerShell\v1.0\powershell.exe'
    );

    Args :=
      '-NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\installer\configure_install.ps1') +
      '" -InstallDir "' + ExpandConstant('{app}') + '"';

    if not Exec(
      PowerShellExe,
      Args,
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) then
      RaiseException(
        'No fue posible iniciar la configuración de RackNova.'
      );

    if ResultCode <> 0 then
      RaiseException(
        'RackNova no pudo completar la configuración. Código: ' +
        IntToStr(ResultCode) + '. Revisa C:\ProgramData\RackNova\Logs para ver el diagnóstico.'
      );
  end;
end;
