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
$Original = Join-Path $InstallDir "installer\configure_install.ps1"
$Effective = Join-Path $InstallDir "installer\configure_install_safe_effective.ps1"
$PgCtl = Join-Path $InstallDir "PostgreSQL\bin\pg_ctl.exe"

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$EntryLog = Join-Path $LogDir ("safe-install-" + $Stamp + ".log")
$RollbackRoot = Join-Path $BackupsDir ("SafeRepair-" + $Stamp)
$RollbackConfig = Join-Path $RollbackRoot "Config"

New-Item -ItemType Directory -Force -Path `
    $ProgramDataRoot, $ConfigDir, $LogDir, $BackupsDir, $PgRoot | Out-Null

function Write-SafeLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $EntryLog -Append | Write-Host
}

function Normalize-Newlines([string]$Value) {
    if ($null -eq $Value) {
        return ""
    }
    return $Value.Replace("`r`n", "`n").Replace("`r", "`n")
}

function Replace-RequiredText {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Old,
        [Parameter(Mandatory=$true)][string]$New,
        [Parameter(Mandatory=$true)][string]$Description
    )

    $NormalizedSource = Normalize-Newlines $Source
    $NormalizedOld = Normalize-Newlines $Old
    $NormalizedNew = Normalize-Newlines $New

    if (-not $NormalizedSource.Contains($NormalizedOld)) {
        throw "No encontré el bloque esperado: $Description"
    }

    return $NormalizedSource.Replace($NormalizedOld, $NormalizedNew)
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item `
            -LiteralPath $_.FullName `
            -Destination $Destination `
            -Recurse `
            -Force `
            -ErrorAction Stop
    }
}

function Find-LatestDefinitiveResetBackup {
    if (-not (Test-Path -LiteralPath $BackupsDir)) {
        return $null
    }

    $Candidates = Get-ChildItem `
        -LiteralPath $BackupsDir `
        -Directory `
        -Filter "DefinitiveReset-*" `
        -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending

    foreach ($Candidate in $Candidates) {
        $BackupConfig = Join-Path $Candidate.FullName "Config"
        $BackupPg = Join-Path $Candidate.FullName "PostgreSQL-data"

        $HasConfig = Test-Path -LiteralPath (Join-Path $BackupConfig "secrets.dat")
        $HasPg = Test-Path -LiteralPath (Join-Path $BackupPg "PG_VERSION")

        if ($HasConfig -or $HasPg) {
            return $Candidate.FullName
        }
    }

    return $null
}

function Recover-FromInterruptedDefinitiveReset {
    $SecretsPath = Join-Path $ConfigDir "secrets.dat"
    $ConfigPath = Join-Path $ConfigDir "config.json"
    $PgVersionPath = Join-Path $PgData "PG_VERSION"

    $NeedsConfig = `
        (-not (Test-Path -LiteralPath $SecretsPath)) -or `
        (-not (Test-Path -LiteralPath $ConfigPath))

    $PgService = Get-Service `
        -Name "RackNovaPostgreSQL16" `
        -ErrorAction SilentlyContinue

    $NeedsPg = $PgService -and (-not (Test-Path -LiteralPath $PgVersionPath))

    if (-not $NeedsConfig -and -not $NeedsPg) {
        return
    }

    $ResetBackup = Find-LatestDefinitiveResetBackup
    if (-not $ResetBackup) {
        if ($PgService -and $NeedsConfig) {
            throw (
                "Existe RackNovaPostgreSQL16 pero faltan archivos críticos de Config y " +
                "no encontré un respaldo DefinitiveReset utilizable. No tocaré PostgreSQL."
            )
        }
        return
    }

    Write-SafeLog ("Respaldo DefinitiveReset detectado: " + $ResetBackup)

    if ($NeedsConfig) {
        $SourceConfig = Join-Path $ResetBackup "Config"
        if (Test-Path -LiteralPath (Join-Path $SourceConfig "secrets.dat")) {
            Write-SafeLog "Restaurando Config desde el respaldo anterior."
            Copy-DirectoryContents -Source $SourceConfig -Destination $ConfigDir
        }
    }

    if (-not (Test-Path -LiteralPath $PgVersionPath)) {
        $SourcePg = Join-Path $ResetBackup "PostgreSQL-data"
        if (Test-Path -LiteralPath (Join-Path $SourcePg "PG_VERSION")) {
            if (Test-Path -LiteralPath $PgData) {
                $ExistingItems = @(Get-ChildItem -LiteralPath $PgData -Force -ErrorAction SilentlyContinue)
                if ($ExistingItems.Count -gt 0) {
                    throw (
                        "El cluster activo está incompleto pero no está vacío. " +
                        "No lo reemplazaré automáticamente."
                    )
                }
            }

            Write-SafeLog "Restaurando una copia del cluster PostgreSQL desde el respaldo."
            New-Item -ItemType Directory -Force -Path $PgData | Out-Null
            Copy-DirectoryContents -Source $SourcePg -Destination $PgData
        }
    }
}

function Backup-ConfigForRollback {
    New-Item -ItemType Directory -Force -Path $RollbackRoot | Out-Null

    if (Test-Path -LiteralPath $ConfigDir) {
        Copy-DirectoryContents -Source $ConfigDir -Destination $RollbackConfig
    }

    @"
RackNova Local - respaldo previo a reparación segura
Fecha: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Equipo: $env:COMPUTERNAME

Este respaldo contiene únicamente la configuración previa a esta reparación.
El instalador seguro NO mueve, borra ni reinicializa PostgreSQL\data existente.
"@ | Set-Content -LiteralPath (Join-Path $RollbackRoot "README.txt") -Encoding UTF8

    Write-SafeLog ("BACKUP SEGURO: " + $RollbackRoot)
}

function Restore-ConfigRollback {
    if (-not (Test-Path -LiteralPath $RollbackConfig)) {
        return
    }

    $FailedConfig = Join-Path $RollbackRoot "Config-after-failure"
    if (Test-Path -LiteralPath $ConfigDir) {
        try {
            Copy-DirectoryContents -Source $ConfigDir -Destination $FailedConfig
        }
        catch {
            Write-SafeLog ("No pude guardar Config-after-failure: " + $_.Exception.Message)
        }
    }

    Write-SafeLog "Restaurando Config previa porque la configuración falló."
    Remove-Item -LiteralPath $ConfigDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    Copy-DirectoryContents -Source $RollbackConfig -Destination $ConfigDir
}

function Grant-LocalSystemDataAccess {
    if (-not (Test-Path -LiteralPath $PgRoot)) {
        return
    }

    $AclOutput = (& icacls.exe `
        $PgRoot `
        /inheritance:e `
        /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        /T `
        /C 2>&1 | Out-String).Trim()

    $AclExitCode = $LASTEXITCODE

    if ($AclOutput) {
        Write-SafeLog ("POSTGRES DATA ACL: " + ($AclOutput -replace "`r?`n", " | "))
    }

    if ($AclExitCode -ne 0) {
        throw "No pude preparar los permisos del cluster PostgreSQL. Código: $AclExitCode"
    }
}

function Ensure-ExistingPostgresService {
    $PgVersionPath = Join-Path $PgData "PG_VERSION"
    $SecretsPath = Join-Path $ConfigDir "secrets.dat"
    $PgService = Get-Service `
        -Name "RackNovaPostgreSQL16" `
        -ErrorAction SilentlyContinue

    if ($PgService) {
        if (-not (Test-Path -LiteralPath $PgVersionPath)) {
            throw (
                "RackNovaPostgreSQL16 existe pero el cluster esperado no está disponible. " +
                "No inicializaré uno nuevo encima de este estado."
            )
        }
        return
    }

    if (Test-Path -LiteralPath $PgVersionPath) {
        if (-not (Test-Path -LiteralPath $SecretsPath)) {
            throw (
                "Encontré un cluster PostgreSQL existente sin secrets.dat. " +
                "No registraré ni modificaré el cluster sin sus credenciales originales."
            )
        }

        if (-not (Test-Path -LiteralPath $PgCtl)) {
            throw "Falta pg_ctl.exe para volver a registrar el servicio PostgreSQL."
        }

        Write-SafeLog "Cluster existente sin servicio: registrando RackNovaPostgreSQL16 sin recrear datos."
        Grant-LocalSystemDataAccess

        & $PgCtl register `
            -N "RackNovaPostgreSQL16" `
            -D $PgData `
            -S auto

        if ($LASTEXITCODE -ne 0) {
            throw "pg_ctl register terminó con código $LASTEXITCODE."
        }

        & sc.exe config `
            RackNovaPostgreSQL16 `
            obj= LocalSystem `
            start= auto | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "No pude normalizar RackNovaPostgreSQL16 a LocalSystem."
        }

        & sc.exe failure `
            RackNovaPostgreSQL16 `
            reset= 86400 `
            actions= restart/5000/restart/15000/restart/60000 | Out-Null
        & sc.exe failureflag RackNovaPostgreSQL16 1 | Out-Null
    }
    elseif (Test-Path -LiteralPath $SecretsPath) {
        throw (
            "Existe configuración de una instalación anterior pero no encuentro su cluster PostgreSQL. " +
            "No crearé una base nueva automáticamente; revisa el respaldo indicado en el log."
        )
    }
}

