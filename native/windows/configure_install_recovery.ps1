param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$BackupsDir = Join-Path $ProgramDataRoot "Backups"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$PgData = Join-Path $PgRoot "data"
$PgLogs = Join-Path $PgRoot "logs"

$Ctl = Join-Path $InstallDir "RackNovaCtl.exe"
$BaseConfigure = Join-Path $InstallDir "installer\configure_install.ps1"
$BootstrapCloud = Join-Path $InstallDir "installer\bootstrap_cloud_snapshot.ps1"
$PgBin = Join-Path $InstallDir "PostgreSQL\bin"
$InitDb = Join-Path $PgBin "initdb.exe"
$PgCtl = Join-Path $PgBin "pg_ctl.exe"
$PgIsReady = Join-Path $PgBin "pg_isready.exe"
$Psql = Join-Path $PgBin "psql.exe"
$Createdb = Join-Path $PgBin "createdb.exe"

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Log = Join-Path $LogDir ("recovery-install-" + $Stamp + ".log")
$BackupRoot = Join-Path $BackupsDir ("Recovery-" + $Stamp)
$BackupConfig = Join-Path $BackupRoot "Config"
$BackupPgData = Join-Path $BackupRoot "PostgreSQL-data"
$PreflightBackup = Join-Path $BackupRoot "Preflight"
$DidRebuild = $false

New-Item -ItemType Directory -Force -Path `
    $ProgramDataRoot, $ConfigDir, $LogDir, $BackupsDir, $PgRoot, $PgLogs | Out-Null

function Write-RecoveryLog([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $Line | Tee-Object -FilePath $Log -Append | Write-Host
}

function Copy-Tree {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $Output = (& robocopy.exe `
        $Source `
        $Destination `
        /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ /NP /NFL /NDL /NJH /NJS 2>&1 | Out-String).Trim()
    $Code = $LASTEXITCODE

    if ($Output) {
        Write-RecoveryLog ("ROBOCOPY: " + ($Output -replace "`r?`n", " | "))
    }

    if ($Code -gt 7) {
        throw "Falló el respaldo de $Source. Robocopy=$Code"
    }
}

function Read-RackNovaConfig {
    $Path = Join-Path $ConfigDir "config.json"
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    return (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json)
}

function Read-RackNovaSecrets {
    $Path = Join-Path $ConfigDir "secrets.dat"
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    $Encoded = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop).Trim()
    if (-not $Encoded) {
        return $null
    }

    $Encrypted = [Convert]::FromBase64String($Encoded)
    $Raw = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $Encrypted,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $Json = [System.Text.Encoding]::UTF8.GetString($Raw)
    return ($Json | ConvertFrom-Json)
}

function Find-LatestDefinitiveResetBackup {
    if (-not (Test-Path -LiteralPath $BackupsDir)) {
        return $null
    }

    $Candidates = Get-ChildItem -LiteralPath $BackupsDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "DefinitiveReset-*" } |
        Sort-Object LastWriteTimeUtc -Descending

    foreach ($Candidate in $Candidates) {
        $Cfg = Join-Path $Candidate.FullName "Config"
        $Pg = Join-Path $Candidate.FullName "PostgreSQL-data"
        $HasCfg = (Test-Path (Join-Path $Cfg "config.json")) -and
                  (Test-Path (Join-Path $Cfg "secrets.dat"))
        $HasPg = Test-Path (Join-Path $Pg "PG_VERSION")
        if ($HasCfg -or $HasPg) {
            return $Candidate.FullName
        }
    }
    return $null
}

