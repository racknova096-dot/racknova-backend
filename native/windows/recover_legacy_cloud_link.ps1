param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$ConfigDir = Join-Path $ProgramDataRoot "Config"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$RecoveryRoot = Join-Path $ProgramDataRoot "RecoveryBackups"
$SnapshotPath = Join-Path $ConfigDir "cloud-link-preserve.dat"

New-Item -ItemType Directory -Force -Path $ConfigDir, $LogDir | Out-Null

$LogPath = Join-Path $LogDir (
    "cloud-link-recovery-" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log"
)

function Write-RecoveryLog([string]$Message) {
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

function Read-Config([string]$Path) {
    $Raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if (-not $Raw.Trim()) {
        return @{}
    }
    return Convert-ObjectToHashtable ($Raw | ConvertFrom-Json)
}

function Read-Secrets([string]$Path) {
    $Encoded = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop).Trim()
    if (-not $Encoded) {
        return @{}
    }

    $Encrypted = [Convert]::FromBase64String($Encoded)
    $Raw = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $Encrypted,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $Json = [System.Text.Encoding]::UTF8.GetString($Raw)
    return Convert-ObjectToHashtable ($Json | ConvertFrom-Json)
}

function Save-Snapshot([hashtable]$Snapshot) {
    $Json = $Snapshot | ConvertTo-Json -Compress -Depth 20
    $Raw = [System.Text.Encoding]::UTF8.GetBytes($Json)
    $Encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
        $Raw,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $Encoded = [Convert]::ToBase64String($Encrypted)

    [System.IO.File]::WriteAllText(
        $SnapshotPath,
        $Encoded,
        [System.Text.Encoding]::ASCII
    )

    & icacls.exe `
        $SnapshotPath `
        /inheritance:r `
        /grant:r `
        "*S-1-5-18:F" `
        "*S-1-5-32-544:F" | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "No pude proteger el respaldo temporal del vínculo Cloud."
    }
}

if (Test-Path -LiteralPath $SnapshotPath) {
    Write-RecoveryLog "Ya existe un vínculo Cloud preservado para esta reparación."
    exit 0
}

if (-not (Test-Path -LiteralPath $RecoveryRoot)) {
    Write-RecoveryLog "No existen respaldos de recuperación anteriores."
    exit 0
}

$Candidates = Get-ChildItem `
    -LiteralPath $RecoveryRoot `
    -Directory `
    -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "init-schema-*" } |
    Sort-Object LastWriteTime -Descending

foreach ($Candidate in $Candidates) {
    $CandidateConfigDir = Join-Path $Candidate.FullName "Config"
    $CandidateConfig = Join-Path $CandidateConfigDir "config.json"
    $CandidateSecrets = Join-Path $CandidateConfigDir "secrets.dat"

    if (-not (Test-Path -LiteralPath $CandidateConfig) -or
        -not (Test-Path -LiteralPath $CandidateSecrets)) {
        continue
    }

    try {
        $Config = Read-Config $CandidateConfig
        $Secrets = Read-Secrets $CandidateSecrets
        $CloudUrl = ([string]($Config["cloud_url"])).Trim().TrimEnd("/")
        $Credential = ([string]($Secrets["node_credential"])).Trim()

        if (-not $CloudUrl -or -not $Credential) {
            continue
        }

        $Interval = 15
        if ($Config["sync_interval_seconds"]) {
            $Interval = [int]$Config["sync_interval_seconds"]
        }

        $Snapshot = @{
            empresa_id = [string]($Config["empresa_id"])
            node_code = [string]($Config["node_code"])
            node_name = [string]($Config["node_name"])
            cloud_url = $CloudUrl
            sync_interval_seconds = $Interval
            node_credential = $Credential
        }

        Save-Snapshot $Snapshot
        Write-RecoveryLog (
            "Recuperé de forma protegida un vínculo Cloud anterior desde " +
            $Candidate.Name + " para node=" + $Snapshot["node_code"] + "."
        )
        exit 0
    }
    catch {
        Write-RecoveryLog (
            "No pude leer " + $Candidate.Name + ": " + $_.Exception.Message
        )
    }
}

Write-RecoveryLog (
    "No encontré un vínculo Cloud completo en los respaldos init-schema existentes."
)
exit 0
