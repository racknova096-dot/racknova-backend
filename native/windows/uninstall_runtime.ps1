param(
    [switch]$DeleteDatabase = $false
)

$ErrorActionPreference = "Continue"

$InstallDir = Join-Path $env:ProgramFiles "RackNova"
$ServiceExe = Join-Path $InstallDir "RackNovaLocalService.exe"
$PgCtl = Join-Path $InstallDir "PostgreSQL\bin\pg_ctl.exe"
$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$PgData = Join-Path $ProgramDataRoot "PostgreSQL\data"

if (Test-Path $ServiceExe) {
    & $ServiceExe stop | Out-Null
    Start-Sleep -Seconds 2
    & $ServiceExe remove | Out-Null
}

Get-NetFirewallRule `
    -DisplayName "RackNova Local" `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

$PgService = Get-Service `
    -Name "RackNovaPostgreSQL16" `
    -ErrorAction SilentlyContinue

if ($PgService) {
    Stop-Service "RackNovaPostgreSQL16" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    if (Test-Path $PgCtl) {
        & $PgCtl unregister -N "RackNovaPostgreSQL16" | Out-Null
    }
    else {
        & sc.exe delete RackNovaPostgreSQL16 | Out-Null
    }
}

if ($DeleteDatabase) {
    Remove-Item $PgData -Recurse -Force -ErrorAction SilentlyContinue
}

# Por defecto preserva C:\ProgramData\RackNova y la base de datos.
exit 0
