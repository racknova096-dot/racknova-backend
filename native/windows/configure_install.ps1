param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$PgData = Join-Path $ProgramDataRoot "PostgreSQL\data"
$PgLogDir = Join-Path $ProgramDataRoot "PostgreSQL\logs"
$PgInstall = Join-Path $InstallDir "PostgreSQL"

$Ctl = Join-Path $InstallDir "RackNovaCtl.exe"
$ServiceExe = Join-Path $InstallDir "RackNovaLocalService.exe"

$InitDb = Join-Path $PgInstall "bin\initdb.exe"
$PgCtl = Join-Path $PgInstall "bin\pg_ctl.exe"
$PgIsReady = Join-Path $PgInstall "bin\pg_isready.exe"
$Psql = Join-Path $PgInstall "bin\psql.exe"
$Createdb = Join-Path $PgInstall "bin\createdb.exe"
$PostgresExe = Join-Path $PgInstall "bin\postgres.exe"

$Log = Join-Path $LogDir (
    "native-install-" +
    (Get-Date -Format "yyyyMMdd_HHmmss") +
    ".log"
)

New-Item -ItemType Directory -Force -Path `
    $ConfigDir, `
    $LogDir, `
    $PgLogDir | Out-Null

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $Log -Append | Write-Host
}

function New-RackNovaPassword {
    $guid1 = [Guid]::NewGuid().ToString("N")
    $guid2 = [Guid]::NewGuid().ToString("N")
    return ("Rn!" + $guid1 + "Aa1" + $guid2)
}

function Secure-TempFile([string]$Path) {
    $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

    if (Test-Path -LiteralPath $Path) {
        & takeown.exe /F $Path /A | Out-Null

        & icacls.exe `
            $Path `
            /grant:r `
            "*S-1-5-18:F" `
            "*S-1-5-32-544:F" `
            "*${CurrentSid}:F" | Out-Null

        Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
    }

    New-Item -ItemType File -Force -Path $Path | Out-Null

    & icacls.exe `
        $Path `
        /inheritance:r `
        /grant:r `
        "*S-1-5-18:F" `
        "*S-1-5-32-544:F" `
        "*${CurrentSid}:F" | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude proteger el archivo temporal: $Path"
    }
}

function Remove-LegacyInstallerSecret {
    $legacy = Join-Path $ConfigDir "postgres-install-options.tmp"

    if (Test-Path -LiteralPath $legacy) {
        Write-Log "Eliminando archivo temporal legacy de PostgreSQL."

        try {
            & takeown.exe /F $legacy /A | Out-Null
            & icacls.exe `
                $legacy `
                /grant:r `
                "*S-1-5-18:F" `
                "*S-1-5-32-544:F" | Out-Null

            Remove-Item -LiteralPath $legacy -Force -ErrorAction Stop
        }
        catch {
            throw "No pude eliminar el archivo temporal legacy de PostgreSQL."
        }
    }
}

