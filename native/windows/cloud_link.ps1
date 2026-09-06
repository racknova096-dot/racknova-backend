param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("Backup", "Restore", "Activate")]
    [string]$Mode,

    [Parameter(Mandatory=$true)]
    [string]$InstallDir,

    [string]$CloudUrl = "",
    [string]$EmpresaId = "",
    [string]$NodeCode = "",
    [string]$NodeName = "",
    [int]$SyncInterval = 15
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$ConfigPath = Join-Path $ConfigDir "config.json"
$SecretsPath = Join-Path $ConfigDir "secrets.dat"
$SnapshotPath = Join-Path $ConfigDir "cloud-link-preserve.dat"
$BackupDir = Join-Path $ConfigDir "CloudLinkBackups"
$ServiceName = "RackNovaLocal"
$DefaultEmpresaId = "11111111-1111-4111-8111-111111111111"

New-Item -ItemType Directory -Force -Path $ConfigDir, $LogDir | Out-Null

$LogPath = Join-Path $LogDir (
    "cloud-link-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log"
)

function Write-CloudLog([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $Line | Tee-Object -FilePath $LogPath -Append | Write-Host
}

function Convert-ObjectToHashtable($Object) {
    $Result = @{}
    if ($null -eq $Object) {
        return $Result
    }
    foreach ($Property in $Object.PSObject.Properties) {
        $Result[$Property.Name] = $Property.Value
    }
    return $Result
}

function Read-JsonHashtable([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return @{}
    }
    $Raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if (-not $Raw.Trim()) {
        return @{}
    }
    return Convert-ObjectToHashtable ($Raw | ConvertFrom-Json)
}

function Write-JsonAtomic([string]$Path, [hashtable]$Value) {
    $Temp = $Path + ".tmp"
    $Json = $Value | ConvertTo-Json -Depth 20
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Temp, $Json + "`n", $Utf8NoBom)
    Move-Item -LiteralPath $Temp -Destination $Path -Force
}

