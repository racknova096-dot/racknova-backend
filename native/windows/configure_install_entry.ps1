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
    # pg_ctl registra RackNovaPostgreSQL16 como LocalSystem cuando -U se omite.
    # Reparamos por SID para que funcione igual en cualquier idioma de Windows
    # y para recuperar instalaciones parciales de F1.7/F1.8/F1.9.
    if (Test-Path -LiteralPath $PgRoot) {
        & icacls.exe `
            $PgRoot `
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

# Repara primero una instalación parcial antes de intentar arrancarla.
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