function Wait-PostgresReady {
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        & $PgIsReady `
            -h 127.0.0.1 `
            -p 54329 `
            -t 2 | Out-Null

        if ($LASTEXITCODE -eq 0) {
            return
        }

        Start-Sleep -Seconds 2
    }

    throw "PostgreSQL no quedó listo en 60 segundos."
}

Write-Log "RackNova Native F1.7 portable: configuración iniciada."

trap {
    try {
        Write-Log ("ERROR: " + $_.Exception.Message)

        if ($_.ScriptStackTrace) {
            Write-Log (
                "STACK: " +
                ($_.ScriptStackTrace -replace "`r?`n", " | ")
            )
        }
    }
    catch {
    }

    exit 1
}

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
$IsAdmin = $Principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $IsAdmin) {
    throw "RackNova Setup requiere privilegios de administrador."
}

Write-Log "Privilegios administrativos confirmados."

Remove-LegacyInstallerSecret

foreach ($required in @(
    $Ctl,
    $ServiceExe,
    $InitDb,
    $PgCtl,
    $PgIsReady,
    $Psql,
    $Createdb,
    $PostgresExe
)) {
    if (-not (Test-Path $required)) {
        throw "Falta componente requerido: $required"
    }
}

$PgService = Get-Service `
    -Name "RackNovaPostgreSQL16" `
    -ErrorAction SilentlyContinue

$SecretsPath = Join-Path $ConfigDir "secrets.dat"

if ($PgService -and -not (Test-Path $SecretsPath)) {
    throw (
        "Existe RackNovaPostgreSQL16 pero faltan secretos DPAPI. " +
        "No continuaré para evitar perder acceso a la base."
    )
}

$PgSuperPassword = New-RackNovaPassword
$AppPassword = New-RackNovaPassword
$JwtSecret = (New-RackNovaPassword) + (New-RackNovaPassword)

if (-not $PgService) {
    Write-Log "Preparando PostgreSQL portable; no se ejecutará instalador EDB."

    if (Test-Path $PgData) {
        $PgVersionFile = Join-Path $PgData "PG_VERSION"

        if (-not (Test-Path $PgVersionFile)) {
            Write-Log "Eliminando directorio PostgreSQL incompleto."
            Remove-Item $PgData -Recurse -Force
        }
        else {
            throw (
                "Existe un cluster PostgreSQL sin servicio registrado. " +
                "Requiere reparación antes de continuar."
            )
        }
    }

    New-Item -ItemType Directory -Force -Path $PgData | Out-Null

    $PwFile = Join-Path $ConfigDir "postgres-super.tmp"
    Secure-TempFile $PwFile

    try {
        Set-Content `
            -LiteralPath $PwFile `
            -Value $PgSuperPassword `
            -Encoding ASCII `
            -NoNewline

        Write-Log "Inicializando cluster PostgreSQL."

        & $InitDb `
            -D $PgData `
            -U racknova_super `
            -E UTF8 `
            --locale=C `
            --auth=scram-sha-256 `
            --pwfile=$PwFile `
            --no-instructions

        if ($LASTEXITCODE -ne 0) {
            throw "initdb terminó con código $LASTEXITCODE."
        }
    }
    finally {
        Remove-Item $PwFile -Force -ErrorAction SilentlyContinue
    }

    $PgConfig = Join-Path $PgData "postgresql.conf"

    @"

# RackNova Local
listen_addresses = '127.0.0.1'
port = 54329
password_encryption = 'scram-sha-256'
"@ | Add-Content -LiteralPath $PgConfig -Encoding UTF8

    $PgHba = Join-Path $PgData "pg_hba.conf"

    @"