function Unprotect-RackNovaBytes([byte[]]$Encrypted) {
    return [System.Security.Cryptography.ProtectedData]::Unprotect(
        $Encrypted,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
}

function Protect-RackNovaBytes([byte[]]$Raw) {
    return [System.Security.Cryptography.ProtectedData]::Protect(
        $Raw,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
}

function Read-RackNovaSecrets {
    if (-not (Test-Path -LiteralPath $SecretsPath)) {
        return @{}
    }

    $Encoded = (Get-Content -LiteralPath $SecretsPath -Raw -ErrorAction Stop).Trim()
    if (-not $Encoded) {
        return @{}
    }

    $Encrypted = [Convert]::FromBase64String($Encoded)
    $Raw = Unprotect-RackNovaBytes $Encrypted
    $Json = [System.Text.Encoding]::UTF8.GetString($Raw)
    return Convert-ObjectToHashtable ($Json | ConvertFrom-Json)
}

function Write-RackNovaSecrets([hashtable]$Secrets) {
    $Json = $Secrets | ConvertTo-Json -Compress -Depth 20
    $Raw = [System.Text.Encoding]::UTF8.GetBytes($Json)
    $Encrypted = Protect-RackNovaBytes $Raw
    $Encoded = [Convert]::ToBase64String($Encrypted)
    [System.IO.File]::WriteAllText(
        $SecretsPath,
        $Encoded,
        [System.Text.Encoding]::ASCII
    )
}

function Protect-FileAcl([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    & icacls.exe `
        $Path `
        /inheritance:r `
        /grant:r `
        "*S-1-5-18:F" `
        "*S-1-5-32-544:F" | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude proteger $Path."
    }
}

function Save-ProtectedSnapshot([hashtable]$Snapshot) {
    $Json = $Snapshot | ConvertTo-Json -Compress -Depth 20
    $Raw = [System.Text.Encoding]::UTF8.GetBytes($Json)
    $Encrypted = Protect-RackNovaBytes $Raw
    $Encoded = [Convert]::ToBase64String($Encrypted)
    [System.IO.File]::WriteAllText(
        $SnapshotPath,
        $Encoded,
        [System.Text.Encoding]::ASCII
    )
    Protect-FileAcl $SnapshotPath
}

function Load-ProtectedSnapshot {
    if (-not (Test-Path -LiteralPath $SnapshotPath)) {
        return @{}
    }
    $Encoded = (Get-Content -LiteralPath $SnapshotPath -Raw -ErrorAction Stop).Trim()
    if (-not $Encoded) {
        return @{}
    }
    $Encrypted = [Convert]::FromBase64String($Encoded)
    $Raw = Unprotect-RackNovaBytes $Encrypted
    $Json = [System.Text.Encoding]::UTF8.GetString($Raw)
    return Convert-ObjectToHashtable ($Json | ConvertFrom-Json)
}

function Backup-CurrentFiles([string]$Reason) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    if (Test-Path -LiteralPath $ConfigPath) {
        Copy-Item -LiteralPath $ConfigPath -Destination (
            Join-Path $BackupDir ("config-$Reason-$Stamp.json")
        ) -Force
    }
    if (Test-Path -LiteralPath $SecretsPath) {
        Copy-Item -LiteralPath $SecretsPath -Destination (
            Join-Path $BackupDir ("secrets-$Reason-$Stamp.dat")
        ) -Force
    }
}

function Merge-CloudLink(
    [hashtable]$Config,
    [hashtable]$Secrets,
    [hashtable]$Link
) {
    $Config["activated"] = $true
    $Config["empresa_id"] = [string]$Link["empresa_id"]
    $Config["node_code"] = [string]$Link["node_code"]
    $Config["node_name"] = [string]$Link["node_name"]
    $Config["cloud_url"] = ([string]$Link["cloud_url"]).TrimEnd("/")
    $Config["sync_interval_seconds"] = [int]$Link["sync_interval_seconds"]
    $Config["activated_at"] = (Get-Date).ToUniversalTime().ToString("o")
    $Secrets["node_credential"] = [string]$Link["node_credential"]

    Write-RackNovaSecrets $Secrets
    Write-JsonAtomic -Path $ConfigPath -Value $Config
}

function Restart-RackNovaLocal {
    $Service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $Service) {
        return
    }

    Restart-Service -Name $ServiceName -Force -ErrorAction Stop
    for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
        try {
            $Response = Invoke-WebRequest `
                -Uri "http://127.0.0.1:8000/racknova-native/health" `
                -UseBasicParsing `
                -TimeoutSec 3
            if ($Response.StatusCode -eq 200) {
                return
            }
        }
        catch {
        }
        Start-Sleep -Seconds 1
    }
}

if ($Mode -eq "Backup") {
    Remove-Item -LiteralPath $SnapshotPath -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path -LiteralPath $ConfigPath) -or
        -not (Test-Path -LiteralPath $SecretsPath)) {
        Write-CloudLog "No existe vínculo Cloud previo que preservar."
        exit 0
    }

    try {
        $Config = Read-JsonHashtable $ConfigPath
        $Secrets = Read-RackNovaSecrets
        $ExistingCloudUrl = ([string]($Config["cloud_url"])).Trim()
        $ExistingCredential = ([string]($Secrets["node_credential"])).Trim()

        if (-not $ExistingCloudUrl -or -not $ExistingCredential) {
            Write-CloudLog "La instalación anterior no tenía vínculo Cloud completo."
            exit 0
        }

        $Snapshot = @{
            empresa_id = [string]($Config["empresa_id"])
            node_code = [string]($Config["node_code"])
            node_name = [string]($Config["node_name"])
            cloud_url = $ExistingCloudUrl.TrimEnd("/")
            sync_interval_seconds = [int](
                if ($Config["sync_interval_seconds"]) {
                    $Config["sync_interval_seconds"]
                }
                else {
                    15
                }
            )
            node_credential = $ExistingCredential
        }

        Save-ProtectedSnapshot $Snapshot
        Write-CloudLog (
            "Vínculo Cloud previo preservado de forma protegida para " +
            $Snapshot["node_code"] + "."
        )
        exit 0
    }
    catch {
        Write-CloudLog (
            "No pude preservar el vínculo Cloud previo: " + $_.Exception.Message
        )
        throw
    }
}

if ($Mode -eq "Restore") {
    if (-not (Test-Path -LiteralPath $SnapshotPath)) {
        Write-CloudLog "No hay vínculo Cloud previo pendiente de restaurar."
        exit 0
    }

    try {
        $Link = Load-ProtectedSnapshot
        if (-not $Link["cloud_url"] -or -not $Link["node_credential"]) {
            throw "El respaldo de vínculo Cloud está incompleto."
        }

        $Config = Read-JsonHashtable $ConfigPath
        $Secrets = Read-RackNovaSecrets
        Backup-CurrentFiles "before-restore"
        Merge-CloudLink -Config $Config -Secrets $Secrets -Link $Link
        Remove-Item -LiteralPath $SnapshotPath -Force -ErrorAction SilentlyContinue
        Restart-RackNovaLocal
        Write-CloudLog (
            "Vínculo Cloud restaurado. URL=" + $Link["cloud_url"] +
            " node=" + $Link["node_code"]
        )
        exit 0
    }
    catch {
        Write-CloudLog (
            "ERROR restaurando vínculo Cloud: " + $_.Exception.Message
        )
        throw
    }
}

