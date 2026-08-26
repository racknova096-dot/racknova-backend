#define MyAppName "RackNova Local"
#define MyAppVersion "0.1.0-native-f1"
#define MyAppPublisher "RackNova"

[Setup]
AppId={{A6464E3C-1357-4E4A-B5D0-5ED3ED9441F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RackNova
DefaultGroupName=RackNova
OutputDir=output
OutputBaseFilename=RackNova_Setup_Native_F1
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableProgramGroupPage=yes
SetupLogging=yes
UninstallDisplayIcon={app}\RackNovaLocalService.exe

[Files]
Source: "..\..\dist\RackNovaLocalService.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\RackNovaCtl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\postgresql-16.15-1-windows-x64.exe"; DestDir: "{app}\vendor"; Flags: ignoreversion
Source: "configure_install.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "uninstall_runtime.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\RackNova\Config"
Name: "{commonappdata}\RackNova\Logs"
Name: "{commonappdata}\RackNova\Backups"
Name: "{commonappdata}\RackNova\Diagnostics"


[INI]
Filename: "{commondesktop}\RackNova.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:8000/ui/"
Filename: "{group}\RackNova.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:8000/ui/"

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\uninstall_runtime.ps1"""; \
    Flags: runhidden waituntilterminated; RunOnceId: "RackNovaRemoveService"

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
      '" -InstallDir "' + ExpandConstant('{app}') +
      '" -PostgresInstaller "' +
      ExpandConstant('{app}\vendor\postgresql-16.15-1-windows-x64.exe') +
      '"';

    if not Exec(
      PowerShellExe,
      Args,
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) then
      RaiseException('No fue posible iniciar la configuración de RackNova.');

    if ResultCode <> 0 then
      RaiseException(
        'RackNova no pudo completar la configuración. Código: ' +
        IntToStr(ResultCode)
      );
  end;
end;
