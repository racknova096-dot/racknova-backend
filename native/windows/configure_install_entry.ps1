param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$PgData = Join-Path $PgRoot "data"
$PgInstall = Join-Path $InstallDir "PostgreSQL"
$Original = Join-Path $InstallDir "installer\configure_install.ps1"
$Effective = Join-Path $InstallDir "installer\configure_install_effective.ps1"

function Grant-NetworkServiceAccess {
    # PostgreSQL 9.2+ on Windows is normally run as NETWORK SERVICE.  The
    # previous RackNova F1.8 registration omitted -U, so pg_ctl registered
    # RackNovaPostgreSQL16 as LocalSystem.  Repair both old installations and
    # fresh installs without depending on localized Windows account names.
    if (Test-Path -LiteralPath $PgRoot) {
        & icacls.exe `
            $PgRoot `
            /grant:r `
            "*S-1-5-20:(OI)(CI)F" `
            /T `
            /C | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "No pude dar permisos de PostgreSQL a NetworkService."
        }
    }

    if (Test-Path -LiteralPath $PgInstall) {
        & icacls.exe `
            $PgInstall `
            /grant:r `
            "*S-1-5-20:(OI)(CI)RX" `
            /T `
            /C | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "No pude dar acceso a los binarios PostgreSQL a NetworkService."
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

# Repara permisos antes de tocar un servicio ya existente de F1.7/F1.8.
Grant-NetworkServiceAccess

$text = [System.IO.File]::ReadAllText($Original)

$oldServiceAccount = @'
    & sc.exe config `
        RackNovaPostgreSQL16 `
        obj= LocalSystem `
        start= auto | Out-Null
'@

$newServiceAccount = @'
    & sc.exe config `
        RackNovaPostgreSQL16 `
        obj= "NT AUTHORITY\NetworkService" `
        password= "" `
        start= auto | Out-Null
'@

$text = Replace-RequiredText `
    -Source $text `
    -Old $oldServiceAccount `
    -New $newServiceAccount `
    -Description "cuenta del servicio PostgreSQL"

# En una instalación limpia PgData todavía no existía cuando este entrypoint
# arrancó. Por eso también insertamos el SID de NetworkService en el ACL que
# configure_install.ps1 aplica DESPUÉS de initdb y ANTES de iniciar el servicio.
$aclAdminLine = '        "*S-1-5-32-544:(OI)(CI)F" `'
$aclNetworkLine = '        "*S-1-5-20:(OI)(CI)F" `'

if (-not $text.Contains($aclNetworkLine)) {
    $text = Replace-RequiredText `
        -Source $text `
        -Old $aclAdminLine `
        -New ($aclAdminLine + [Environment]::NewLine + $aclNetworkLine) `
        -Description "ACL del directorio PostgreSQL"
}

# PostgreSQL/pg_ctl escribe fallos tempranos del servicio en Application. El
# instalador anterior solo inspeccionaba System y por eso el log externo quedó
# sin la causa real del arranque fallido.
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

    # Si el script creó el cluster durante esta ejecución, asegura también los
    # permisos finales. Es idempotente y repara instalaciones parciales.
    Grant-NetworkServiceAccess

    exit $ExitCode
}
finally {
    Remove-Item -LiteralPath $Effective -Force -ErrorAction SilentlyContinue
}
