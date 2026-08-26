param(
    [switch]$KeepDatabase = $true
)

$ErrorActionPreference = "Continue"

$serviceExe = Join-Path $env:ProgramFiles "RackNova\RackNovaLocalService.exe"

if (Test-Path $serviceExe) {
    & $serviceExe stop | Out-Null
    Start-Sleep -Seconds 2
    & $serviceExe remove | Out-Null
}

Get-NetFirewallRule -DisplayName "RackNova Local" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

if (-not $KeepDatabase) {
    Stop-Service "RackNovaPostgreSQL16" -ErrorAction SilentlyContinue
}

# F1 preserva C:\ProgramData\RackNova por seguridad.
exit 0
