param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$PgData = Join-Path $PgRoot "data"
$PgLogDir = Join-Path $PgRoot "logs"
$PgInstall = Join-Path $InstallDir "PostgreSQL"

$Ctl = Join-Path $InstallDir "RackNovaCtl.exe"
$ServiceExe = Join-Path $InstallDir "RackNovaLocalService.exe"
$InitDb = Join-Path $PgInstall "bin\initdb.exe"
$PgCtl = Join-Path $PgInstall "bin\pg_ctl.exe"
$PgIsReady = Join-Path $PgInstall "bin\pg_isready.exe"
$Psql = Join-Path $PgInstall "bin\psql.exe"
$Createdb = Join-Path $PgInstall "bin\createdb.exe"
$PostgresExe = Join-Path $PgInstall "bin\postgres.exe"
$SecretsPath = Join-Path $ConfigDir "secrets.dat"

$PostgresServiceName = "RackNovaPostgreSQL16"
$PostgresServiceAccount = "NT AUTHORITY\NetworkService"
$NetworkServiceSid = "*S-1-5-20"

$Log = Join-Path $LogDir ("native-install-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

New-Item -ItemType Directory -Force -Path `
    $ConfigDir, $LogDir, $PgRoot, $PgLogDir | Out-Null

function Write-Log([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $Line | Tee-Object -FilePath $Log -Append | Write-Host
}

function New-RackNovaPassword {
    return ("Rn!" + [Guid]::NewGuid().ToString("N") + "Aa1" + [Guid]::NewGuid().ToString("N"))
}

function Secure-TempFile([string]$Path) {
    $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

    if (Test-Path -LiteralPath $Path) {
        & takeown.exe /F $Path /A | Out-Null
        & icacls.exe $Path /grant:r `
            "*S-1-5-18:F" `
            "*S-1-5-32-544:F" `
            "*${CurrentSid}:F" | Out-Null
        Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
    }

    New-Item -ItemType File -Force -Path $Path | Out-Null
    & icacls.exe $Path /inheritance:r /grant:r `
        "*S-1-5-18:F" `
        "*S-1-5-32-544:F" `
        "*${CurrentSid}:F" | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude proteger el archivo temporal: $Path"
    }
}

function Remove-LegacyInstallerSecret {
    $Legacy = Join-Path $ConfigDir "postgres-install-options.tmp"
    if (-not (Test-Path -LiteralPath $Legacy)) { return }

    Write-Log "Eliminando archivo temporal legacy de PostgreSQL."
    try {
        & takeown.exe /F $Legacy /A | Out-Null
        & icacls.exe $Legacy /grant:r `
            "*S-1-5-18:F" `
            "*S-1-5-32-544:F" | Out-Null
        Remove-Item -LiteralPath $Legacy -Force -ErrorAction Stop
    }
    catch {
        throw "No pude eliminar el archivo temporal legacy de PostgreSQL."
    }
}

function Protect-RackNovaBootstrapSecrets {
    param(
        [Parameter(Mandatory=$true)][string]$DbPassword,
        [Parameter(Mandatory=$true)][string]$PgSuperPassword,
        [Parameter(Mandatory=$true)][string]$JwtSecret
    )

    $Bootstrap = Join-Path $ConfigDir "bootstrap-secrets.tmp.json"
    Secure-TempFile $Bootstrap
    try {
        $BootstrapObject = [ordered]@{
            db_password       = $DbPassword
            pg_super_password = $PgSuperPassword
            jwt_secret        = $JwtSecret
            node_credential   = ""
            activated         = $false
            empresa_id        = "11111111-1111-4111-8111-111111111111"
            node_code         = ("LOCAL-" + $env:COMPUTERNAME.ToUpper())
            node_name         = ("RackNova Local - " + $env:COMPUTERNAME)
            cloud_url         = ""
            db_port           = 54329
            app_version       = "native-f1.9.3-recovery"
        }

        $Json = $BootstrapObject | ConvertTo-Json
        $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($Bootstrap, $Json, $Utf8NoBom)

        & $Ctl bootstrap-secrets --file $Bootstrap
        if ($LASTEXITCODE -ne 0) {
            throw "RackNovaCtl no pudo proteger los secretos."
        }
        if (-not (Test-Path -LiteralPath $SecretsPath)) {
            throw "RackNovaCtl no creó secrets.dat."
        }
    }
    finally {
        Remove-Item -LiteralPath $Bootstrap -Force -ErrorAction SilentlyContinue
    }
}

function Grant-PostgresRuntimeAcl {
    New-Item -ItemType Directory -Force -Path $PgRoot, $PgData, $PgLogDir | Out-Null

    # NetworkService es deliberado: PostgreSQL para Windows rechaza tokens
    # administrativos como LocalSystem. El SID evita depender del idioma de Windows.
    & icacls.exe $PgRoot /inheritance:e /grant:r `
        "${NetworkServiceSid}:(OI)(CI)F" `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No pude otorgar permisos de PostgreSQL a NetworkService."
    }

    # El servicio sólo necesita lectura/ejecución de los binarios instalados.
    & icacls.exe $PgInstall /grant:r `
        "${NetworkServiceSid}:(OI)(CI)RX" `
        /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No pude otorgar lectura de los binarios PostgreSQL a NetworkService."
    }
}

function Write-PostgresServiceDiagnostics {
    try {
        $ServiceConfig = (& sc.exe qc $PostgresServiceName 2>&1 | Out-String).Trim()
        if ($ServiceConfig) {
            Write-Log ("POSTGRES SERVICE CONFIG: " + ($ServiceConfig -replace "`r?`n", " | "))
        }
    } catch {}

    foreach ($EventLogName in @("Application", "System")) {
        try {
            $Since = (Get-Date).AddMinutes(-10)
            $Events = Get-WinEvent `
                -FilterHashtable @{ LogName = $EventLogName; StartTime = $Since } `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.ProviderName -match "PostgreSQL|Service Control Manager" -or
                    $_.Message -match "RackNovaPostgreSQL16|postgres|PostgreSQL"
                } |
                Select-Object -First 12

            foreach ($Event in $Events) {
                $Message = ($Event.Message -replace "`r?`n", " ").Trim()
                Write-Log ("WINDOWS {0} EVENT {1}/{2}: {3}" -f `
                    $EventLogName, $Event.ProviderName, $Event.Id, $Message)
            }
        } catch {}
    }

    try {
        Get-ChildItem -LiteralPath $PgLogDir -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 3 |
            ForEach-Object {
                $Tail = (Get-Content -LiteralPath $_.FullName -Tail 80 -ErrorAction SilentlyContinue | Out-String).Trim()
                if ($Tail) {
                    Write-Log ("POSTGRES LOG " + $_.Name + ": " + ($Tail -replace "`r?`n", " | "))
                }
            }
    } catch {}
}

