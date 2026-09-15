param(
    [int]$PostgresPort = 54339,
    [int]$ApiPort = 8010
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Join-Path $env:LOCALAPPDATA "RackNovaDev"
$PgData = Join-Path $DevRoot "postgres-data"
$PgLog = Join-Path $DevRoot "postgres.log"
$SecretsFile = Join-Path $DevRoot "dev-secrets.ps1"

function Find-PostgresBin {
    if ($env:RACKNOVA_DEV_PG_BIN -and (Test-Path $env:RACKNOVA_DEV_PG_BIN)) {
        return $env:RACKNOVA_DEV_PG_BIN
    }

    $NativeBin = "C:\Program Files\RackNova\PostgreSQL\bin"
    if (Test-Path (Join-Path $NativeBin "initdb.exe")) {
        return $NativeBin
    }

    $InitdbCommand = Get-Command initdb.exe -ErrorAction SilentlyContinue
    if ($InitdbCommand) {
        return Split-Path -Parent $InitdbCommand.Source
    }

    throw "No encontré PostgreSQL. Instala RackNova Local o define RACKNOVA_DEV_PG_BIN."
}

function New-LocalPassword {
    return ([Guid]::NewGuid().ToString("N") + "Aa1!")
}

New-Item -ItemType Directory -Force -Path $DevRoot | Out-Null

$PgBin = Find-PostgresBin
$Initdb = Join-Path $PgBin "initdb.exe"
$PgCtl = Join-Path $PgBin "pg_ctl.exe"
$Psql = Join-Path $PgBin "psql.exe"

if (-not (Test-Path $SecretsFile)) {
    $AdminPassword = New-LocalPassword
    $AppPassword = New-LocalPassword
    $SecretContent = @(
        "# Credenciales SOLO para el entorno local de desarrollo RackNova.",
        "# No subir este archivo a Git.",
        ('$RackNovaDevAdminPassword = ''' + $AdminPassword + ''''),
        ('$RackNovaDevAppPassword = ''' + $AppPassword + '''')
    )
    $SecretContent | Set-Content -LiteralPath $SecretsFile -Encoding UTF8
}

. $SecretsFile

if (-not $RackNovaDevAdminPassword -or -not $RackNovaDevAppPassword) {
    throw "El archivo de credenciales de desarrollo está incompleto: $SecretsFile"
}

if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
    Write-Host "Inicializando PostgreSQL de desarrollo aislado..."
    New-Item -ItemType Directory -Force -Path $PgData | Out-Null

    $PwFile = Join-Path $DevRoot "initdb-password.tmp"
    try {
        Set-Content -LiteralPath $PwFile -Value $RackNovaDevAdminPassword -NoNewline -Encoding ASCII
        $InitArgs = @("-D", $PgData, "-U", "postgres", "-A", "scram-sha-256", "--pwfile=$PwFile")
        & $Initdb @InitArgs
        if ($LASTEXITCODE -ne 0) {
            throw "initdb devolvió código $LASTEXITCODE"
        }
    }
    finally {
        Remove-Item -LiteralPath $PwFile -Force -ErrorAction SilentlyContinue
    }

    Add-Content -LiteralPath (Join-Path $PgData "postgresql.conf") -Value @"

# RackNovaDev managed settings
listen_addresses = '127.0.0.1'
port = $PostgresPort
"@
}

& $PgCtl -D $PgData status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Iniciando PostgreSQL de desarrollo en 127.0.0.1:$PostgresPort..."
    & $PgCtl -D $PgData -l $PgLog start
    if ($LASTEXITCODE -ne 0) {
        throw "No pude iniciar PostgreSQL de desarrollo. Revisa $PgLog"
    }
    Start-Sleep -Seconds 2
}

$env:PGPASSWORD = $RackNovaDevAdminPassword

$RoleExists = (& $Psql -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='racknova_dev';" 2>$null).Trim()
if ($RoleExists -ne "1") {
    & $Psql -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE racknova_dev LOGIN PASSWORD '$RackNovaDevAppPassword';"
    if ($LASTEXITCODE -ne 0) {
        throw "No pude crear el usuario PostgreSQL racknova_dev."
    }
}

$DbExists = (& $Psql -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='racknova_dev';" 2>$null).Trim()
if ($DbExists -ne "1") {
    & $Psql -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE racknova_dev OWNER racknova_dev;"
    if ($LASTEXITCODE -ne 0) {
        throw "No pude crear la base racknova_dev."
    }
}

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Py) {
        & $Py.Source -3 -m venv (Join-Path $RepoRoot ".venv")
    }
    else {
        $SystemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $SystemPython) {
            throw "No encontré Python. Instala Python 3 o crea .venv manualmente."
        }
        & $SystemPython.Source -m venv (Join-Path $RepoRoot ".venv")
    }

    if ($LASTEXITCODE -ne 0) {
        throw "No pude crear el entorno virtual de Python."
    }
}

Write-Host "Preparando dependencias del backend..."
& $Python -m pip install -q -r (Join-Path $RepoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Falló la instalación de dependencias."
}

$env:DATABASE_URL = "postgresql://racknova_dev:$RackNovaDevAppPassword@127.0.0.1:$PostgresPort/racknova_dev"
$env:RACKNOVA_MODE = "local"
$env:RACKNOVA_EMPRESA_ID = "11111111-1111-4111-8111-111111111111"
$env:RACKNOVA_NODE_CODE = "DEV-$($env:COMPUTERNAME.ToUpper())"
$env:RACKNOVA_NODE_NAME = "RackNova Desarrollo - $env:COMPUTERNAME"
$env:RACKNOVA_NODE_TYPE = "LOCAL_SERVER"
$env:RACKNOVA_SYNC_AUTOSTART = "false"
$env:RACKNOVA_SYNC_SECRET = ""
$env:RACKNOVA_CLOUD_URL = ""
$env:SECRET_KEY = "racknova-local-development-only"

Push-Location $RepoRoot
try {
    Write-Host "Inicializando esquema base..."
    & $Python -c "import main; main.on_startup()"
    if ($LASTEXITCODE -ne 0) {
        throw "No pude inicializar el esquema base."
    }

    $env:PGPASSWORD = $RackNovaDevAppPassword
    $Migrations = @(
        "001_multiempresa_fase1.sql",
        "002_multiempresa_fase2_local_first.sql",
        "003_unidad_manejo.sql"
    )

    foreach ($Migration in $Migrations) {
        $MigrationPath = Join-Path $RepoRoot $Migration
        if (Test-Path $MigrationPath) {
            Write-Host "Aplicando $Migration..."
            & $Psql -h 127.0.0.1 -p $PostgresPort -U racknova_dev -d racknova_dev -v ON_ERROR_STOP=1 -f $MigrationPath
            if ($LASTEXITCODE -ne 0) {
                throw "Falló la migración $Migration."
            }
        }
    }

    Write-Host ""
    Write-Host "============================================"
    Write-Host " RACKNOVA DESARROLLO LOCAL - COSTO CERO"
    Write-Host "============================================"
    Write-Host "API:        http://127.0.0.1:$ApiPort"
    Write-Host "PostgreSQL: 127.0.0.1:$PostgresPort / racknova_dev"
    Write-Host "Sync Cloud: DESACTIVADO"
    Write-Host "Usuario:    admin@racknova.com"
    Write-Host "Password:   admin123"
    Write-Host ""
    Write-Host "Este entorno es independiente de producción."
    Write-Host "Ctrl+C detiene la API."
    Write-Host "Para detener PostgreSQL dev: scripts\stop-dev-zero-cost.ps1"
    Write-Host ""

    & $Python -m uvicorn main:app --host 127.0.0.1 --port $ApiPort --reload
}
finally {
    Pop-Location
}
