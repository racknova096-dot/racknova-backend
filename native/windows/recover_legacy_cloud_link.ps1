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
$ActivationBackupDir = Join-Path $ConfigDir "ActivationBackups"
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

function Add-ActivationBackupPairs(
    [string]$Directory,
    [string]$LabelPrefix
) {
    if (-not (Test-Path -LiteralPath $Directory)) {
        return
    }

    $Configs = Get-ChildItem `
        -LiteralPath $Directory `
        -Filter "config-*.json" `
        -File `
        -ErrorAction SilentlyContinue

    foreach ($ConfigFile in $Configs) {
        $BaseName = [System.IO.Path]::GetFileNameWithoutExtension($ConfigFile.Name)
        if ($BaseName.Length -le 7) {
            continue
        }

        $Stamp = $BaseName.Substring(7)
        $SecretFile = Join-Path $Directory ("secrets-" + $Stamp + ".dat")
        if (-not (Test-Path -LiteralPath $SecretFile)) {
            continue
        }

        $script:CandidatePairs += [PSCustomObject]@{
            ConfigPath = $ConfigFile.FullName
            SecretsPath = $SecretFile
            Label = $LabelPrefix + "/" + $Stamp
            SortTime = $ConfigFile.LastWriteTimeUtc
        }
    }
}

if (Test-Path -LiteralPath $SnapshotPath) {
    Write-RecoveryLog "Ya existe un vínculo Cloud preservado para esta reparación."
    exit 0
}

$CandidatePairs = @()

# RackNovaCtl activate-cloud guarda pares config/secrets aquí antes de modificar.
Add-ActivationBackupPairs `
    -Directory $ActivationBackupDir `
    -LabelPrefix "ActivationBackups"

# Los intentos de recuperación init-schema guardaron Config completo, incluido
# ActivationBackups. Buscamos tanto el estado directo como los pares históricos.
if (Test-Path -LiteralPath $RecoveryRoot) {
    $RecoveryDirs = Get-ChildItem `
        -LiteralPath $RecoveryRoot `
        -Directory `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "init-schema-*" }

    foreach ($RecoveryDir in $RecoveryDirs) {
        $RecoveryConfigDir = Join-Path $RecoveryDir.FullName "Config"
        $DirectConfig = Join-Path $RecoveryConfigDir "config.json"
        $DirectSecrets = Join-Path $RecoveryConfigDir "secrets.dat"

        if ((Test-Path -LiteralPath $DirectConfig) -and
            (Test-Path -LiteralPath $DirectSecrets)) {
            $DirectInfo = Get-Item -LiteralPath $DirectConfig
            $CandidatePairs += [PSCustomObject]@{
                ConfigPath = $DirectConfig
                SecretsPath = $DirectSecrets
                Label = $RecoveryDir.Name + "/Config"
                SortTime = $DirectInfo.LastWriteTimeUtc
            }
        }

        Add-ActivationBackupPairs `
            -Directory (Join-Path $RecoveryConfigDir "ActivationBackups") `
            -LabelPrefix ($RecoveryDir.Name + "/ActivationBackups")
    }
}

if (-not $CandidatePairs -or $CandidatePairs.Count -eq 0) {
    Write-RecoveryLog "No existen respaldos anteriores con pares config/secrets."
    exit 0
}

$CandidatePairs = $CandidatePairs | Sort-Object SortTime -Descending

foreach ($Candidate in $CandidatePairs) {
    try {
        $Config = Read-Config $Candidate.ConfigPath
        $Secrets = Read-Secrets $Candidate.SecretsPath
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
            $Candidate.Label + " para node=" + $Snapshot["node_code"] + "."
        )
        exit 0
    }
    catch {
        Write-RecoveryLog (
            "No pude leer " + $Candidate.Label + ": " + $_.Exception.Message
        )
    }
}

Write-RecoveryLog (
    "No encontré un vínculo Cloud completo en los respaldos disponibles."
)
exit 0
