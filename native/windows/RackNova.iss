#define MyAppName "RackNova Local"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "RackNova"

[Setup]
AppId={{A6464E3C-1357-4E4A-B5D0-5ED3ED9441F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RackNova
DefaultGroupName=RackNova
OutputDir=output
; Se conserva el nombre técnico F1_9 para no romper el workflow existente.
; El artefacto publicado para usuario se renombra a RackNova_Setup_Definitivo.exe.
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
CloseApplications=force
RestartApplications=no
UsePreviousAppDir=yes

[Files]
Source: "..\..\dist\RackNovaLocalService\RackNovaLocalService.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\RackNovaLocalService\_service_internal\*"; DestDir: "{app}\_service_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "racknova.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\RackNovaCtl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "postgresql_portable\*"; DestDir: "{app}\PostgreSQL"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "configure_install.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "configure_install_entry.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "cloud_link.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "recover_legacy_cloud_link.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "reset_definitive.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "bootstrap_cloud_snapshot.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
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

[Run]
Filename: "http://127.0.0.1:8000/ui/"; Description: "Abrir RackNova Local"; Flags: shellexec postinstall skipifsilent nowait

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\uninstall_runtime.ps1"""; \
    Flags: runhidden waituntilterminated; RunOnceId: "RackNovaRemoveServices"

[UninstallDelete]
Type: files; Name: "{commondesktop}\RackNova.url"
Type: files; Name: "{group}\RackNova.url"

[Code]
var
  CloudConfigPage: TInputQueryWizardPage;
  MigrationPage: TInputOptionWizardPage;
  ExistingInstall: Boolean;

function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
external 'SetEnvironmentVariableW@Kernel32.dll stdcall delayload';

function DetectExistingInstall(): Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{commonappdata}\RackNova\Config\config.json')) or
    DirExists(ExpandConstant('{commonappdata}\RackNova\PostgreSQL\data')) or
    FileExists(ExpandConstant('{autopf}\RackNova\RackNovaLocalService.exe'));
end;

function ShouldCleanReset(): Boolean;
var
  SilentValue: String;
begin
  if not ExistingInstall then
  begin
    Result := False;
    Exit;
  end;

  if WizardSilent then
  begin
    SilentValue := ExpandConstant('{param:CLEANLOCAL|0}');
    Result := CompareText(Trim(SilentValue), '1') = 0;
    Exit;
  end;

  Result := MigrationPage.Values[0];
end;

function ShouldBootstrapCloud(): Boolean;
begin
  Result := (not ExistingInstall) or ShouldCleanReset();
end;

procedure InitializeWizard();
var
  PreviousPageId: Integer;
begin
  ExistingInstall := DetectExistingInstall();
  PreviousPageId := wpSelectDir;

  if ExistingInstall then
  begin
    MigrationPage := CreateInputOptionPage(
      wpSelectDir,
      'Instalación anterior detectada',
      'Actualizar RackNova Local de forma segura',
      'Para esta transición a la versión definitiva se recomienda reconstruir la base Local desde el estado actual de Cloud. Antes de hacerlo, el instalador conserva un respaldo completo de la configuración y del cluster PostgreSQL anterior.',
      False,
      False
    );
    MigrationPage.Add(
      'Crear respaldo y reconstruir la base Local desde RackNova Cloud (recomendado)'
    );
    MigrationPage.Values[0] := True;
    PreviousPageId := MigrationPage.ID;
  end;

  CloudConfigPage := CreateInputQueryPage(
    PreviousPageId,
    'Conexión con RackNova Cloud',
    'Activa este equipo durante la instalación',
    'Introduce los datos de RackNova Cloud. El Sync Secret se oculta, no se escribe en el log del instalador y se guarda después protegido por Windows DPAPI.'
  );
  CloudConfigPage.Add('URL de RackNova Cloud:', False);
  CloudConfigPage.Values[0] := 'https://racknova-backend-1.onrender.com';
  CloudConfigPage.Add('ID de empresa:', False);
  CloudConfigPage.Values[1] := '11111111-1111-4111-8111-111111111111';
  CloudConfigPage.Add('Sync Secret de RackNova:', True);
  CloudConfigPage.Values[2] := '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  CloudUrl: String;
  EmpresaId: String;
  SyncSecret: String;
begin
  Result := True;

  if CurPageID = CloudConfigPage.ID then
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

    if (Length(EmpresaId) < 32) or (Pos('-', EmpresaId) = 0) then
    begin
      MsgBox('El ID de empresa no parece un UUID válido.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if Length(SyncSecret) < 20 then
    begin
      MsgBox('El Sync Secret debe tener al menos 20 caracteres.', mbError, MB_OK);
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

procedure StopServiceForUpgrade(ServiceName: String);
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'stop ' + ServiceName,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;

  if ExistingInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Deteniendo servicios de RackNova...';
    StopServiceForUpgrade('RackNovaLocal');
    StopServiceForUpgrade('RackNovaPostgreSQL16');
    Sleep(5000);
  end;
end;

procedure RunPowerShellScript(
  ScriptPath: String;
  InstallDir: String;
  var ResultCode: Integer
);
var
  PowerShellExe: String;
  Args: String;
begin
  PowerShellExe := ExpandConstant(
    '{sys}\WindowsPowerShell\v1.0\powershell.exe'
  );
  Args :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath +
    '" -InstallDir "' + InstallDir + '"';

  if not Exec(
    PowerShellExe,
    Args,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    RaiseException('No fue posible iniciar un componente interno de instalación.');
end;

procedure RunCloudActivation(
  InstallDir: String;
  CloudUrl: String;
  EmpresaId: String;
  var ResultCode: Integer
);
var
  PowerShellExe: String;
  ScriptPath: String;
  Args: String;
begin
  PowerShellExe := ExpandConstant(
    '{sys}\WindowsPowerShell\v1.0\powershell.exe'
  );
  ScriptPath := ExpandConstant('{app}\installer\cloud_link.ps1');
  Args :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath +
    '" -Mode Activate -InstallDir "' + InstallDir +
    '" -CloudUrl "' + CloudUrl +
    '" -EmpresaId "' + EmpresaId + '"';

  if not Exec(
    PowerShellExe,
    Args,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    RaiseException('No fue posible iniciar la activación de RackNova Cloud.');
end;

procedure RestartRackNovaLocal();
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'stop RackNovaLocal',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Sleep(2500);
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'start RackNovaLocal',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
end;

function WaitForRackNovaHealth(InstallDir: String): Boolean;
var
  Attempt: Integer;
  ResultCode: Integer;
begin
  Result := False;
  for Attempt := 1 to 12 do
  begin
    Sleep(2500);
    if Exec(
      InstallDir + '\RackNovaCtl.exe',
      'health',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) and (ResultCode = 0) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  BootstrapResultCode: Integer;
  InstallDir: String;
  CloudUrl: String;
  EmpresaId: String;
  SyncSecret: String;
  ResetRequested: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    InstallDir := ExpandConstant('{app}');
    ResetRequested := ShouldCleanReset();

    if ResetRequested then
    begin
      WizardForm.StatusLabel.Caption := 'Respaldando la instalación anterior...';
      RunPowerShellScript(
        ExpandConstant('{app}\installer\reset_definitive.ps1'),
        InstallDir,
        ResultCode
      );
      if ResultCode <> 0 then
        RaiseException(
          'No pude respaldar y preparar la instalación anterior. No se eliminó el respaldo. Revisa C:\ProgramData\RackNova\Logs.'
        );
    end;

    WizardForm.StatusLabel.Caption := 'Configurando RackNova Local...';
    RunPowerShellScript(
      ExpandConstant('{app}\installer\configure_install_entry.ps1'),
      InstallDir,
      ResultCode
    );
    if ResultCode <> 0 then
      RaiseException(
        'RackNova no pudo completar la configuración local. Código: ' +
        IntToStr(ResultCode) + '. Revisa C:\ProgramData\RackNova\Logs.'
      );

    // El smoke test del workflow usa /VERYSILENT y valida únicamente el runtime.
    // La activación Cloud siempre se realiza en el asistente interactivo normal.
    if not WizardSilent then
    begin
      CloudUrl := Trim(CloudConfigPage.Values[0]);
      EmpresaId := Trim(CloudConfigPage.Values[1]);
      SyncSecret := Trim(CloudConfigPage.Values[2]);

      WizardForm.StatusLabel.Caption := 'Activando RackNova Cloud...';
      if not SetEnvironmentVariable('RACKNOVA_INSTALL_SYNC_SECRET', SyncSecret) then
        RaiseException('No pude preparar de forma segura la credencial Cloud.');

      try
        RunCloudActivation(
          InstallDir,
          CloudUrl,
          EmpresaId,
          ResultCode
        );
      finally
        SetEnvironmentVariable('RACKNOVA_INSTALL_SYNC_SECRET', '');
      end;

      if ResultCode <> 0 then
        RaiseException(
          'La instalación local terminó, pero RackNova Cloud rechazó la activación. Revisa los datos ingresados y C:\ProgramData\RackNova\Logs.'
        );

      if ShouldBootstrapCloud() then
      begin
        WizardForm.StatusLabel.Caption := 'Descargando estado actual desde RackNova Cloud...';
        StopServiceForUpgrade('RackNovaLocal');
        Sleep(5000);

        RunPowerShellScript(
          ExpandConstant('{app}\installer\bootstrap_cloud_snapshot.ps1'),
          InstallDir,
          BootstrapResultCode
        );

        RestartRackNovaLocal();

        if BootstrapResultCode <> 0 then
          RaiseException(
            'RackNova quedó activado, pero no pude importar el snapshot actual de Cloud. El respaldo anterior permanece en C:\ProgramData\RackNova\Backups.'
          );
      end
      else
      begin
        RestartRackNovaLocal();
      end;

      WizardForm.StatusLabel.Caption := 'Verificando RackNova Local...';
      if not WaitForRackNovaHealth(InstallDir) then
        RaiseException(
          'La instalación terminó, pero RackNova Local no respondió correctamente al chequeo final. Revisa C:\ProgramData\RackNova\Logs.'
        );
    end;
  end;
end;