if ($Mode -eq "Activate") {
    $Secret = [string]$env:RACKNOVA_INSTALL_SYNC_SECRET
    $Secret = $Secret.Trim()
    $CloudUrl = ([string]$CloudUrl).Trim().TrimEnd("/")
    $EmpresaId = ([string]$EmpresaId).Trim()

    if (-not $CloudUrl) {
        throw "RackNova Cloud URL es obligatoria."
    }
    if (-not $CloudUrl.StartsWith("https://") -and
        -not $CloudUrl.StartsWith("http://127.0.0.1") -and
        -not $CloudUrl.StartsWith("http://localhost")) {
        throw "RackNova Cloud debe usar HTTPS."
    }
    if ($Secret.Length -lt 20) {
        throw "La credencial RackNova Sync debe tener al menos 20 caracteres."
    }

    try {
        $RequestedEmpresa = ([Guid]$EmpresaId).ToString()
    }
    catch {
        throw "empresa_id no es un UUID válido."
    }

    if ($SyncInterval -lt 5 -or $SyncInterval -gt 3600) {
        throw "sync_interval debe estar entre 5 y 3600 segundos."
    }

    $Config = Read-JsonHashtable $ConfigPath
    $Secrets = Read-RackNovaSecrets
    $CurrentEmpresa = [string]($Config["empresa_id"])
    if (-not $CurrentEmpresa) {
        $CurrentEmpresa = $DefaultEmpresaId
    }

    try {
        $CurrentEmpresa = ([Guid]$CurrentEmpresa).ToString()
    }
    catch {
        throw "La empresa configurada actualmente en RackNova Local no es válida."
    }

    if ($CurrentEmpresa -ne $RequestedEmpresa) {
        throw (
            "RackNova Local está inicializado para la empresa $CurrentEmpresa, " +
            "pero se intentó vincular con $RequestedEmpresa. " +
            "No cambiaré de tenant sin un bootstrap Cloud seguro."
        )
    }

    if (-not $NodeCode) {
        $NodeCode = [string]($Config["node_code"])
    }
    if (-not $NodeCode) {
        $NodeCode = "LOCAL-" + $env:COMPUTERNAME.ToUpper()
    }
    $NodeCode = $NodeCode.Trim().ToUpper()
    $NodeCode = [regex]::Replace($NodeCode, "[^A-Z0-9_-]", "-")
    $NodeCode = $NodeCode.Trim("-", "_")
    if ($NodeCode.Length -gt 120) {
        $NodeCode = $NodeCode.Substring(0, 120)
    }

    if (-not $NodeName) {
        $NodeName = [string]($Config["node_name"])
    }
    if (-not $NodeName) {
        $NodeName = "RackNova Local - " + $env:COMPUTERNAME
    }
    $NodeName = $NodeName.Trim()
    if ($NodeName.Length -gt 180) {
        $NodeName = $NodeName.Substring(0, 180)
    }

    Write-CloudLog (
        "Validando vínculo con RackNova Cloud. URL=$CloudUrl empresa=$RequestedEmpresa node=$NodeCode"
    )

    $Headers = @{
        "Accept" = "application/json"
        "X-RackNova-Sync-Secret" = $Secret
    }
    $Body = @{
        empresa_id = $RequestedEmpresa
        node_code = $NodeCode
        node_name = $NodeName
        node_type = "LOCAL_SERVER"
        app_version = [string](
            if ($Config["app_version"]) {
                $Config["app_version"]
            }
            else {
                "native-f1.9"
            }
        )
    } | ConvertTo-Json -Compress

    try {
        $Remote = Invoke-RestMethod `
            -Method Post `
            -Uri ($CloudUrl + "/sync/v1/nodes/register") `
            -Headers $Headers `
            -ContentType "application/json" `
            -Body $Body `
            -TimeoutSec 30
    }
    catch {
        throw (
            "No pude registrar este RackNova Local en Cloud: " +
            $_.Exception.Message
        )
    }

    if ($Remote.ok -ne $true) {
        throw "RackNova Cloud no confirmó el registro del nodo."
    }

    $Link = @{
        empresa_id = $RequestedEmpresa
        node_code = $NodeCode
        node_name = $NodeName
        cloud_url = $CloudUrl
        sync_interval_seconds = $SyncInterval
        node_credential = $Secret
    }

    Backup-CurrentFiles "before-activate"
    Merge-CloudLink -Config $Config -Secrets $Secrets -Link $Link
    Restart-RackNovaLocal

    Remove-Item Env:\RACKNOVA_INSTALL_SYNC_SECRET -ErrorAction SilentlyContinue

    Write-CloudLog (
        "RackNova Local quedó vinculado a Cloud correctamente. node=" + $NodeCode
    )
    exit 0
}