# RackNova Local - solo localhost
host    all    all    127.0.0.1/32    scram-sha-256
host    all    all    ::1/128         scram-sha-256
"@ | Set-Content -LiteralPath $PgHba -Encoding ASCII

    & icacls.exe `
        $PgData `
        /inheritance:r `
        /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        /T `
        /C | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude proteger el directorio PostgreSQL."
    }

    Write-Log "Registrando RackNovaPostgreSQL16 con pg_ctl."

    & $PgCtl register `
        -N "RackNovaPostgreSQL16" `
        -D $PgData `
        -S auto `
        -U "LocalSystem"

    if ($LASTEXITCODE -ne 0) {
        throw "pg_ctl register terminó con código $LASTEXITCODE."
    }

    & sc.exe failure `
        RackNovaPostgreSQL16 `
        reset= 86400 `
        actions= restart/5000/restart/15000/restart/60000 | Out-Null

    & sc.exe failureflag RackNovaPostgreSQL16 1 | Out-Null

    Write-Log "Iniciando PostgreSQL."
    Start-Service "RackNovaPostgreSQL16"

    Wait-PostgresReady

    Write-Log "PostgreSQL portable listo en 127.0.0.1:54329."

    $env:PGPASSWORD = $PgSuperPassword

    $RoleSql = @'
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='racknova_app'
    ) THEN
        CREATE ROLE racknova_app LOGIN PASSWORD '__APP_PASSWORD__';
    ELSE
        ALTER ROLE racknova_app
            WITH LOGIN PASSWORD '__APP_PASSWORD__';
    END IF;
END
$$;
'@.Replace("__APP_PASSWORD__", $AppPassword)

    & $Psql `
        -h 127.0.0.1 `
        -p 54329 `
        -U racknova_super `
        -d postgres `
        -v ON_ERROR_STOP=1 `
        -c $RoleSql

    if ($LASTEXITCODE -ne 0) {
        throw "No pude crear/actualizar racknova_app."
    }

    $Exists = & $Psql `
        -h 127.0.0.1 `
        -p 54329 `
        -U racknova_super `
        -d postgres `
        -tAc "SELECT 1 FROM pg_database WHERE datname='racknova'"

    if (($Exists | Out-String).Trim() -ne "1") {
        & $Createdb `
            -h 127.0.0.1 `
            -p 54329 `
            -U racknova_super `
            -O racknova_app `
            racknova

        if ($LASTEXITCODE -ne 0) {
            throw "No pude crear la base racknova."
        }
    }

    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

    $Bootstrap = Join-Path $ConfigDir "bootstrap-secrets.tmp.json"
    Secure-TempFile $Bootstrap

    @{
        db_password = $AppPassword
        pg_super_password = $PgSuperPassword
        jwt_secret = $JwtSecret
        node_credential = ""
        activated = $false
        empresa_id = "11111111-1111-4111-8111-111111111111"
        node_code = ("LOCAL-" + $env:COMPUTERNAME.ToUpper())
        node_name = ("RackNova Local - " + $env:COMPUTERNAME)
        cloud_url = ""
        db_port = 54329
        app_version = "native-f1.7-portable"
    } |
        ConvertTo-Json |
        Set-Content -LiteralPath $Bootstrap -Encoding UTF8

    & $Ctl bootstrap-secrets --file $Bootstrap

    if ($LASTEXITCODE -ne 0) {
        throw "RackNovaCtl no pudo proteger los secretos."
    }
}
else {
    Write-Log "RackNovaPostgreSQL16 existente detectado."

    if ($PgService.Status -ne "Running") {
        Start-Service "RackNovaPostgreSQL16"
    }

    Wait-PostgresReady
}

Write-Log "Inicializando esquema RackNova."
& $Ctl init-schema

if ($LASTEXITCODE -ne 0) {
    throw "Falló init-schema."
}

$ExistingService = Get-Service `
    -Name "RackNovaLocal" `
    -ErrorAction SilentlyContinue

if (-not $ExistingService) {
    Write-Log "Registrando servicio RackNova Local."

    & $ServiceExe --startup auto install

    if ($LASTEXITCODE -ne 0) {
        throw "No pude registrar RackNovaLocal."
    }
}

& sc.exe config RackNovaLocal depend= RackNovaPostgreSQL16 | Out-Null

& sc.exe failure `
    RackNovaLocal `
    reset= 86400 `
    actions= restart/5000/restart/15000/restart/60000 | Out-Null

& sc.exe failureflag RackNovaLocal 1 | Out-Null

& $ServiceExe start | Out-Null

$ServiceReady = $false

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-WebRequest `
            -Uri "http://127.0.0.1:8000/racknova-native/health" `
            -UseBasicParsing `
            -TimeoutSec 3

        if ($response.StatusCode -eq 200) {
            $ServiceReady = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $ServiceReady) {
    throw "RackNovaLocal no respondió después de 60 segundos."
}

$FirewallRule = Get-NetFirewallRule `
    -DisplayName "RackNova Local" `
    -ErrorAction SilentlyContinue

if (-not $FirewallRule) {
    New-NetFirewallRule `
        -DisplayName "RackNova Local" `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8000 `
        -Profile Private,Domain | Out-Null
}

Write-Log "Ejecutando health check final."
& $Ctl health

if ($LASTEXITCODE -ne 0) {
    Write-Log "Health check falló; generando diagnóstico."
    $Diag = & $Ctl diagnose
    throw "RackNova Local no pasó health check. Diagnóstico: $Diag"
}

Write-Log "RackNova Native F1.7 portable instalado correctamente."
Write-Log "Dashboard Local: http://127.0.0.1:8000/ui/"
exit 0
