param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigPath = Join-Path $ProgramDataRoot "Config\config.json"
$SecretsPath = Join-Path $ProgramDataRoot "Config\secrets.dat"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$Log = Join-Path $LogDir (
    "bootstrap-cloud-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log"
)
$Psql = Join-Path $InstallDir "PostgreSQL\bin\psql.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $Line | Out-File -LiteralPath $Log -Append -Encoding utf8
}

function Quote-Ident([string]$Name) {
    if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "Identificador SQL no permitido: $Name"
    }
    return '"' + $Name + '"'
}

function Sql-String([string]$Value) {
    return "'" + (($Value ?? "") -replace "'", "''") + "'"
}

function New-JsonDollarLiteral($Value) {
    $Json = $Value | ConvertTo-Json -Depth 100 -Compress
    $Tag = "rn" + [Guid]::NewGuid().ToString("N")
    return ('$' + $Tag + '$' + $Json + '$' + $Tag + '$')
}

function Invoke-PsqlScript {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Sql,
        [switch]$AllowFailure
    )

    $TempDir = Join-Path $ProgramDataRoot "InstallerTemp"
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    $Temp = Join-Path $TempDir ("sql-" + [Guid]::NewGuid().ToString("N") + ".sql")
    [System.IO.File]::WriteAllText(
        $Temp,
        $Sql,
        (New-Object System.Text.UTF8Encoding($false))
    )

    try {
        $Output = (& $Psql `
            -h 127.0.0.1 `
            -p 54329 `
            -U racknova_app `
            -d racknova `
            -v ON_ERROR_STOP=1 `
            -f $Temp 2>&1 | Out-String).Trim()
        $Code = $LASTEXITCODE

        if ($Code -ne 0 -and -not $AllowFailure) {
            if ($Output) {
                Write-Log ("PSQL ERROR: " + ($Output -replace "`r?`n", " | "))
            }
            throw "PostgreSQL rechazó una operación del snapshot. Código: $Code"
        }

        return [pscustomobject]@{
            Code = $Code
            Output = $Output
        }
    }
    finally {
        Remove-Item -LiteralPath $Temp -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-PsqlScalar([string]$Sql) {
    $Output = (& $Psql `
        -h 127.0.0.1 `
        -p 54329 `
        -U racknova_app `
        -d racknova `
        -t `
        -A `
        -v ON_ERROR_STOP=1 `
        -c $Sql 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Falló consulta PostgreSQL: $Output"
    }
    return $Output
}

function Insert-JsonRows {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Table,
        [Parameter(Mandatory=$true)]
        [object[]]$Rows,
        [switch]$AllowFailure
    )

    if (-not $Rows -or $Rows.Count -eq 0) {
        return [pscustomobject]@{ Code = 0; Output = "" }
    }

    $QTable = Quote-Ident $Table
    $Builder = New-Object System.Text.StringBuilder
    [void]$Builder.AppendLine("BEGIN;")
    [void]$Builder.AppendLine("SELECT set_config('app.racknova_sync_apply','1',true);")

    foreach ($Row in $Rows) {
        $Literal = New-JsonDollarLiteral $Row
        [void]$Builder.AppendLine(
            "INSERT INTO $QTable SELECT src.* FROM jsonb_populate_record(NULL::$QTable, CAST($Literal AS JSONB)) AS src ON CONFLICT DO NOTHING;"
        )
    }

    [void]$Builder.AppendLine("COMMIT;")
    return Invoke-PsqlScript -Sql $Builder.ToString() -AllowFailure:$AllowFailure
}

