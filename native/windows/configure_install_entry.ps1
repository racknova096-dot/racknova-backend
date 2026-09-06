param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$PgInstall = Join-Path $InstallDir "PostgreSQL"
$Original = Join-Path $InstallDir "installer\configure_install.ps1"
$Effective = Join-Path $InstallDir "installer\configure_install_effective.ps1"

function Grant-LocalSystemAccess {
    # PostgreSQL corre como LocalSystem. Habilitamos herencia antes de aplicar
    # el ACE de SYSTEM para reparar también archivos de clusters creados por
    # builds anteriores que quedaron con la herencia deshabilitada.
    if (Test-Path -LiteralPath $PgRoot) {
        & icacls.exe `
            $PgRoot `
            /inheritance:e `
            /grant:r `
            "*S-1-5-18:(OI)(CI)F" `
            /T `
            /C | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "No pude dar permisos de PostgreSQL a LocalSystem."
        }
    }

    if (Test-Path -LiteralPath $PgInstall) {
        & icacls.exe `
            $PgInstall `
            /inheritance:e `
            /grant:r `
            "*S-1-5-18:(OI)(CI)RX" `
            /T `
            /C | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "No pude dar acceso a los binarios PostgreSQL a LocalSystem."
        }
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

    if (-not $Source.Contains($Old)) {
        throw "No encontré el bloque esperado: $Description"
    }

    return $Source.Replace($Old, $New)
}

if (-not (Test-Path -LiteralPath $Original)) {
    throw "No existe configure_install.ps1"
}

# Crear el padre antes de la reparación hace que el cluster nuevo herede desde
# el inicio el acceso de SYSTEM. Esto replica la reparación manual validada.
New-Item -ItemType Directory -Force -Path $PgRoot | Out-Null
Grant-LocalSystemAccess

$text = [System.IO.File]::ReadAllText($Original)

# Mantener LocalSystem, que es la cuenta con la que pg_ctl registra el servicio
# y la que ya fue validada en la reparación manual. Además, conservar la salida
# real de sc.exe en el log para no volver a perder el motivo de un fallo.
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

# initdb crea postgresql.conf y el resto del cluster con la cuenta del
# instalador. F1.9 estaba deshabilitando la herencia (/inheritance:r) justo
# antes de arrancar el servicio, dejando postgresql.conf inaccesible para
# LocalSystem en Windows Server/GitHub Runner. Mantener la herencia habilitada
# conserva SYSTEM en cada archivo y sigue dejando control total a SYSTEM/admin.
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

# Una PC que ya pasó por F1.7/F1.8 puede conservar el servicio PostgreSQL y
# secrets.dat aunque la creación de racknova/racknova_app haya quedado a medias.
# En ese estado PostgreSQL arranca correctamente, pero RackNovaCtl init-schema
# falla. Si la instalación todavía NO está activada con Cloud, conservamos una
# copia completa del estado parcial y reconstruimos el runtime local desde cero.
# El segundo intento está protegido por una bandera para evitar recursión infinita.
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

# El script base se guarda con CRLF en el paquete de Windows, mientras que los
# here-strings del wrapper pueden llegar con LF según cómo Git prepare el repo.
# Una comparación literal hacía abortar el instalador antes de crear el log.
# Usamos regex solo para localizar este bloque y MatchEvaluator para que los '$'
# del script de reemplazo no se interpreten como grupos de regex.
$InitSchemaPattern = @'
(?ms)^Write-Log "Inicializando esquema RackNova\."\r?\n&\s+\$Ctl\s+init-schema\s*\r?\n\s*\r?\nif\s+\(\$LASTEXITCODE\s+-ne\s+0\)\s*\{\s*\r?\n\s*throw\s+"Falló init-schema\."\s*\r?\n\s*\}
'@.Trim()

$InitSchemaRegex = New-Object System.Text.RegularExpressions.Regex($InitSchemaPattern)

if (-not $InitSchemaRegex.IsMatch($text)) {
    throw "No encontré el bloque esperado: recuperación automática de init-schema"
}

$text = $InitSchemaRegex.Replace(
    $text,
    [System.Text.RegularExpressions.MatchEvaluator]{
        param($Match)
        return $newInitSchema
    },
    1
)

# PostgreSQL/pg_ctl puede escribir fallos tempranos en Application. Revisamos
# tanto System como Application para que un fallo de arranque quede explicado.
$oldEventFilter = '@{ LogName = "System"; StartTime = $Since }'
$newEventFilter = '@{ LogName = @("System", "Application"); StartTime = $Since }'

if ($text.Contains($oldEventFilter)) {
    $text = $text.Replace($oldEventFilter, $newEventFilter)
}

# Mantener diagnóstico y secretos con la versión real del instalador.
$text = $text.Replace("native-f1.8-portable", "native-f1.9-portable")
$text = $text.Replace("RackNova Native F1.8 portable", "RackNova Native F1.9 portable")

[System.IO.File]::WriteAllText(
    $Effective,
    $text,
    (New-Object System.Text.UTF8Encoding($false))
)

try {
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $Effective `
        -InstallDir $InstallDir

    $ExitCode = $LASTEXITCODE

    # Es idempotente: cubre también el cluster creado durante esta ejecución.
    Grant-LocalSystemAccess

    exit $ExitCode
}
finally {
    Remove-Item -LiteralPath $Effective -Force -ErrorAction SilentlyContinue
}
