param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$Original = Join-Path $InstallDir "installer\configure_install.ps1"
$Effective = Join-Path $InstallDir "installer\configure_install_effective.ps1"
$EntryLog = Join-Path $LogDir (
    "entry-install-" +
    (Get-Date -Format "yyyyMMdd_HHmmss") +
    ".log"
)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-EntryLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $EntryLog -Append | Write-Host
}

function Normalize-Newlines([string]$Value) {
    if ($null -eq $Value) {
        return ""
    }

    return $Value.Replace("`r`n", "`n").Replace("`r", "`n")
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
        /T `
        /C 2>&1 | Out-String).Trim()

    $AclExitCode = $LASTEXITCODE

    if ($AclOutput) {
        Write-EntryLog (
            "POSTGRES DATA ACL: " +
            ($AclOutput -replace "`r?`n", " | ")
        )
    }

    if ($AclExitCode -ne 0) {
        throw (
            "No pude dar permisos del cluster PostgreSQL a LocalSystem. " +
            "icacls terminó con código $AclExitCode."
        )
    }
}

function Replace-RequiredText {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Source,

        [Parameter(Mandatory=$true)]
        [string]$Old,

        [Parameter(Mandatory=$true)]
        [string]$New,

        [Parameter(Mandatory=$true)]
        [string]$Description
    )

    $NormalizedSource = Normalize-Newlines $Source
    $NormalizedOld = Normalize-Newlines $Old
    $NormalizedNew = Normalize-Newlines $New

    if (-not $NormalizedSource.Contains($NormalizedOld)) {
        throw "No encontré el bloque esperado: $Description"
    }

    return $NormalizedSource.Replace($NormalizedOld, $NormalizedNew)
}

$ExitCode = 1

try {
    Write-EntryLog "RackNova installer entry iniciado."
    Write-EntryLog ("InstallDir=" + $InstallDir)

    if (-not (Test-Path -LiteralPath $Original)) {
        throw "No existe configure_install.ps1"
    }

    # Solo modificamos ACL del cluster en ProgramData. No tocamos de forma
    # recursiva los binarios de PostgreSQL en Program Files: LocalSystem ya
    # dispone de lectura/ejecución allí y un icacls recursivo podía abortar
    # antes de que configure_install.ps1 alcanzara a crear su propio log.
    New-Item -ItemType Directory -Force -Path $PgRoot | Out-Null
    Write-EntryLog "Preparando permisos del cluster PostgreSQL."
    Grant-LocalSystemDataAccess

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
        -Description "configuración y diagnóstico de la cuenta LocalSystem"

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
        -Description "ACL heredable de PostgreSQL para LocalSystem"

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
    if ($env:RACKNOVA_SCHEMA_RECOVERY_ATTEMPTED -eq "1") {
        throw (
            "Falló init-schema incluso después de reconstruir el runtime local. " +
            "Código: $InitSchemaExitCode."
        )
    }

    $CanRebuildLocal = $true
    $ConfigFile = Join-Path $ConfigDir "config.json"

    if (Test-Path -LiteralPath $ConfigFile) {
        try {
            $ExistingConfig = Get-Content `
                -LiteralPath $ConfigFile `
                -Raw `
                -ErrorAction Stop | ConvertFrom-Json

            if ($ExistingConfig.activated -eq $true) {
                $CanRebuildLocal = $false
            }
        }
        catch {
            Write-Log (
                "No pude validar config.json antes de recuperación: " +
                $_.Exception.Message
            )
        }
    }

    if (-not $CanRebuildLocal) {
        throw (
            "Falló init-schema y esta instalación ya está activada con Cloud. " +
            "No reconstruiré PostgreSQL automáticamente para proteger sus datos."
        )
    }

    $RecoveryStamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $RecoveryRoot = Join-Path `
        $ProgramDataRoot `
        ("RecoveryBackups\init-schema-" + $RecoveryStamp)

    Write-Log (
        "Detecté una instalación local parcial anterior. " +
        "Haré respaldo y reconstrucción automática."
    )
    Write-Log ("RECOVERY BACKUP: " + $RecoveryRoot)

    New-Item -ItemType Directory -Force -Path $RecoveryRoot | Out-Null

    if (Test-Path -LiteralPath $ConfigDir) {
        Copy-Item `
            -LiteralPath $ConfigDir `
            -Destination (Join-Path $RecoveryRoot "Config") `
            -Recurse `
            -Force
    }

    $LocalService = Get-Service `
        -Name "RackNovaLocal" `
        -ErrorAction SilentlyContinue

    if ($LocalService) {
        Stop-Service `
            -Name "RackNovaLocal" `
            -Force `
            -ErrorAction SilentlyContinue

        & sc.exe delete RackNovaLocal | Out-Null
    }

    $PostgresService = Get-Service `
        -Name "RackNovaPostgreSQL16" `
        -ErrorAction SilentlyContinue

    if ($PostgresService) {
        Stop-Service `
            -Name "RackNovaPostgreSQL16" `
            -Force `
            -ErrorAction SilentlyContinue

        & sc.exe delete RackNovaPostgreSQL16 | Out-Null
    }

    foreach ($ServiceName in @("RackNovaLocal", "RackNovaPostgreSQL16")) {
        for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
            if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
                break
            }
            Start-Sleep -Milliseconds 500
        }

        if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
            throw "No pude eliminar el servicio $ServiceName durante la recuperación."
        }
    }

    if (Test-Path -LiteralPath $PgData) {
        & icacls.exe `
            $PgData `
            /inheritance:e `
            /grant:r `
            "*S-1-5-18:(OI)(CI)F" `
            "*S-1-5-32-544:(OI)(CI)F" `
            /T `
            /C | Out-Null

        $BackupPgData = Join-Path $RecoveryRoot "PostgreSQL-data"
        Move-Item `
            -LiteralPath $PgData `
            -Destination $BackupPgData `
            -Force `
            -ErrorAction Stop
    }

    Remove-Item `
        -LiteralPath (Join-Path $ConfigDir "secrets.dat") `
        -Force `
        -ErrorAction SilentlyContinue
    Remove-Item `
        -LiteralPath (Join-Path $ConfigDir "config.json") `
        -Force `
        -ErrorAction SilentlyContinue
    Remove-Item `
        -LiteralPath (Join-Path $ConfigDir "bootstrap-secrets.tmp.json") `
        -Force `
        -ErrorAction SilentlyContinue

    $PreviousRecoveryFlag = $env:RACKNOVA_SCHEMA_RECOVERY_ATTEMPTED
    $env:RACKNOVA_SCHEMA_RECOVERY_ATTEMPTED = "1"

    try {
        Write-Log "Reintentando RackNova como instalación local limpia."

        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -NoProfile `
            -ExecutionPolicy Bypass `
            -File $PSCommandPath `
            -InstallDir $InstallDir

        $RecoveryExitCode = $LASTEXITCODE
    }
    finally {
        if ($null -eq $PreviousRecoveryFlag) {
            Remove-Item `
                Env:\RACKNOVA_SCHEMA_RECOVERY_ATTEMPTED `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:RACKNOVA_SCHEMA_RECOVERY_ATTEMPTED = $PreviousRecoveryFlag
        }
    }

    if ($RecoveryExitCode -eq 0) {
        Write-Log "Recuperación automática completada correctamente."
        exit 0
    }

    throw (
        "La reconstrucción automática también falló. " +
        "Código: $RecoveryExitCode. Respaldo conservado en $RecoveryRoot"
    )
}
'@

    $text = Replace-RequiredText `
        -Source $text `
        -Old $oldInitSchema `
        -New $newInitSchema `
        -Description "recuperación automática de init-schema"

    $oldEventFilter = '@{ LogName = "System"; StartTime = $Since }'
    $newEventFilter = '@{ LogName = @("System", "Application"); StartTime = $Since }'

    if ($text.Contains($oldEventFilter)) {
        $text = $text.Replace($oldEventFilter, $newEventFilter)
    }

    $text = $text.Replace("native-f1.8-portable", "native-f1.9-portable")
    $text = $text.Replace(
        "RackNova Native F1.8 portable",
        "RackNova Native F1.9 portable"
    )

    [System.IO.File]::WriteAllText(
        $Effective,
        $text,
        (New-Object System.Text.UTF8Encoding($false))
    )

    Write-EntryLog "configure_install_effective.ps1 preparado correctamente."
    Write-EntryLog "Ejecutando configuración principal de RackNova."

    $Output = & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $Effective `
        -InstallDir $InstallDir 2>&1

    $ExitCode = $LASTEXITCODE

    foreach ($OutputLine in @($Output)) {
        if ($null -ne $OutputLine) {
            Write-EntryLog ("CONFIGURE: " + ($OutputLine | Out-String).Trim())
        }
    }

    Write-EntryLog (
        "configure_install_effective.ps1 terminó con código " + $ExitCode + "."
    )

    try {
        Grant-LocalSystemDataAccess
    }
    catch {
        Write-EntryLog (
            "ACL FINAL WARNING: " + $_.Exception.Message
        )
    }
}
catch {
    $ExitCode = 1
    try {
        Write-EntryLog ("ERROR: " + $_.Exception.Message)

        if ($_.ScriptStackTrace) {
            Write-EntryLog (
                "STACK: " +
                ($_.ScriptStackTrace -replace "`r?`n", " | ")
            )
        }
    }
    catch {
    }
}
finally {
    Remove-Item `
        -LiteralPath $Effective `
        -Force `
        -ErrorAction SilentlyContinue
}

exit $ExitCode
