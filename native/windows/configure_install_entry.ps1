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
