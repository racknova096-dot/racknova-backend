param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir,

    [Parameter(Mandatory=$true)]
    [string]$PostgresInstaller
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$PgData = Join-Path $ProgramDataRoot "PostgreSQL\data"
$PgInstall = Join-Path $InstallDir "PostgreSQL"
$Ctl = Join-Path $InstallDir "RackNovaCtl.exe"
$ServiceExe = Join-Path $InstallDir "RackNovaLocalService.exe"
$Log = Join-Path $LogDir ("native-install-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

New-Item -ItemType Directory -Force -Path $ConfigDir, $LogDir | Out-Null

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $Log -Append | Write-Host
}

function New-RackNovaPassword {
    $guid = [Guid]::NewGuid().ToString("N")
    return ("Rn!" + $guid.Substring(0,16) + "Aa1" + $guid.Substring(16))
}

function Secure-TempFile([string]$Path) {
    New-Item -ItemType File -Force -Path $Path | Out-Null
    & icacls.exe $Path /inheritance:r | Out-Null
    & icacls.exe $Path /grant:r "SYSTEM:F" "Administrators:F" | Out-Null
}

Write-Log "RackNova Native F1: configuración iniciada."

if (-not (Test-Path $Ctl)) {
    throw "No existe RackNovaCtl.exe en $InstallDir"
}
if (-not (Test-Path $ServiceExe)) {
    throw "No existe RackNovaLocalService.exe en $InstallDir"
}

$PgService = Get-Service -Name "RackNovaPostgreSQL16" -ErrorAction SilentlyContinue
$PgSuperPassword = New-RackNovaPassword
$PgServicePassword = New-RackNovaPassword
$AppPassword = New-RackNovaPassword
$JwtSecret = (New-RackNovaPassword) + (New-RackNovaPassword)
$JwtSecret = (New-RackNovaPassword) + (New-RackNovaPassword)
$JwtSecret = (New-RackNovaPassword) + (New-RackNovaPassword)

