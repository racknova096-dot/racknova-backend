#define MyAppName "RackNova Local"
#define MyAppVersion "1.0.2"
#define MyAppPublisher "RackNova"

[Setup]
AppId={{A6464E3C-1357-4E4A-B5D0-5ED3ED9441F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RackNova
DefaultGroupName=RackNova
OutputDir=output
OutputBaseFilename=RackNova_Setup_SafeRepair_F1_9_2
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
Source: "configure_install_safe_entry.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "cloud_link.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "recover_legacy_cloud_link.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
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
  ExistingInstall: Boolean;
  InstallerEntryLog: String;

function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
external 'SetEnvironmentVariableW@Kernel32.dll stdcall delayload';

procedure InitInstallerEntryLog();
var
  LogDir: String;
begin
  LogDir := ExpandConstant('{commonappdata}\RackNova\Logs');
  ForceDirectories(LogDir);
  InstallerEntryLog := LogDir + '\setup-entry-' +
    GetDateTimeString('yyyymmdd_hhnnss', '-', ':') + '.log';
  SaveStringToFile(
    InstallerEntryLog,
    '[START] RackNova F1.9.2 SAFE setup iniciado.' + #13#10,
    False
  );
end;

procedure WriteInstallerEntryLog(MessageText: String);
begin
  if InstallerEntryLog = '' then
    InitInstallerEntryLog();

  SaveStringToFile(
    InstallerEntryLog,
    '[' + GetDateTimeString('hh:nn:ss', '-', ':') + '] ' +
      MessageText + #13#10,
    True
  );
end;

function DetectExistingInstall(): Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{commonappdata}\RackNova\Config\config.json')) or
    FileExists(ExpandConstant('{commonappdata}\RackNova\Config\secrets.dat')) or
    DirExists(ExpandConstant('{commonappdata}\RackNova\PostgreSQL\data')) or
    FileExists(ExpandConstant('{autopf}\RackNova\RackNovaLocalService.exe'));
end;

procedure InitializeWizard();
begin
  ExistingInstall := DetectExistingInstall();

  if ExistingInstall then
  begin
    WriteInstallerEntryLog(
      'Instalación existente detectada. Modo reparación segura: no se reconstruirá PostgreSQL.'
    );
  end
  else
  begin
    WriteInstallerEntryLog('Instalación nueva detectada.');

    CloudConfigPage := CreateInputQueryPage(
      wpSelectDir,
      'Conexión con RackNova Cloud',
      'Activa este equipo durante la instalación',
      'Introduce los datos de RackNova Cloud. El Sync Secret se oculta y se guarda protegido por Windows DPAPI.'
    );
    CloudConfigPage.Add('URL de RackNova Cloud:', False);
    CloudConfigPage.Values[0] := 'https://racknova-backend-1.onrender.com';
    CloudConfigPage.Add('ID de empresa:', False);
    CloudConfigPage.Values[1] := '11111111-1111-4111-8111-111111111111';
    CloudConfigPage.Add('Sync Secret de RackNova:', True);
    CloudConfigPage.Values[2] := '';
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  CloudUrl: String;
  EmpresaId: String;
  SyncSecret: String;
begin
  Result := True;

  if (not ExistingInstall) and (CurPageID = CloudConfigPage.ID) then
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
  InitInstallerEntryLog();
  WriteInstallerEntryLog('Validando Windows x64.');

  Result := True;
  if not IsWin64 then
  begin
    WriteInstallerEntryLog('ERROR: Windows no es x64.');
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

procedure ForceCloseRackNovaPostgres(var ResultCode: Integer);
var
  PowerShellExe: String;
  PgRoot: String;
  Args: String;
begin
  PowerShellExe := ExpandConstant(
    '{sys}\WindowsPowerShell\v1.0\powershell.exe'
  );
  PgRoot := ExpandConstant('{app}\PostgreSQL\');
  StringChangeEx(PgRoot, '''', '''''', True);

  Args :=
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
    '$root=''' + PgRoot + '''; ' +
    '$procs=@(Get-Process -Name postgres -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) }); ' +
    'if($procs.Count -gt 0){$procs | Stop-Process -Force -ErrorAction SilentlyContinue}; ' +
    'Start-Sleep -Milliseconds 1500; ' +
    '$left=@(Get-Process -Name postgres -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) }); ' +
    'if($left.Count -gt 0){exit 32}else{exit 0}"';

  if not Exec(
    PowerShellExe,
    Args,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    ResultCode := 33;
end;

function StopRackNovaRuntimeForUpgrade(): Boolean;
var
  ResultCode: Integer;
  PgCtlResultCode: Integer;
  ForceResultCode: Integer;
  PgCtl: String;
  DataDir: String;
begin
  Result := True;

  WriteInstallerEntryLog('Deteniendo únicamente RackNovaLocal y RackNovaPostgreSQL16.');
  StopServiceForUpgrade('RackNovaLocal');
  StopServiceForUpgrade('RackNovaPostgreSQL16');

  PgCtl := ExpandConstant('{app}\PostgreSQL\bin\pg_ctl.exe');
  DataDir := ExpandConstant('{commonappdata}\RackNova\PostgreSQL\data');

  if FileExists(PgCtl) and DirExists(DataDir) then
  begin
    Exec(
      PgCtl,
      'stop -D "' + DataDir + '" -m fast -w -t 45',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      PgCtlResultCode
    );
  end;

  Sleep(1500);

  ForceCloseRackNovaPostgres(ForceResultCode);
  if ForceResultCode <> 0 then
  begin
    WriteInstallerEntryLog(
      'ERROR: quedaron procesos postgres.exe pertenecientes a RackNova. Código=' +
      IntToStr(ForceResultCode)
    );
    Result := False;
    Exit;
  end;

  Exec(
    ExpandConstant('{sys}\taskkill.exe'),
    '/F /T /FI "SERVICES eq RackNovaLocal"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );

  Sleep(1000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  WriteInstallerEntryLog('PrepareToInstall iniciado.');

  if ExistingInstall then
  begin
    WizardForm.StatusLabel.Caption :=
      'Preparando reparación segura sin borrar PostgreSQL...';

    if not StopRackNovaRuntimeForUpgrade() then
    begin
      Result :=
        'No fue posible liberar los archivos de PostgreSQL de RackNova. ' +
        'El instalador no modificó la base de datos. Reinicia Windows y vuelve a ejecutar este instalador como administrador.';
      Exit;
    end;
  end;

  WriteInstallerEntryLog('Preflight completado; puede comenzar la copia de archivos.');
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
  for Attempt := 1 to 16 do
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
begin
  if CurStep = ssPostInstall then
  begin
    InstallDir := ExpandConstant('{app}');
    WriteInstallerEntryLog('Archivos copiados. Ejecutando configuración segura.');

    WizardForm.StatusLabel.Caption := 'Configurando RackNova Local de forma segura...';
    RunPowerShellScript(
      ExpandConstant('{app}\installer\configure_install_safe_entry.ps1'),
      InstallDir,
      ResultCode
    );

    if ResultCode <> 0 then
    begin
      WriteInstallerEntryLog(
        'ERROR: configuración segura terminó con código ' +
        IntToStr(ResultCode) + '.'
      );
      RaiseException(
        'RackNova no pudo completar la configuración segura. Código: ' +
        IntToStr(ResultCode) + '. Revisa C:\ProgramData\RackNova\Logs. ' +
        'El instalador no reconstruyó PostgreSQL.'
      );
    end;

    WriteInstallerEntryLog('Configuración local segura completada.');

    if ExistingInstall then
    begin
      WizardForm.StatusLabel.Caption := 'Verificando RackNova Local...';
      if not WaitForRackNovaHealth(InstallDir) then
      begin
        WriteInstallerEntryLog('ERROR: health final no respondió.');
        RaiseException(
          'La reparación terminó, pero RackNova Local no respondió correctamente. ' +
          'Revisa C:\ProgramData\RackNova\Logs. PostgreSQL no fue reconstruido.'
        );
      end;

      WriteInstallerEntryLog('Reparación existente verificada correctamente.');
      Exit;
    end;

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
          'La instalación local terminó, pero RackNova Cloud rechazó la activación. ' +
          'Revisa los datos ingresados y C:\ProgramData\RackNova\Logs.'
        );

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
          'RackNova quedó activado, pero no pude importar el snapshot actual de Cloud. ' +
          'Revisa C:\ProgramData\RackNova\Logs.'
        );
    end;

    WizardForm.StatusLabel.Caption := 'Verificando RackNova Local...';
    if not WaitForRackNovaHealth(InstallDir) then
      RaiseException(
        'La instalación terminó, pero RackNova Local no respondió correctamente al chequeo final. ' +
        'Revisa C:\ProgramData\RackNova\Logs.'
      );

    WriteInstallerEntryLog('Instalación nueva verificada correctamente.');
  end;
end;