try {
    Write-Log "RackNova bootstrap Cloud definitivo iniciado."

    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "No existe config.json después de activar RackNova Cloud."
    }
    if (-not (Test-Path -LiteralPath $SecretsPath)) {
        throw "No existe secrets.dat después de configurar RackNova Local."
    }
    if (-not (Test-Path -LiteralPath $Psql)) {
        throw "No existe psql.exe en la instalación RackNova."
    }

    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($Config.activated -ne $true) {
        throw "RackNova Local todavía no está activado con Cloud."
    }

    Add-Type -AssemblyName System.Security
    $Encoded = [System.IO.File]::ReadAllText($SecretsPath).Trim()
    $Encrypted = [Convert]::FromBase64String($Encoded)
    $Plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $Encrypted,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $Secrets = ([System.Text.Encoding]::UTF8.GetString($Plain)) | ConvertFrom-Json

    $DbPassword = [string]$Secrets.db_password
    $SyncSecret = [string]$Secrets.node_credential
    if (-not $DbPassword) { throw "db_password no está disponible." }
    if (-not $SyncSecret) { throw "node_credential no está disponible." }

    $env:PGPASSWORD = $DbPassword

    $CloudUrl = ([string]$Config.cloud_url).TrimEnd('/')
    $EmpresaId = [string]$Config.empresa_id
    $NodeCode = [string]$Config.node_code
    $NodeName = [string]$Config.node_name

    Write-Log "Solicitando snapshot actual de Cloud."
    $Headers = @{
        "X-RackNova-Sync-Secret" = $SyncSecret
        "Accept" = "application/json"
    }
    $BootstrapUrl = (
        $CloudUrl + "/sync/v1/bootstrap/export?empresa_id=" +
        [uri]::EscapeDataString($EmpresaId) +
        "&max_rows_per_table=100000"
    )
    $Package = Invoke-RestMethod `
        -Method GET `
        -Uri $BootstrapUrl `
        -Headers $Headers `
        -TimeoutSec 180

    if ([int]$Package.bootstrap_version -ne 1) {
        throw "Versión de bootstrap Cloud incompatible: $($Package.bootstrap_version)"
    }
    if ([string]$Package.empresa_id -ne $EmpresaId) {
        throw "El snapshot Cloud pertenece a otra empresa."
    }

    Write-Log ("Snapshot recibido. Filas comerciales=" + $Package.total_commercial_rows)

    # ------------------------------------------------------------
    # Reconstruir tablas lazy que no existan todavía en Local.
    # ------------------------------------------------------------
    $CreatedTables = @()
    foreach ($Item in @($Package.tables)) {
        $Table = [string]$Item.table
        [void](Quote-Ident $Table)
        $Exists = Invoke-PsqlScalar (
            "SELECT COALESCE(to_regclass('public." + $Table + "')::text,'');"
        )
        if ($Exists) {
            continue
        }

        Write-Log "Creando tabla lazy $Table desde esquema Cloud."
        foreach ($Statement in @($Item.schema.pre_sql)) {
            if ([string]$Statement) {
                Invoke-PsqlScript -Sql ([string]$Statement) | Out-Null
            }
        }
        Invoke-PsqlScript -Sql ([string]$Item.schema.create_sql) | Out-Null
        $CreatedTables += $Item
    }

    foreach ($Item in $CreatedTables) {
        foreach ($Statement in @($Item.schema.constraints_sql)) {
            if ([string]$Statement) {
                Invoke-PsqlScript -Sql ([string]$Statement) | Out-Null
            }
        }
        foreach ($Statement in @($Item.schema.indexes_sql)) {
            if ([string]$Statement) {
                Invoke-PsqlScript -Sql ([string]$Statement) | Out-Null
            }
        }
    }

    # ------------------------------------------------------------
    # Exigir base comercial fresca. No mezclamos snapshot con datos viejos.
    # ------------------------------------------------------------
    $Infra = @(
        'empresas','empresa_usuarios','usuario','racknova_platform_admins',
        'racknova_nodos','racknova_sync_outbox','racknova_sync_estado',
        'racknova_sync_inbox','racknova_sync_cursor','racknova_sync_id_map',
        'racknova_sync_pos_cursor'
    )
    $TenantTableSql = @"
SELECT DISTINCT t.table_name
FROM information_schema.tables t
JOIN information_schema.columns c
  ON c.table_schema=t.table_schema AND c.table_name=t.table_name
WHERE t.table_schema='public'
  AND t.table_type='BASE TABLE'
  AND c.column_name='empresa_id'