function Build-SafeEffectiveConfigure {
    if (-not (Test-Path -LiteralPath $Original)) {
        throw "No existe configure_install.ps1"
    }

    $text = Normalize-Newlines ([System.IO.File]::ReadAllText($Original))

    $oldServiceAccount = @'
    & sc.exe config `
        RackNovaPostgreSQL16 `
        obj= LocalSystem `
        start= auto | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude configurar la cuenta LocalSystem de PostgreSQL."
    }
'@

    $newServiceAccount = @'
    $ServiceAccountOutput = (& sc.exe config `
        RackNovaPostgreSQL16 `
        obj= LocalSystem `
        start= auto 2>&1 | Out-String).Trim()
    $ServiceAccountExitCode = $LASTEXITCODE

    if ($ServiceAccountOutput) {
        Write-Log (
            "POSTGRES SERVICE ACCOUNT: " +
            ($ServiceAccountOutput -replace "`r?`n", " | ")
        )
    }

    if ($ServiceAccountExitCode -ne 0) {
        throw (
            "No pude configurar la cuenta LocalSystem de PostgreSQL. " +
            "sc.exe terminó con código $ServiceAccountExitCode."
        )
    }
'@

    $text = Replace-RequiredText `
        -Source $text `
        -Old $oldServiceAccount `
        -New $newServiceAccount `
        -Description "diagnóstico de cuenta LocalSystem"

    $oldPgDataAcl = @'
    & icacls.exe `
        $PgData `
        /inheritance:r `
        /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        /T `
        /C | Out-Null
