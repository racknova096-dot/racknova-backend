#define MyAppName "RackNova Local"
#define MyAppVersion "1.0.4"
#define MyAppPublisher "RackNova"

[Setup]
AppId={{A6464E3C-1357-4E4A-B5D0-5ED3ED9441F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RackNova
DefaultGroupName=RackNova
OutputDir=output
OutputBaseFilename=RackNova_Setup_F1_9_4
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
Source: "configure_install_recovery.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
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
  RecoveryFailed: Boolean;

function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
external 'SetEnvironmentVariableW@Kernel32.dll stdcall delayload';

procedure InitInstallerEntryLog();
var
  LogDir: String;
begin
  LogDir := ExpandConstant('{commonappdata}\RackNova\Logs');
  ForceDirectories(LogDir);
  InstallerEntryLog := LogDir + '\setup-recovery-entry-' +
    GetDateTimeString('yyyymmdd_hhnnss', '-', ':') + '.log';
  SaveStringToFile(
    InstallerEntryLog,
    '[START] RackNova F1.9.3 Recovery setup iniciado.' + #13#10,
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
      'Instalación existente: se conservará PostgreSQL si está sano y se reconstruirá únicamente si está roto, siempre con respaldo previo.'
    );
  end
  else
  begin
    WriteInstallerEntryLog('Instalación nueva detectada.');

    if WizardSilent then
    begin
      WriteInstallerEntryLog(
        'Modo silencioso: se omite captura/validación Cloud; continuará la instalación local sin activación interactiva.'
      );
    end
    else
    begin
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
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  CloudUrl: String;
  EmpresaId: String;
  SyncSecret: String;
begin
  Result := True;

  if WizardSilent then
    Exit;

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

function StopRackNovaRuntimeForUpgrade(): Boolean;
var
  ResultCode: Integer;
  PowerShellExe: String;
  PgRoot: String;
  Args: String;
begin
  Result := True;
  WriteInstallerEntryLog('Deteniendo RackNovaLocal y RackNovaPostgreSQL16 antes de reemplazar binarios.');

  StopServiceForUpgrade('RackNovaLocal');
  StopServiceForUpgrade('RackNovaPostgreSQL16');

  PowerShellExe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  PgRoot := ExpandConstant('{app}\PostgreSQL\');
  StringChangeEx(PgRoot, '''', '''''', True);

  Args :=
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
    '$ErrorActionPreference=''SilentlyContinue''; ' +
    '$root=''' + PgRoot + '''; ' +
    '$names=@(''RackNovaLocal'',''RackNovaPostgreSQL16''); ' +
    'foreach($n in $names){$s=Get-Service -Name $n -ErrorAction SilentlyContinue; if($s -and $s.Status -ne ''Stopped''){Stop-Service -Name $n -Force -ErrorAction SilentlyContinue; try{$s.WaitForStatus(''Stopped'',[TimeSpan]::FromSeconds(30))}catch{}}}; ' +
    'for($i=0;$i -lt 20;$i++){ ' +
      '$procs=@(Get-Process -Name postgres -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and $_.Path.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) } catch { $false } }); ' +
      'if($procs.Count -gt 0){$procs | Stop-Process -Force -ErrorAction SilentlyContinue}; ' +
      '$running=@($names | ForEach-Object { Get-Service -Name $_ -ErrorAction SilentlyContinue } | Where-Object { $_.Status -ne ''Stopped'' }); ' +
      'if($procs.Count -eq 0 -and $running.Count -eq 0){break}; ' +
      'Start-Sleep -Milliseconds 500 ' +
    '}; ' +
    'Start-Sleep -Milliseconds 1200; ' +
    '$left=@(Get-Process -Name postgres -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and $_.Path.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) } catch { $false } }); ' +
    '$leftSvc=@($names | ForEach-Object { Get-Service -Name $_ -ErrorAction SilentlyContinue } | Where-Object { $_.Status -ne ''Stopped'' }); ' +
    'if($left.Count -gt 0 -or $leftSvc.Count -gt 0){exit 32}else{exit 0}"';

  if not Exec(
    PowerShellExe,
    Args,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    WriteInstallerEntryLog('ERROR: no pude ejecutar preflight PowerShell.');
    Result := False;
    Exit;
  end;

  if ResultCode <> 0 then
  begin
    WriteInstallerEntryLog('ERROR: RackNova no liberó PostgreSQL/servicios antes de actualizar. Código=' + IntToStr(ResultCode));
    Result := False;
  end
  else
    WriteInstallerEntryLog('Servicios y procesos PostgreSQL liberados correctamente.');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  WriteInstallerEntryLog('PrepareToInstall iniciado.');

  if ExistingInstall then
  begin
    WizardForm.StatusLabel.Caption :=
      'Preparando diagnóstico y recuperación de PostgreSQL...';

    if not StopRackNovaRuntimeForUpgrade() then
    begin
      Result :=
        'No fue posible liberar PostgreSQL para una reparación segura. ' +
        'Reinicia Windows y vuelve a ejecutar el instalador como administrador.';
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
  PowerShellExe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
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
    RaiseException('No fue posible iniciar el componente interno de recuperación.');
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
  PowerShellExe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
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
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop RackNovaLocal', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(2500);
  Exec(ExpandConstant('{sys}\sc.exe'), 'start RackNovaLocal', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function WaitForRackNovaHealth(InstallDir: String): Boolean;
var
  Attempt: Integer;
  ResultCode: Integer;
begin
  Result := False;
  for Attempt := 1 to 24 do
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
  if CurStep <> ssPostInstall then
    Exit;

  InstallDir := ExpandConstant('{app}');
  WriteInstallerEntryLog('Archivos copiados. Ejecutando recuperación estructural F1.9.3.');
  WizardForm.StatusLabel.Caption := 'Validando y recuperando PostgreSQL de RackNova...';

  RunPowerShellScript(
    ExpandConstant('{app}\installer\configure_install_recovery.ps1'),
    InstallDir,
    ResultCode
  );

  if ResultCode <> 0 then
  begin
    RecoveryFailed := True;
    WriteInstallerEntryLog('ERROR: recuperación terminó con código ' + IntToStr(ResultCode) + '.');
    RaiseException(
      'RackNova no pudo completar la recuperación. Código: ' + IntToStr(ResultCode) +
      '. Revisa C:\ProgramData\RackNova\Logs y C:\ProgramData\RackNova\Backups.'
    );
  end;

  WriteInstallerEntryLog('Configuración/recuperación local completada.');

  if (not ExistingInstall) and (not WizardSilent) then
  begin
    CloudUrl := Trim(CloudConfigPage.Values[0]);
    EmpresaId := Trim(CloudConfigPage.Values[1]);
    SyncSecret := Trim(CloudConfigPage.Values[2]);

    WizardForm.StatusLabel.Caption := 'Activando RackNova Cloud...';
    if not SetEnvironmentVariable('RACKNOVA_INSTALL_SYNC_SECRET', SyncSecret) then
      RaiseException('No pude preparar de forma segura la credencial Cloud.');

    try
      RunCloudActivation(InstallDir, CloudUrl, EmpresaId, ResultCode);
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
    Sleep(3500);
    RunPowerShellScript(
      ExpandConstant('{app}\installer\bootstrap_cloud_snapshot.ps1'),
      InstallDir,
      BootstrapResultCode
    );
    RestartRackNovaLocal();

    if BootstrapResultCode <> 0 then
      WriteInstallerEntryLog('AVISO: bootstrap Cloud no terminó correctamente; Sync reintentará después.');
  end;

  WizardForm.StatusLabel.Caption := 'Verificando RackNova Local...';
  if not WaitForRackNovaHealth(InstallDir) then
  begin
    RecoveryFailed := True;
    WriteInstallerEntryLog('ERROR: health final no respondió.');
    RaiseException(
      'La recuperación terminó, pero RackNova Local no respondió correctamente. ' +
      'Revisa C:\ProgramData\RackNova\Logs.'
    );
  end;

  WriteInstallerEntryLog('RackNova F1.9.3 verificado correctamente.');
end;

function GetCustomSetupExitCode(): Integer;
begin
  if RecoveryFailed then
    Result := 5
  else
    Result := 0;
end;