ORDER BY t.table_name;
"@
    $TenantTablesRaw = & $Psql `
        -h 127.0.0.1 -p 54329 -U racknova_app -d racknova `
        -t -A -v ON_ERROR_STOP=1 -c $TenantTableSql
    if ($LASTEXITCODE -ne 0) { throw "No pude inspeccionar tablas Local." }

    $Occupied = @()
    foreach ($RawTable in @($TenantTablesRaw)) {
        $Table = ([string]$RawTable).Trim()
        if (-not $Table -or $Infra -contains $Table) { continue }
        $QTable = Quote-Ident $Table
        $Count = [int64](Invoke-PsqlScalar "SELECT COUNT(*) FROM $QTable;")
        if ($Count -gt 0) {
            $Occupied += ("$Table=$Count")
        }
    }
    if ($Occupied.Count -gt 0) {
        throw (
            "La base Local no está fresca. Tablas con datos: " +
            ($Occupied -join ', ')
        )
    }

    # ------------------------------------------------------------
    # Limpiar únicamente residuos de inicialización e infraestructura sync.
    # ------------------------------------------------------------
    $CleanupSql = @"
BEGIN;
DO `$rn`$
BEGIN
    IF to_regclass('public.racknova_sync_pos_cursor') IS NOT NULL THEN
        DELETE FROM racknova_sync_pos_cursor;
    END IF;
