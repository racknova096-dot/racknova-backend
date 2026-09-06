#define MyAppName "RackNova Local"
#define MyAppVersion "0.1.9-native-f1"
#define MyAppPublisher "RackNova"

[Setup]
AppId={{A6464E3C-1357-4E4A-B5D0-5ED3ED9441F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RackNova
DefaultGroupName=RackNova
OutputDir=output
OutputBaseFilename=RackNova_Setup_Native_F1_9
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
Source: "configure_install_entry.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "cloud_link.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
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
var
  CloudChoicePage: TInputOptionWizardPage;
  CloudConfigPage: TInputQueryWizardPage;

function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
external 'SetEnvironmentVariableW@Kernel32.dll stdcall delayload';

procedure InitializeWizard();
begin
  CloudChoicePage := CreateInputOptionPage(
    wpSelectDir,
    'RackNova Cloud',
    'Conecta RackNova Local con tu cuenta Cloud',
    'RackNova Local seguirá funcionando sin Internet. Si conectas Cloud, los cambios podrán sincronizarse cuando haya conexión.',
    False,
    False
  );
  CloudChoicePage.Add('Conectar este equipo con RackNova Cloud');
  CloudChoicePage.Values[0] := True;

  CloudConfigPage := CreateInputQueryPage(
    CloudChoicePage.ID,
    'Conexión con RackNova Cloud',
    'Datos de sincronización',
    'Usa los mismos datos con los que ya vinculabas RackNova Local. El secreto no se guarda en el log del instalador.'
  );
  CloudConfigPage.Add('URL de RackNova Cloud:', False);
  CloudConfigPage.Values[0] := 'https://racknova-backend-1.onrender.com';
  CloudConfigPage.Add('ID de empresa:', False);
  CloudConfigPage.Values[1] := '11111111-1111-4111-8111-111111111111';
  CloudConfigPage.Add('RACKNOVA_SYNC_SECRET:', True);
  CloudConfigPage.Values[2] := '';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = CloudConfigPage.ID) and (not CloudChoicePage.Values[0]) then
    Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  CloudUrl: String;
  EmpresaId: String;
  SyncSecret: String;
begin
  Result := True;

  if (CurPageID = CloudConfigPage.ID) and CloudChoicePage.Values[0] then
  begin
    CloudUrl := Trim(CloudConfigPage.Values[0]);
    EmpresaId := Trim(CloudConfigPage.Values[1]);
    SyncSecret := Trim(CloudConfigPage.Values[2]);

    if Pos('https://', LowerCase(CloudUrl)) <> 1 then
    begin
      MsgBox('La URL de RackNova Cloud debe comenzar con https://', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if Length(EmpresaId) < 32 then
    begin
      MsgBox('El ID de empresa no parece un UUID válido.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if Length(SyncSecret) < 20 then
    begin
      MsgBox('RACKNOVA_SYNC_SECRET debe tener al menos 20 caracteres.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if (Pos('"', CloudUrl) > 0) or (Pos('"', EmpresaId) > 0) then
    begin
      MsgBox('Los datos de Cloud contienen caracteres no permitidos.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;

  if not IsWin64 then
  begin
    MsgBox('RackNova Local requiere Windows de 64 bits.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure RunCloudLinkStep(
  PowerShellExe: String;
  CloudLinkScript: String;
  InstallDir: String;
  Mode: String;
  CloudUrl: String;
  EmpresaId: String;
  var ResultCode: Integer
);
var
  Args: String;
begin
  Args :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + CloudLinkScript +
    '" -Mode ' + Mode +
    ' -InstallDir "' + InstallDir + '"';

  if CloudUrl <> '' then
    Args := Args + ' -CloudUrl "' + CloudUrl + '"';

  if EmpresaId <> '' then
    Args := Args + ' -EmpresaId "' + EmpresaId + '"';

  if not Exec(
    PowerShellExe,
    Args,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    RaiseException('No fue posible iniciar la configuración de RackNova Cloud.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PowerShellExe: String;
  Args: String;
  InstallDir: String;
  CloudLinkScript: String;
  CloudUrl: String;
  EmpresaId: String;
  SyncSecret: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Configurando RackNova Local...';

    PowerShellExe := ExpandConstant(
      '{sys}\WindowsPowerShell\v1.0\powershell.exe'
    );
    InstallDir := ExpandConstant('{app}');
    CloudLinkScript := ExpandConstant('{app}\installer\cloud_link.ps1');

    { Preservar cualquier vínculo Cloud existente antes de reparar/recrear local. }
    RunCloudLinkStep(
      PowerShellExe,
      CloudLinkScript,
      InstallDir,
      'Backup',
      '',
      '',
      ResultCode
    );
    if ResultCode <> 0 then
      RaiseException(
        'No pude respaldar la conexión Cloud anterior. La instalación se detuvo para no perderla.'
      );

    Args :=
      '-NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\installer\configure_install_entry.ps1') +
      '" -InstallDir "' + InstallDir + '"';

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

    { Si una reparación reemplazó config/secrets, restaurar el vínculo protegido. }
    RunCloudLinkStep(
      PowerShellExe,
      CloudLinkScript,
      InstallDir,
      'Restore',
      '',
      '',
      ResultCode
    );
    if ResultCode <> 0 then
      RaiseException(
        'RackNova Local quedó instalado, pero no pude restaurar la conexión Cloud anterior.'
      );

    if CloudChoicePage.Values[0] then
    begin
      WizardForm.StatusLabel.Caption := 'Conectando RackNova Local con Cloud...';
      CloudUrl := Trim(CloudConfigPage.Values[0]);
      EmpresaId := Trim(CloudConfigPage.Values[1]);
      SyncSecret := Trim(CloudConfigPage.Values[2]);

      if not SetEnvironmentVariable('RACKNOVA_INSTALL_SYNC_SECRET', SyncSecret) then
        RaiseException('No pude preparar de forma segura la credencial Cloud.');

      try
        RunCloudLinkStep(
          PowerShellExe,
          CloudLinkScript,
          InstallDir,
          'Activate',
          CloudUrl,
          EmpresaId,
          ResultCode
        );
      finally
        SetEnvironmentVariable('RACKNOVA_INSTALL_SYNC_SECRET', '');
      end;

      if ResultCode <> 0 then
        RaiseException(
          'RackNova Local quedó instalado, pero la conexión con RackNova Cloud falló. ' +
          'Revisa C:\ProgramData\RackNova\Logs\cloud-link-*.log.'
        );
    end;
  end;
end;
