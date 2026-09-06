param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$PgInstall = Join-Path $InstallDir "PostgreSQL"
$Installer = Join-Path $InstallDir "installer\configure_install.ps1"
$EntryLog = Join-Path $LogDir ("native-entry-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

New-Item -ItemType Directory -Force -Path $LogDir, $PgRoot | Out-Null

function Write-EntryLog([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $Line | Tee-Object -FilePath $EntryLog -Append | Write-Host
}

function Grant-LocalSystemAccess {
    if (Test-Path -LiteralPath $PgRoot) {
        & icacls.exe $PgRoot /inheritance:e /grant:r `
            "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "No pude dar permisos de PostgreSQL a LocalSystem."
        }
    }

    if (Test-Path -LiteralPath $PgInstall) {
        & icacls.exe $PgInstall /inheritance:e /grant:r `
            "*S-1-5-18:(OI)(CI)RX" "*S-1-5-32-544:(OI)(CI)F" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "No pude dar acceso a los binarios PostgreSQL a LocalSystem."
        }
    }
}

try {
    if (-not (Test-Path -LiteralPath $Installer)) {
        throw "No existe configure_install.ps1."
    }

    Write-EntryLog "Preparando permisos de RackNova Local F1.9."
    Grant-LocalSystemAccess

    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile -ExecutionPolicy Bypass -File $Installer -InstallDir $InstallDir

    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "La configuración principal terminó con código $ExitCode."
    }

    Grant-LocalSystemAccess
    Write-EntryLog "Configuración principal terminada correctamente."
    exit 0
}
catch {
    try {
        Write-EntryLog ("ERROR DE ENTRADA: " + $_.Exception.Message)
        if ($_.ScriptStackTrace) {
            Write-EntryLog ("STACK: " + ($_.ScriptStackTrace -replace "`r?`n", " | "))
        }
    }
    catch {
    }
    exit 1
}
