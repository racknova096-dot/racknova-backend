param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$PgData = Join-Path $ProgramDataRoot "PostgreSQL\data"
$BackupRoot = Join-Path $ProgramDataRoot (
    "Backups\DefinitiveReset-" + (Get-Date -Format "yyyyMMdd_HHmmss")
)
$LogDir = Join-Path $ProgramDataRoot "Logs"
$Log = Join-Path $LogDir (
    "definitive-reset-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log"
)

New-Item -ItemType Directory -Force -Path $BackupRoot, $LogDir | Out-Null

function Write-Log([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $Line | Out-File -LiteralPath $Log -Append -Encoding utf8
}

function Stop-And-Delete-Service([string]$Name) {
    $Service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $Service) {
        return
    }

    Write-Log "Deteniendo servicio $Name."
    if ($Service.Status -ne "Stopped") {
        Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        try {
            $Service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
        }
        catch {
            Write-Log "El servicio $Name no confirmó STOPPED dentro de 30 s; continuaré con sc.exe."
        }
    }

    & sc.exe delete $Name | Out-Null
    $DeleteCode = $LASTEXITCODE
    if ($DeleteCode -ne 0 -and $DeleteCode -ne 1060) {
        throw "No pude eliminar el servicio $Name. Código sc.exe: $DeleteCode"
    }

    for ($Attempt = 1; $Attempt -le 40; $Attempt++) {
        if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
            Write-Log "Servicio $Name eliminado."
            return
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Windows todavía conserva el servicio $Name después de esperar 20 segundos."
}

try {
    Write-Log "RackNova Definitive Reset iniciado."
    Write-Log "InstallDir=$InstallDir"
    Write-Log "BackupRoot=$BackupRoot"

    Stop-And-Delete-Service "RackNovaLocal"
    Stop-And-Delete-Service "RackNovaPostgreSQL16"

    if (Test-Path -LiteralPath $ConfigDir) {
        $ConfigBackup = Join-Path $BackupRoot "Config"
        Write-Log "Respaldando Config."
        Copy-Item -LiteralPath $ConfigDir -Destination $ConfigBackup -Recurse -Force
    }

    if (Test-Path -LiteralPath $PgData) {
        Write-Log "Preparando ACL del cluster PostgreSQL para respaldo."
        & icacls.exe `
            $PgData `
            /inheritance:e `
            /grant:r `
            "*S-1-5-18:(OI)(CI)F" `
            "*S-1-5-32-544:(OI)(CI)F" `
            /T `
            /C | Out-Null

        $PgBackup = Join-Path $BackupRoot "PostgreSQL-data"
        Write-Log "Moviendo cluster PostgreSQL anterior al respaldo."
        Move-Item -LiteralPath $PgData -Destination $PgBackup -Force
    }

    if (Test-Path -LiteralPath $ConfigDir) {
        Write-Log "Retirando configuración activa anterior; ya está respaldada."
        Remove-Item -LiteralPath $ConfigDir -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

    $Manifest = Join-Path $BackupRoot "README.txt"
    @"
RackNova Local - respaldo previo a instalación definitiva
Fecha: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Equipo: $env:COMPUTERNAME

Este respaldo fue creado automáticamente antes de reconstruir la base Local.
Incluye la configuración anterior y, si existía, el cluster PostgreSQL completo.
No contiene el Sync Secret en texto plano; secrets.dat permanece protegido con DPAPI.
"@ | Set-Content -LiteralPath $Manifest -Encoding UTF8

    Write-Log "RackNova Definitive Reset completado correctamente."
    exit 0
}
catch {
    try {
        Write-Log ("ERROR: " + $_.Exception.Message)
    }
    catch {
    }
    exit 1
}
