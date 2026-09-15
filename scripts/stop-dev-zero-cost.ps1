param(
    [int]$PostgresPort = 54339
)

$ErrorActionPreference = "Stop"

$DevRoot = Join-Path $env:LOCALAPPDATA "RackNovaDev"
$PgData = Join-Path $DevRoot "postgres-data"

function Find-PostgresBin {
    if ($env:RACKNOVA_DEV_PG_BIN -and (Test-Path $env:RACKNOVA_DEV_PG_BIN)) {
        return $env:RACKNOVA_DEV_PG_BIN
    }

    $NativeBin = "C:\Program Files\RackNova\PostgreSQL\bin"
    if (Test-Path (Join-Path $NativeBin "pg_ctl.exe")) {
        return $NativeBin
    }

    $PgCtlCommand = Get-Command pg_ctl.exe -ErrorAction SilentlyContinue
    if ($PgCtlCommand) {
        return Split-Path -Parent $PgCtlCommand.Source
    }

    throw "No encontré pg_ctl.exe."
}

if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
    Write-Host "No existe un PostgreSQL RackNovaDev inicializado."
    exit 0
}

$PgBin = Find-PostgresBin
$PgCtl = Join-Path $PgBin "pg_ctl.exe"

& $PgCtl -D $PgData status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Deteniendo PostgreSQL de desarrollo..."
    & $PgCtl -D $PgData stop -m fast
}
else {
    Write-Host "PostgreSQL de desarrollo ya está detenido."
}