END
`$rn`$;
DELETE FROM racknova_sync_estado;
DELETE FROM racknova_sync_inbox;
DELETE FROM racknova_sync_cursor;
DELETE FROM racknova_sync_id_map;
DELETE FROM racknova_sync_outbox;
DELETE FROM racknova_nodos;
DELETE FROM empresa_usuarios;
DELETE FROM racknova_platform_admins;
DELETE FROM usuario;
DELETE FROM empresas;
COMMIT;
"@
    Invoke-PsqlScript -Sql $CleanupSql | Out-Null

    # Empresa -> usuarios -> membresías.
    Insert-JsonRows -Table "empresas" -Rows @($Package.company) | Out-Null
    Insert-JsonRows -Table "usuario" -Rows @($Package.users) | Out-Null
    Insert-JsonRows -Table "empresa_usuarios" -Rows @($Package.memberships) | Out-Null

    # ------------------------------------------------------------
    # Importar tablas comerciales por rondas para respetar FKs.
    # Cada tabla se importa en una sola transacción: si falta un padre,
    # se revierte completa y se reintenta en la siguiente ronda.
    # ------------------------------------------------------------
    $Pending = @(
        $Package.tables |
            Where-Object { [int]$_.row_count -gt 0 }
    )
    $ImportedTables = 0

    for ($Round = 1; $Round -le 30 -and $Pending.Count -gt 0; $Round++) {
        $NextPending = @()
        $Progress = 0

        foreach ($Item in $Pending) {
            $Table = [string]$Item.table
            $Result = Insert-JsonRows `
                -Table $Table `
                -Rows @($Item.rows) `
                -AllowFailure

            if ($Result.Code -eq 0) {
                $ImportedTables++
                $Progress++
                Write-Log "Snapshot importado: $Table ($($Item.row_count) filas)."
            }
            else {
                $NextPending += $Item
            }
        }

        $Pending = @($NextPending)
        if ($Pending.Count -gt 0 -and $Progress -eq 0) {
            $Names = @($Pending | ForEach-Object { $_.table }) -join ', '
            throw "No pude resolver dependencias FK del snapshot: $Names"
        }
    }

    if ($Pending.Count -gt 0) {
        $Names = @($Pending | ForEach-Object { $_.table }) -join ', '
        throw "Quedaron tablas sin importar después de 30 rondas: $Names"
    }

    # ------------------------------------------------------------
    # Alinear secuencias SERIAL/IDENTITY después de importar IDs Cloud.
    # ------------------------------------------------------------
    $SequenceSql = @'
DO $rn$
DECLARE
    r RECORD;
    seq_name TEXT;
    max_value BIGINT;
BEGIN
    FOR r IN
        SELECT c.table_schema, c.table_name, c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema='public'
        ORDER BY c.table_name, c.ordinal_position
    LOOP
        seq_name := pg_get_serial_sequence(
            format('%I.%I', r.table_schema, r.table_name),
            r.column_name
        );
        IF seq_name IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'SELECT MAX(%I)::bigint FROM %I.%I',
            r.column_name, r.table_schema, r.table_name
        ) INTO max_value;
        IF max_value IS NOT NULL THEN
            PERFORM setval(seq_name::regclass, max_value, true);
        END IF;
    END LOOP;
END
$rn$;
'@
    Invoke-PsqlScript -Sql $SequenceSql | Out-Null

    # ------------------------------------------------------------
    # Registrar nodo Local y colocar TODOS los cursores en el snapshot.
    # Esto evita reproducir eventos históricos B3 y POS.
    # ------------------------------------------------------------
    $EmpresaSql = Sql-String $EmpresaId
    $NodeCodeSql = Sql-String $NodeCode
    $NodeNameSql = Sql-String $NodeName
    $CursorCreated = $Package.cloud_cursor.last_created_at
    $CursorEvent = $Package.cloud_cursor.last_event_id

    $CursorCreatedSql = "NULL"
    if ($CursorCreated) {
        $CursorCreatedSql = (Sql-String ([string]$CursorCreated)) + "::timestamptz"
    }
    $CursorEventSql = "NULL"
    if ($CursorEvent) {
        $CursorEventSql = (Sql-String ([string]$CursorEvent)) + "::uuid"
    }

    $CursorSql = @"
BEGIN;
INSERT INTO racknova_nodos (
    empresa_id, codigo, nombre, tipo, activo,
    version_app, ultima_conexion, creado_en, actualizado_en
)
VALUES (
    $EmpresaSql::uuid, $NodeCodeSql, $NodeNameSql, 'LOCAL_SERVER', TRUE,
    'native-1.0.0-definitive', NOW(), NOW(), NOW()
)
ON CONFLICT (empresa_id, codigo)
DO UPDATE SET
    nombre=EXCLUDED.nombre,
    tipo='LOCAL_SERVER',
    activo=TRUE,
    version_app='native-1.0.0-definitive',
    ultima_conexion=NOW(),
    actualizado_en=NOW();

INSERT INTO racknova_sync_cursor (
    empresa_id, node_code, direccion,
    last_created_at, last_event_id, actualizado_en
)
VALUES (
    $EmpresaSql::uuid, $NodeCodeSql, 'CLOUD_TO_LOCAL',
    $CursorCreatedSql, $CursorEventSql, NOW()
)
ON CONFLICT (empresa_id, node_code, direccion)
DO UPDATE SET
    last_created_at=EXCLUDED.last_created_at,
    last_event_id=EXCLUDED.last_event_id,
    actualizado_en=NOW();

DO `$rn`$
BEGIN
    IF to_regclass('public.racknova_sync_pos_cursor') IS NOT NULL THEN
        INSERT INTO racknova_sync_pos_cursor (
            empresa_id, node_code, last_created_at, last_event_id, actualizado_en
        )
        VALUES (
            $EmpresaSql::uuid, $NodeCodeSql,
            $CursorCreatedSql, $CursorEventSql, NOW()
        )
        ON CONFLICT (empresa_id, node_code)
        DO UPDATE SET
            last_created_at=EXCLUDED.last_created_at,
            last_event_id=EXCLUDED.last_event_id,
            actualizado_en=NOW();
    END IF;
END
`$rn`$;
COMMIT;
"@
    Invoke-PsqlScript -Sql $CursorSql | Out-Null

    $Config.app_version = "native-1.0.0-definitive"
    $Config.native_installer_phase = "FINAL"
    $Config | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath $ConfigPath -Encoding UTF8

    Write-Log (
        "Bootstrap Cloud completado. Tablas importadas=" + $ImportedTables +
        "; cursor=" + [string]$CursorEvent
    )

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
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    $DbPassword = $null
    $SyncSecret = $null
    $Secrets = $null
    $Plain = $null
    $Encrypted = $null
    $Encoded = $null
}