function Recover-InterruptedResetIfNeeded {
    $CfgPath = Join-Path $ConfigDir "config.json"
    $SecretsPath = Join-Path $ConfigDir "secrets.dat"
    $PgVersion = Join-Path $PgData "PG_VERSION"

    $NeedCfg = (-not (Test-Path $CfgPath)) -or (-not (Test-Path $SecretsPath))
    $NeedPg = -not (Test-Path $PgVersion)

    if (-not $NeedCfg -and -not $NeedPg) {
        return
    }

    $Source = Find-LatestDefinitiveResetBackup
    if (-not $Source) {
        return
    }

    Write-RecoveryLog ("Detecté recuperación incompleta. Respaldo candidato: " + $Source)

    if ($NeedCfg) {
        $SourceCfg = Join-Path $Source "Config"
        if ((Test-Path (Join-Path $SourceCfg "config.json")) -and
            (Test-Path (Join-Path $SourceCfg "secrets.dat"))) {
            Write-RecoveryLog "Restaurando Config desde DefinitiveReset antes del diagnóstico."
            Copy-Tree -Source $SourceCfg -Destination $ConfigDir
        }
    }

    if ($NeedPg) {
        $SourcePg = Join-Path $Source "PostgreSQL-data"
        if ((Test-Path (Join-Path $SourcePg "PG_VERSION")) -and
            (-not (Test-Path $PgData) -or @(
                Get-ChildItem -LiteralPath $PgData -Force -ErrorAction SilentlyContinue
            ).Count -eq 0)) {
            Write-RecoveryLog "Restaurando copia del cluster previo para diagnosticarlo."
            Copy-Tree -Source $SourcePg -Destination $PgData
        }
    }
}