function Ensure-PostgresServiceAccount {
    Grant-PostgresRuntimeAcl

    & sc.exe config `
        $PostgresServiceName `
        obj= $PostgresServiceAccount `
        start= auto | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude configurar PostgreSQL con la cuenta NetworkService."
    }
}

function Register-PostgresService {
    $Existing = Get-Service -Name $PostgresServiceName -ErrorAction SilentlyContinue
    if ($Existing) {
        try { Stop-Service -Name $PostgresServiceName -Force -ErrorAction SilentlyContinue } catch {}
        & $PgCtl unregister -N $PostgresServiceName 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & sc.exe delete $PostgresServiceName | Out-Null
        }
        for ($i = 0; $i -lt 20; $i++) {
            if (-not (Get-Service -Name $PostgresServiceName -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        }
    }

    Grant-PostgresRuntimeAcl
    Write-Log "Registrando RackNovaPostgreSQL16 con NetworkService."
    & $PgCtl register `
        -N $PostgresServiceName `
        -D $PgData `
        -S auto `
        -U $PostgresServiceAccount

    if ($LASTEXITCODE -ne 0) {
        throw "pg_ctl register terminó con código $LASTEXITCODE."
    }

    Ensure-PostgresServiceAccount
    & sc.exe failure $PostgresServiceName `
        reset= 86400 `
        actions= restart/5000/restart/15000/restart/60000 | Out-Null
    & sc.exe failureflag $PostgresServiceName 1 | Out-Null
}

function Wait-PostgresReady([int]$Attempts = 60) {
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        & $PgIsReady -h 127.0.0.1 -p 54329 -t 2 | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-RackNovaPostgresService {
    Ensure-PostgresServiceAccount
    $Service = Get-Service -Name $PostgresServiceName -ErrorAction Stop

    if ($Service.Status -ne "Running") {
        Write-Log "Iniciando PostgreSQL con NetworkService."
        try {
            Start-Service $PostgresServiceName -ErrorAction Stop
        }
        catch {
            Write-PostgresServiceDiagnostics
            throw "Windows no pudo iniciar RackNovaPostgreSQL16. Revisa $Log"
        }
    }

    if (-not (Wait-PostgresReady -Attempts 60)) {
        Write-PostgresServiceDiagnostics
        throw "PostgreSQL no quedó listo en 127.0.0.1:54329."
    }
}

Write-Log "RackNova Native F1.9.3 portable: configuración iniciada."

trap {
    try {
        Write-Log ("ERROR: " + $_.Exception.Message)
        if ($_.ScriptStackTrace) {
            Write-Log ("STACK: " + ($_.ScriptStackTrace -replace "`r?`n", " | "))
        }
        Write-PostgresServiceDiagnostics
    } catch {}
    exit 1
}

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "RackNova Setup requiere privilegios de administrador."
}
Write-Log "Privilegios administrativos confirmados."

Remove-LegacyInstallerSecret
foreach ($Required in @($Ctl, $ServiceExe, $InitDb, $PgCtl, $PgIsReady, $Psql, $Createdb, $PostgresExe)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Falta componente requerido: $Required"
    }
}

$PgService = Get-Service -Name $PostgresServiceName -ErrorAction SilentlyContinue
if ($PgService -and -not (Test-Path -LiteralPath $SecretsPath)) {
    throw "Existe RackNovaPostgreSQL16 pero faltan secretos DPAPI. No continuaré para evitar perder acceso a la base."
}

$PgSuperPassword = New-RackNovaPassword
$AppPassword = New-RackNovaPassword
$JwtSecret = (New-RackNovaPassword) + (New-RackNovaPassword)

if (-not $PgService) {
    Write-Log "Preparando PostgreSQL portable; no se ejecutará instalador EDB."

    if (Test-Path -LiteralPath $PgData) {
        $PgVersionFile = Join-Path $PgData "PG_VERSION"
        if (-not (Test-Path -LiteralPath $PgVersionFile)) {
            Write-Log "Eliminando directorio PostgreSQL incompleto."
            Remove-Item -LiteralPath $PgData -Recurse -Force
        }
        else {
            throw "Existe un cluster PostgreSQL sin servicio registrado. Requiere recuperación antes de continuar."
        }
    }

    New-Item -ItemType Directory -Force -Path $PgData, $PgLogDir | Out-Null
    Write-Log "Protegiendo credenciales locales antes de inicializar PostgreSQL."
    Protect-RackNovaBootstrapSecrets `
        -DbPassword $AppPassword `
        -PgSuperPassword $PgSuperPassword `
        -JwtSecret $JwtSecret

    $PwFile = Join-Path $ConfigDir "postgres-super.tmp"
    Secure-TempFile $PwFile
    try {
        Set-Content -LiteralPath $PwFile -Value $PgSuperPassword -Encoding ASCII -NoNewline
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
        Remove-Item -LiteralPath $PwFile -Force -ErrorAction SilentlyContinue
    }

    $PgConfig = Join-Path $PgData "postgresql.conf"
    $PgLogForConfig = ($PgLogDir -replace "\\", "/").Replace("'", "''")
    @"

# RackNova Local F1.9.3
listen_addresses = '127.0.0.1'
port = 54329
password_encryption = 'scram-sha-256'
logging_collector = on
log_directory = '$PgLogForConfig'
log_filename = 'postgresql-%Y%m%d-%H%M%S.log'
log_min_messages = info
"@ | Add-Content -LiteralPath $PgConfig -Encoding UTF8

    $PgHba = Join-Path $PgData "pg_hba.conf"
    @"
# RackNova Local - solo localhost
host    all    all    127.0.0.1/32    scram-sha-256
host    all    all    ::1/128         scram-sha-256
"@ | Set-Content -LiteralPath $PgHba -Encoding ASCII

    Grant-PostgresRuntimeAcl
    Register-PostgresService
    Start-RackNovaPostgresService
    Write-Log "PostgreSQL portable listo en 127.0.0.1:54329."

    $env:PGPASSWORD = $PgSuperPassword
    try {
        $EscapedAppPassword = $AppPassword.Replace("'", "''")
        $RoleSql = @"
DO `$`$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='racknova_app') THEN
        CREATE ROLE racknova_app LOGIN PASSWORD '$EscapedAppPassword';
    ELSE
        ALTER ROLE racknova_app WITH LOGIN PASSWORD '$EscapedAppPassword';
    END IF;
END
`$`$;
"@
        & $Psql -h 127.0.0.1 -p 54329 -U racknova_super -d postgres -v ON_ERROR_STOP=1 -c $RoleSql
        if ($LASTEXITCODE -ne 0) {
            throw "No pude crear/actualizar racknova_app."
        }

        $Exists = & $Psql `
            -h 127.0.0.1 -p 54329 `
            -U racknova_super -d postgres `
            -tAc "SELECT 1 FROM pg_database WHERE datname='racknova'"
        if (($Exists | Out-String).Trim() -ne "1") {
            & $Createdb `
                -h 127.0.0.1 -p 54329 `
                -U racknova_super `
                -O racknova_app racknova
            if ($LASTEXITCODE -ne 0) {
                throw "No pude crear la base racknova."
            }
        }
    }
    finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }

    Write-Log "Credenciales DPAPI protegidas y PostgreSQL inicializado."
}
else {
    Write-Log "RackNovaPostgreSQL16 existente detectado; normalizando cuenta a NetworkService."
    Start-RackNovaPostgresService
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

$RackNovaControlKey = "HKLM:\SYSTEM\CurrentControlSet\Control"
$RackNovaPipeTimeout = (
    Get-ItemProperty `
        -Path $RackNovaControlKey `
        -Name "ServicesPipeTimeout" `
        -ErrorAction SilentlyContinue
).ServicesPipeTimeout

if ((-not $RackNovaPipeTimeout) -or ([int64]$RackNovaPipeTimeout -lt 120000)) {
    New-ItemProperty `
        -Path $RackNovaControlKey `
        -Name "ServicesPipeTimeout" `
        -PropertyType DWord `
        -Value 120000 `
        -Force | Out-Null
    Write-Log "ServicesPipeTimeout protegido a 120000 ms."
}

& sc.exe config RackNovaLocal depend= $PostgresServiceName | Out-Null
& sc.exe config RackNovaLocal start= delayed-auto | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "No pude configurar RackNovaLocal como inicio automático retrasado."
}
& sc.exe failure RackNovaLocal `
    reset= 86400 `
    actions= restart/5000/restart/15000/restart/60000 | Out-Null
& sc.exe failureflag RackNovaLocal 1 | Out-Null

& $ServiceExe start | Out-Null

$ServiceReady = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    try {
        $Response = Invoke-WebRequest `
            -Uri "http://127.0.0.1:8000/racknova-native/health" `
            -UseBasicParsing `
            -TimeoutSec 3
        if ($Response.StatusCode -eq 200) {
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

$FirewallRule = Get-NetFirewallRule -DisplayName "RackNova Local" -ErrorAction SilentlyContinue
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

Write-Log "RackNova Native F1.9.3 portable instalado correctamente."
Write-Log "Dashboard Local: http://127.0.0.1:8000/ui/"
exit 0