'@

    $newPgDataAcl = @'
    & icacls.exe `
        $PgData `
        /inheritance:e `
        /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        /T `
        /C | Out-Null
'@

    $text = Replace-RequiredText `
        -Source $text `
        -Old $oldPgDataAcl `
        -New $newPgDataAcl `
        -Description "ACL heredable PostgreSQL"

    $oldInitSchema = @'
Write-Log "Inicializando esquema RackNova."
& $Ctl init-schema

if ($LASTEXITCODE -ne 0) {
    throw "Falló init-schema."
}
'@

    $newInitSchema = @'
Write-Log "Inicializando esquema RackNova."
$InitSchemaOutput = (& $Ctl init-schema 2>&1 | Out-String).Trim()
$InitSchemaExitCode = $LASTEXITCODE

if ($InitSchemaOutput) {
    Write-Log (
        "INIT-SCHEMA: " +
        ($InitSchemaOutput -replace "`r?`n", " | ")
    )
}

if ($InitSchemaExitCode -ne 0) {
    throw (
        "Falló init-schema con código $InitSchemaExitCode. " +
        "El instalador seguro NO reconstruirá ni moverá PostgreSQL automáticamente."
    )
}
'@

    $text = Replace-RequiredText `
        -Source $text `
        -Old $oldInitSchema `
        -New $newInitSchema `
        -Description "init-schema no destructivo con diagnóstico"

    $oldEventFilter = '@{ LogName = "System"; StartTime = $Since }'
    $newEventFilter = '@{ LogName = @("System", "Application"); StartTime = $Since }'
    if ($text.Contains($oldEventFilter)) {
        $text = $text.Replace($oldEventFilter, $newEventFilter)
    }

    $text = $text.Replace("native-f1.8-portable", "native-f1.9.2-safe")
    $text = $text.Replace(
        "RackNova Native F1.8 portable",
        "RackNova Native F1.9.2 SAFE"
    )

    [System.IO.File]::WriteAllText(
        $Effective,
        $text,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

$ExitCode = 1
$RollbackReady = $false

try {
    Write-SafeLog "RackNova F1.9.2 SAFE installer entry iniciado."
    Write-SafeLog ("InstallDir=" + $InstallDir)
    Write-SafeLog "POLÍTICA: conservar PostgreSQL existente; prohibido mover, borrar o reinicializar el cluster durante reparación."

    Recover-FromInterruptedDefinitiveReset
    Grant-LocalSystemDataAccess
    Ensure-ExistingPostgresService

    Backup-ConfigForRollback
    $RollbackReady = $true

    Build-SafeEffectiveConfigure
    Write-SafeLog "configure_install_safe_effective.ps1 preparado."

    $Output = & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $Effective `
        -InstallDir $InstallDir 2>&1

    $ExitCode = $LASTEXITCODE

    foreach ($OutputLine in @($Output)) {
        if ($null -ne $OutputLine) {
            $Rendered = ($OutputLine | Out-String).Trim()
            if ($Rendered) {
                Write-SafeLog ("CONFIGURE: " + $Rendered)
            }
        }
    }

    Write-SafeLog ("Configuración principal terminó con código " + $ExitCode + ".")

    if ($ExitCode -ne 0) {
        throw "configure_install seguro terminó con código $ExitCode."
    }

    Grant-LocalSystemDataAccess
    Write-SafeLog "RackNova F1.9.2 SAFE completado correctamente."
    $ExitCode = 0
}
catch {
    $ExitCode = 1
    try {
        Write-SafeLog ("ERROR: " + $_.Exception.Message)
        if ($_.ScriptStackTrace) {
            Write-SafeLog ("STACK: " + ($_.ScriptStackTrace -replace "`r?`n", " | "))
        }

        if ($RollbackReady) {
            Restore-ConfigRollback
            Write-SafeLog "Rollback de Config completado. PostgreSQL\data no fue modificado por el rollback."
        }
    }
    catch {
        try {
            Write-SafeLog ("ROLLBACK WARNING: " + $_.Exception.Message)
        }
        catch {
        }
    }
}
finally {
    Remove-Item `
        -LiteralPath $Effective `
        -Force `
        -ErrorAction SilentlyContinue
}

exit $ExitCode