if (-not $PgService) {
    if (-not (Test-Path $PostgresInstaller)) {
        throw "No existe el instalador PostgreSQL empaquetado."
    }

    Write-Log "Instalando PostgreSQL 16 dedicado a RackNova."

    $OptionFile = Join-Path $ConfigDir "postgres-install-options.tmp"
    Secure-TempFile $OptionFile

    @"
mode=unattended
unattendedmodeui=none
prefix=$PgInstall
datadir=$PgData
serverport=54329
servicename=RackNovaPostgreSQL16
serviceaccount=racknova_pg
servicepassword=$PgServicePassword
superaccount=racknova_super
superpassword=$PgSuperPassword
enable-components=server,commandlinetools
create_shortcuts=0
installer-language=es
"@ | Set-Content -LiteralPath $OptionFile -Encoding ASCII

    try {
        $proc = Start-Process `
            -FilePath $PostgresInstaller `
            -ArgumentList @("--optionfile", "`"$OptionFile`"") `
            -Wait `
            -PassThru

        if ($proc.ExitCode -ne 0) {
            throw "PostgreSQL terminó con código $($proc.ExitCode)."
        }
    }
    finally {
        Remove-Item -LiteralPath $OptionFile -Force -ErrorAction SilentlyContinue
    }

    Write-Log "PostgreSQL instalado."
}
else {
    Write-Log "PostgreSQL RackNova existente detectado; no se reinstala."
}

$Psql = Join-Path $PgInstall "bin\psql.exe"
$Createdb = Join-Path $PgInstall "bin\createdb.exe"

$PgConfig = Join-Path $PgData "postgresql.conf"

if (Test-Path $PgConfig) {
    Write-Log "Aislando PostgreSQL a localhost."
    $PgConfigText = Get-Content -LiteralPath $PgConfig -Raw

    if ($PgConfigText -match '(?m)^\s*#?\s*listen_addresses\s*=.*$') {
        $PgConfigText = [regex]::Replace(
            $PgConfigText,
            '(?m)^\s*#?\s*listen_addresses\s*=.*$',
            "listen_addresses = '127.0.0.1'",
            1
        )
    }
    else {
        $PgConfigText += "`r`nlisten_addresses = '127.0.0.1'`r`n"
    }

    if ($PgConfigText -match '(?m)^\s*#?\s*port\s*=.*$') {
        $PgConfigText = [regex]::Replace(
            $PgConfigText,
            '(?m)^\s*#?\s*port\s*=.*$',
            "port = 54329",
            1
        )
    }
    else {
        $PgConfigText += "`r`nport = 54329`r`n"
    }

    Set-Content -LiteralPath $PgConfig -Value $PgConfigText -Encoding UTF8
    Restart-Service "RackNovaPostgreSQL16"
}

$PgConfig = Join-Path $PgData "postgresql.conf"

if (Test-Path $PgConfig) {
    Write-Log "Aislando PostgreSQL a localhost."
    $PgConfigText = Get-Content -LiteralPath $PgConfig -Raw

    if ($PgConfigText -match '(?m)^\s*#?\s*listen_addresses\s*=.*$') {
        $PgConfigText = [regex]::Replace(
            $PgConfigText,
            '(?m)^\s*#?\s*listen_addresses\s*=.*$',
            "listen_addresses = '127.0.0.1'",
            1
        )
    }
    else {
        $PgConfigText += "`r`nlisten_addresses = '127.0.0.1'`r`n"
    }

    if ($PgConfigText -match '(?m)^\s*#?\s*port\s*=.*$') {
        $PgConfigText = [regex]::Replace(
            $PgConfigText,
            '(?m)^\s*#?\s*port\s*=.*$',
            "port = 54329",
            1
        )
    }
    else {
        $PgConfigText += "`r`nport = 54329`r`n"
    }

    Set-Content -LiteralPath $PgConfig -Value $PgConfigText -Encoding UTF8
    Restart-Service "RackNovaPostgreSQL16"
}

$PgConfig = Join-Path $PgData "postgresql.conf"

if (Test-Path $PgConfig) {
    Write-Log "Aislando PostgreSQL a localhost."
    $PgConfigText = Get-Content -LiteralPath $PgConfig -Raw

    if ($PgConfigText -match '(?m)^\s*#?\s*listen_addresses\s*=.*$') {
        $PgConfigText = [regex]::Replace(
            $PgConfigText,
            '(?m)^\s*#?\s*listen_addresses\s*=.*$',
            "listen_addresses = '127.0.0.1'",
            1
        )
    }
    else {
        $PgConfigText += "`r`nlisten_addresses = '127.0.0.1'`r`n"
    }

    if ($PgConfigText -match '(?m)^\s*#?\s*port\s*=.*$') {
        $PgConfigText = [regex]::Replace(
            $PgConfigText,
            '(?m)^\s*#?\s*port\s*=.*$',
            "port = 54329",
            1
        )
    }
    else {
        $PgConfigText += "`r`nport = 54329`r`n"
    }

    Set-Content -LiteralPath $PgConfig -Value $PgConfigText -Encoding UTF8
    Restart-Service "RackNovaPostgreSQL16"
}

if (-not (Test-Path $Psql)) {
    throw "No encontré psql.exe en $PgInstall"
}

if (-not (Test-Path (Join-Path $ConfigDir "secrets.dat"))) {
    $env:PGPASSWORD = $PgSuperPassword

    $RoleSql = @'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='racknova_app') THEN
        CREATE ROLE racknova_app LOGIN PASSWORD '__APP_PASSWORD__';
    ELSE
        ALTER ROLE racknova_app WITH LOGIN PASSWORD '__APP_PASSWORD__';
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
        app_version = "native-f1"
    } | ConvertTo-Json | Set-Content -LiteralPath $Bootstrap -Encoding UTF8

    & $Ctl bootstrap-secrets --file $Bootstrap
    if ($LASTEXITCODE -ne 0) {
        throw "RackNovaCtl no pudo proteger los secretos."
    }
}
else {
    Write-Log "Secretos DPAPI existentes detectados."
}

Write-Log "Inicializando esquema RackNova."
& $Ctl init-schema
if ($LASTEXITCODE -ne 0) {
    throw "Falló init-schema."
}

$ExistingService = Get-Service -Name "RackNovaLocal" -ErrorAction SilentlyContinue
if (-not $ExistingService) {
    Write-Log "Registrando servicio RackNova Local."
    & $ServiceExe --startup auto install
    if ($LASTEXITCODE -ne 0) {
        throw "No pude registrar RackNovaLocal."
    }
}

& sc.exe config RackNovaLocal depend= RackNovaPostgreSQL16 | Out-Null
& sc.exe failure RackNovaLocal reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
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

Write-Log "Ejecutando health check."
& $Ctl health
$HealthCode = $LASTEXITCODE

if ($HealthCode -ne 0) {
    Write-Log "Health check falló; generando diagnóstico."
    $Diag = & $Ctl diagnose
    throw "RackNova Local no pasó health check. Diagnóstico: $Diag"
}

Write-Log "RackNova Native F1 instalado correctamente."
Write-Log "Dashboard Local: http://127.0.0.1:8000/ui/"
Write-Log "F1 instala runtime nativo; activación comercial llegará en F2."