function Stop-ServiceQuiet([string]$Name) {
    $Svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($Svc -and $Svc.Status -ne "Stopped") {
        try {
            Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
        Start-Sleep -Seconds 2
    }
}

function Stop-RackNovaRuntime {
    Stop-ServiceQuiet "RackNovaLocal"
    Stop-ServiceQuiet "RackNovaPostgreSQL16"

    if ((Test-Path $PgCtl) -and (Test-Path $PgData)) {
        & $PgCtl stop -D $PgData -m fast -w -t 20 2>$null | Out-Null
    }

    $Root = (Join-Path $InstallDir "PostgreSQL\").ToLowerInvariant()
    Get-Process -Name postgres -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            if ($_.Path -and $_.Path.ToLowerInvariant().StartsWith($Root)) {
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
        catch {
        }
    }
    Start-Sleep -Seconds 1
}

function Backup-ConfigSnapshot {
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    if (Test-Path -LiteralPath $ConfigDir) {
        Copy-Tree -Source $ConfigDir -Destination $BackupConfig
    }
}

function Backup-ClusterBeforeRebuild {
    Backup-ConfigSnapshot
    if (Test-Path -LiteralPath $PgData) {
        Write-RecoveryLog "BACKUP: copiando cluster PostgreSQL antes de reconstruir."
        Copy-Tree -Source $PgData -Destination $BackupPgData
    }

    @"
RackNova Local - respaldo previo a reconstrucción PostgreSQL
Fecha: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Equipo: $env:COMPUTERNAME

El instalador detectó que PostgreSQL no podía iniciar o autenticar correctamente.
Se guardó Config y una copia cruda del cluster anterior ANTES de borrar el cluster activo.
Si el cluster estaba corrupto, los cambios locales no sincronizados pueden requerir recuperación manual desde esta copia.
"@ | Set-Content -LiteralPath (Join-Path $BackupRoot "README.txt") -Encoding UTF8

    Write-RecoveryLog ("BACKUP COMPLETO: " + $BackupRoot)
}

function Grant-PostgresAcl {
    if (-not (Test-Path -LiteralPath $PgRoot)) {
        return
    }

    $Output = (& icacls.exe `
        $PgRoot `
        /inheritance:e `
        /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        /T /C 2>&1 | Out-String).Trim()
    $Code = $LASTEXITCODE

    if ($Output) {
        Write-RecoveryLog ("ACL PostgreSQL: " + ($Output -replace "`r?`n", " | "))
    }
    if ($Code -ne 0) {
        throw "No pude reparar ACL de PostgreSQL. Código=$Code"
    }
}

function Delete-PostgresService {
    Stop-ServiceQuiet "RackNovaPostgreSQL16"
    $Svc = Get-Service -Name "RackNovaPostgreSQL16" -ErrorAction SilentlyContinue
    if ($Svc) {
        & sc.exe delete RackNovaPostgreSQL16 | Out-Null
        for ($i = 0; $i -lt 20; $i++) {
            if (-not (Get-Service -Name "RackNovaPostgreSQL16" -ErrorAction SilentlyContinue)) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

function Register-PostgresService {
    Delete-PostgresService

    & $PgCtl register -N "RackNovaPostgreSQL16" -D $PgData -S auto
    if ($LASTEXITCODE -ne 0) {
        throw "pg_ctl register falló con código $LASTEXITCODE"
    }

    & sc.exe config RackNovaPostgreSQL16 obj= LocalSystem start= auto | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No pude configurar RackNovaPostgreSQL16 como LocalSystem."
    }

    & sc.exe failure RackNovaPostgreSQL16 `
        reset= 86400 `
        actions= restart/5000/restart/15000/restart/60000 | Out-Null
    & sc.exe failureflag RackNovaPostgreSQL16 1 | Out-Null
}

function Save-PreflightPostgresConfig {
    New-Item -ItemType Directory -Force -Path $PreflightBackup | Out-Null
    foreach ($Name in @("postgresql.conf", "pg_hba.conf", "postmaster.pid")) {
        $Source = Join-Path $PgData $Name
        if (Test-Path -LiteralPath $Source) {
            Copy-Item -LiteralPath $Source -Destination (Join-Path $PreflightBackup $Name) -Force
        }
    }
}

function Normalize-PostgresConfig {
    if (-not (Test-Path -LiteralPath $PgData)) {
        return
    }

    Save-PreflightPostgresConfig

    $PostmasterPid = Join-Path $PgData "postmaster.pid"
    if (Test-Path -LiteralPath $PostmasterPid) {
        $RunningRackNovaPg = @(Get-Process -Name postgres -ErrorAction SilentlyContinue | Where-Object {
            try {
                $_.Path -and $_.Path.StartsWith(
                    (Join-Path $InstallDir "PostgreSQL\"),
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            }
            catch { $false }
        })
        if ($RunningRackNovaPg.Count -eq 0) {
            Remove-Item -LiteralPath $PostmasterPid -Force -ErrorAction SilentlyContinue
            Write-RecoveryLog "Eliminé postmaster.pid obsoleto con PostgreSQL detenido."
        }
    }

    $PgConfig = Join-Path $PgData "postgresql.conf"
    if (Test-Path -LiteralPath $PgConfig) {
        @"

# RackNova Recovery F1.9.3
listen_addresses = '127.0.0.1'
port = 54329
password_encryption = 'scram-sha-256'
"@ | Add-Content -LiteralPath $PgConfig -Encoding UTF8
    }

    $PgHba = Join-Path $PgData "pg_hba.conf"
    @"
# RackNova Local - recovery normalized
local   all    all                     scram-sha-256
host    all    all    127.0.0.1/32     scram-sha-256
host    all    all    ::1/128          scram-sha-256
"@ | Set-Content -LiteralPath $PgHba -Encoding ASCII
}

function Wait-PostgresReady([int]$Attempts = 30) {
    for ($i = 1; $i -le $Attempts; $i++) {
        & $PgIsReady -h 127.0.0.1 -p 54329 -t 2 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-PostgresForProbe {
    try {
        Register-PostgresService
        Grant-PostgresAcl
        Start-Service RackNovaPostgreSQL16 -ErrorAction Stop
    }
    catch {
        Write-RecoveryLog ("PostgreSQL no pudo iniciar: " + $_.Exception.Message)
        return $false
    }

    if (-not (Wait-PostgresReady -Attempts 20)) {
        Write-RecoveryLog "PostgreSQL arrancó como servicio pero no respondió en 127.0.0.1:54329."
        return $false
    }
    return $true
}

function Invoke-PsqlScalar {
    param(
        [Parameter(Mandatory=$true)][string]$User,
        [Parameter(Mandatory=$true)][string]$Password,
        [Parameter(Mandatory=$true)][string]$Database,
        [Parameter(Mandatory=$true)][string]$Sql
    )

    $Previous = $env:PGPASSWORD
    try {
        $env:PGPASSWORD = $Password
        $Output = & $Psql `
            -h 127.0.0.1 -p 54329 `
            -U $User -d $Database `
            -v ON_ERROR_STOP=1 `
            -tAc $Sql 2>&1
        $Code = $LASTEXITCODE
        return [PSCustomObject]@{
            Code = $Code
            Output = (($Output | Out-String).Trim())
        }
    }
    finally {
        if ($null -eq $Previous) {
            Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
        }
        else {
            $env:PGPASSWORD = $Previous
        }
    }
}

function Repair-RoleAndDatabase($Secrets) {
    $SuperPassword = [string]$Secrets.pg_super_password
    $AppPassword = [string]$Secrets.db_password
    $EscapedAppPassword = $AppPassword.Replace("'", "''")

    $SuperProbe = Invoke-PsqlScalar `
        -User "racknova_super" `
        -Password $SuperPassword `
        -Database "postgres" `
        -Sql "SELECT 1"

    if ($SuperProbe.Code -ne 0 -or $SuperProbe.Output -notmatch "1") {
        Write-RecoveryLog ("Credencial racknova_super no pudo autenticar: " + $SuperProbe.Output)
        return $false
    }

    $RoleSql = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='racknova_app') THEN
    CREATE ROLE racknova_app LOGIN PASSWORD '$EscapedAppPassword';
  ELSE
    ALTER ROLE racknova_app WITH LOGIN PASSWORD '$EscapedAppPassword';
  END IF;
END
`$`$;
"@

    $RoleResult = Invoke-PsqlScalar `
        -User "racknova_super" `
        -Password $SuperPassword `
        -Database "postgres" `
        -Sql $RoleSql
    if ($RoleResult.Code -ne 0) {
        Write-RecoveryLog ("No pude reparar racknova_app: " + $RoleResult.Output)
        return $false
    }

    $DbExists = Invoke-PsqlScalar `
        -User "racknova_super" `
        -Password $SuperPassword `
        -Database "postgres" `
        -Sql "SELECT 1 FROM pg_database WHERE datname='racknova'"

    if ($DbExists.Code -ne 0) {
        return $false
    }

    if ($DbExists.Output -notmatch "1") {
        $Previous = $env:PGPASSWORD
        try {
            $env:PGPASSWORD = $SuperPassword
            & $Createdb `
                -h 127.0.0.1 -p 54329 `
                -U racknova_super `
                -O racknova_app `
                racknova 2>&1 | ForEach-Object {
                    Write-RecoveryLog ("createdb: " + $_)
                }
            if ($LASTEXITCODE -ne 0) {
                return $false
            }
        }
        finally {
            if ($null -eq $Previous) {
                Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
            }
            else {
                $env:PGPASSWORD = $Previous
            }
        }
    }

    return $true
}

function Test-AppDatabase($Secrets) {
    $Result = Invoke-PsqlScalar `
        -User "racknova_app" `
        -Password ([string]$Secrets.db_password) `
        -Database "racknova" `
        -Sql "SELECT 1"

    if ($Result.Code -eq 0 -and $Result.Output -match "1") {
        return $true
    }

    Write-RecoveryLog ("Prueba racknova_app falló: " + $Result.Output)
    return $false
}

function Try-RepairExistingCluster($Secrets) {
    $VersionPath = Join-Path $PgData "PG_VERSION"
    if (-not (Test-Path -LiteralPath $VersionPath)) {
        Write-RecoveryLog "No existe PG_VERSION en el cluster activo."
        return $false
    }

    $Version = (Get-Content -LiteralPath $VersionPath -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not $Version.StartsWith("16")) {
        Write-RecoveryLog ("PG_VERSION no es PostgreSQL 16: '" + $Version + "'.")
        return $false
    }

    Normalize-PostgresConfig
    Grant-PostgresAcl

    if (-not (Start-PostgresForProbe)) {
        return $false
    }

    if (Test-AppDatabase -Secrets $Secrets) {
        Write-RecoveryLog "CLUSTER CONSERVADO: PostgreSQL 16 y racknova_app responden correctamente."
        return $true
    }

    Write-RecoveryLog "El servidor arrancó; intentaré reparar rol/base con racknova_super."
    if ((Repair-RoleAndDatabase -Secrets $Secrets) -and (Test-AppDatabase -Secrets $Secrets)) {
        Write-RecoveryLog "CLUSTER CONSERVADO: credenciales/rol/base reparados sin reconstruir datos."
        return $true
    }

    Write-RecoveryLog "El cluster arrancó, pero no fue posible recuperar acceso lógico de RackNova."
    return $false
}

function Initialize-FreshClusterWithExistingSecrets($Secrets) {
    $SuperPassword = [string]$Secrets.pg_super_password
    $AppPassword = [string]$Secrets.db_password

    if (-not $SuperPassword -or -not $AppPassword) {
        throw "secrets.dat no contiene las credenciales PostgreSQL requeridas."
    }

    Delete-PostgresService

    if (Test-Path -LiteralPath $PgData) {
        Remove-Item -LiteralPath $PgData -Recurse -Force -ErrorAction Stop
    }
    New-Item -ItemType Directory -Force -Path $PgData | Out-Null

    $PwFile = Join-Path $BackupRoot "postgres-super.recovery.tmp"
    Set-Content -LiteralPath $PwFile -Value $SuperPassword -Encoding ASCII -NoNewline
    & icacls.exe $PwFile /inheritance:r /grant:r `
        "*S-1-5-18:F" "*S-1-5-32-544:F" | Out-Null

    try {
        Write-RecoveryLog "RECONSTRUCCION: ejecutando initdb PostgreSQL 16."
        & $InitDb `
            -D $PgData `
            -U racknova_super `
            -E UTF8 `
            --locale=C `
            --auth=scram-sha-256 `
            --pwfile=$PwFile `
            --no-instructions 2>&1 | ForEach-Object {
                Write-RecoveryLog ("initdb: " + $_)
            }
        if ($LASTEXITCODE -ne 0) {
            throw "initdb terminó con código $LASTEXITCODE"
        }
    }
    finally {
        Remove-Item -LiteralPath $PwFile -Force -ErrorAction SilentlyContinue
    }

    Normalize-PostgresConfig
    Grant-PostgresAcl
    Register-PostgresService

    Start-Service RackNovaPostgreSQL16 -ErrorAction Stop
    if (-not (Wait-PostgresReady -Attempts 30)) {
        throw "PostgreSQL reconstruido no respondió en 127.0.0.1:54329."
    }

    if (-not (Repair-RoleAndDatabase -Secrets $Secrets)) {
        throw "No pude crear racknova_app/racknova tras reconstruir PostgreSQL."
    }

    if (-not (Test-AppDatabase -Secrets $Secrets)) {
        throw "La base reconstruida no acepta las credenciales RackNova preservadas."
    }

    $script:DidRebuild = $true
    Write-RecoveryLog "RECONSTRUCCION COMPLETA: PostgreSQL limpio operativo con credenciales preservadas."
}

function Invoke-BaseConfigure {
    if (-not (Test-Path -LiteralPath $BaseConfigure)) {
        throw "No existe configure_install.ps1"
    }

    Write-RecoveryLog "Ejecutando configuración base RackNova sobre PostgreSQL validado."
    $Output = & powershell.exe `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy Bypass `
        -File $BaseConfigure `
        -InstallDir $InstallDir 2>&1
    $Code = $LASTEXITCODE

    foreach ($Line in $Output) {
        if ($null -ne $Line -and ([string]$Line).Trim()) {
            Write-RecoveryLog ("BASE: " + [string]$Line)
        }
    }

    if ($Code -ne 0) {
        throw "configure_install.ps1 terminó con código $Code."
    }
}

function Try-BootstrapCloudAfterRebuild {
    if (-not $DidRebuild) {
        return
    }

    try {
        $Cfg = Read-RackNovaConfig
        if (-not $Cfg -or -not [bool]$Cfg.activated -or -not ([string]$Cfg.cloud_url).Trim()) {
            Write-RecoveryLog "Cloud bootstrap omitido: instalación no activada o sin cloud_url."
            return
        }

        if (-not (Test-Path -LiteralPath $BootstrapCloud)) {
            Write-RecoveryLog "Cloud bootstrap omitido: falta bootstrap_cloud_snapshot.ps1."
            return
        }

        Write-RecoveryLog "PostgreSQL fue reconstruido; intentando restaurar snapshot desde RackNova Cloud."
        Stop-ServiceQuiet "RackNovaLocal"
        $Output = & powershell.exe `
            -NoProfile `
            -NonInteractive `
            -ExecutionPolicy Bypass `
            -File $BootstrapCloud `
            -InstallDir $InstallDir 2>&1
        $Code = $LASTEXITCODE
        foreach ($Line in $Output) {
            if ($null -ne $Line -and ([string]$Line).Trim()) {
                Write-RecoveryLog ("CLOUD: " + [string]$Line)
            }
        }
        if ($Code -ne 0) {
            Write-RecoveryLog "AVISO: bootstrap Cloud no terminó correctamente; RackNova Sync podrá reintentar después."
        }
    }
    catch {
        Write-RecoveryLog ("AVISO: no pude completar bootstrap Cloud: " + $_.Exception.Message)
    }
    finally {
        try { Start-Service RackNovaLocal -ErrorAction SilentlyContinue } catch {}
    }
}

$ExitCode = 1
try {
    Write-RecoveryLog "RackNova F1.9.3 Recovery iniciado."
    Write-RecoveryLog ("InstallDir=" + $InstallDir)
    Write-RecoveryLog "POLITICA: respaldo antes de cualquier reconstrucción; conservar cluster sano; reconstruir cluster roto."

    foreach ($Required in @($Ctl, $BaseConfigure, $InitDb, $PgCtl, $PgIsReady, $Psql, $Createdb)) {
        if (-not (Test-Path -LiteralPath $Required)) {
            throw "Falta componente requerido: $Required"
        }
    }

    $Existing = `
        (Test-Path (Join-Path $ConfigDir "config.json")) -or `
        (Test-Path (Join-Path $ConfigDir "secrets.dat")) -or `
        (Test-Path $PgData) -or `
        [bool](Get-Service -Name "RackNovaPostgreSQL16" -ErrorAction SilentlyContinue) -or `
        [bool](Get-Service -Name "RackNovaLocal" -ErrorAction SilentlyContinue)

    if (-not $Existing) {
        Write-RecoveryLog "Instalación nueva: delegando al configurador base."
        Invoke-BaseConfigure
        Write-RecoveryLog "Instalación nueva completada."
        exit 0
    }

    Stop-RackNovaRuntime
    Recover-InterruptedResetIfNeeded
    Backup-ConfigSnapshot

    $Config = Read-RackNovaConfig
    $Secrets = Read-RackNovaSecrets

    if (-not $Secrets) {
        $HasMeaningfulConfig = $null -ne $Config
        if ($HasMeaningfulConfig) {
            throw (
                "Existe configuración RackNova pero secrets.dat no puede recuperarse. " +
                "No reconstruiré PostgreSQL sin preservar credenciales. Revisa " + $BackupRoot
            )
        }

        Write-RecoveryLog "No hay secretos/configuración utilizables; trataré restos PostgreSQL como instalación huérfana."
        if (Test-Path $PgData) {
            Backup-ClusterBeforeRebuild
            Delete-PostgresService
            Remove-Item -LiteralPath $PgData -Recurse -Force -ErrorAction SilentlyContinue
        }
        Invoke-BaseConfigure
        Write-RecoveryLog "Instalación huérfana reconstruida como instalación nueva."
        exit 0
    }

    $Healthy = Try-RepairExistingCluster -Secrets $Secrets
    if (-not $Healthy) {
        Stop-RackNovaRuntime
        Backup-ClusterBeforeRebuild
        Initialize-FreshClusterWithExistingSecrets -Secrets $Secrets
    }

    Invoke-BaseConfigure
    Try-BootstrapCloudAfterRebuild

    Write-RecoveryLog (
        "RESULTADO: PostgreSQL=" + $(if ($DidRebuild) { "RECONSTRUIDO" } else { "CONSERVADO" })
    )
    Write-RecoveryLog ("BACKUP=" + $BackupRoot)
    Write-RecoveryLog "RackNova F1.9.3 Recovery completado correctamente."
    $ExitCode = 0
}
catch {
    try {
        Write-RecoveryLog ("ERROR: " + $_.Exception.Message)
        if ($_.ScriptStackTrace) {
            Write-RecoveryLog ("STACK: " + ($_.ScriptStackTrace -replace "`r?`n", " | "))
        }
        Write-RecoveryLog ("BACKUP DISPONIBLE: " + $BackupRoot)

        try {
            $Diag = & $Ctl diagnose 2>&1
            foreach ($Line in $Diag) {
                if ($null -ne $Line -and ([string]$Line).Trim()) {
                    Write-RecoveryLog ("DIAG: " + [string]$Line)
                }
            }
        }
        catch {
        }
    }
    catch {
    }
    $ExitCode = 1
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

exit $ExitCode
