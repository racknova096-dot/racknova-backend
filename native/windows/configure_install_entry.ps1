param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$LogDir = Join-Path $ProgramDataRoot "Logs"
$PgRoot = Join-Path $ProgramDataRoot "PostgreSQL"
$Original = Join-Path $InstallDir "installer\configure_install.ps1"
$EntryLog = Join-Path $LogDir (
    "entry-install-" +
    (Get-Date -Format "yyyyMMdd_HHmmss") +
    ".log"
)

New-Item -ItemType Directory -Force -Path $LogDir, $PgRoot | Out-Null

function Write-EntryLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $EntryLog -Append | Write-Host
}

function Grant-LocalSystemDataAccess {
    New-Item -ItemType Directory -Force -Path $PgRoot | Out-Null

    $AclOutput = (& icacls.exe `
        $PgRoot `
        /inheritance:e `
        /grant:r `
        "*S-1-5-18:(OI)(CI)F" `
        /T `
        /C 2>&1 | Out-String).Trim()

    $AclExitCode = $LASTEXITCODE

    if ($AclOutput) {
        Write-EntryLog (
            "POSTGRES DATA ACL: " +
            ($AclOutput -replace "`r?`n", " | ")
        )
    }

    if ($AclExitCode -ne 0) {
        throw (
            "No pude dar permisos del cluster PostgreSQL a LocalSystem. " +
            "icacls terminó con código $AclExitCode."
        )
    }
}

$ExitCode = 1

try {
    Write-EntryLog "RackNova installer entry directo iniciado."
    Write-EntryLog ("InstallDir=" + $InstallDir)

    if (-not (Test-Path -LiteralPath $Original)) {
        throw "No existe configure_install.ps1"
    }

    # El configurador actual ya contiene la lógica vigente de PostgreSQL,
    # servicios e init-schema. No debe ser reescrito mediante coincidencias
    # de texto: ese mecanismo quedó obsoleto y rompía cuando el script base
    # evolucionaba.
    Write-EntryLog "Preparando permisos del cluster PostgreSQL."
    Grant-LocalSystemDataAccess

    Write-EntryLog "Ejecutando configure_install.ps1 sin reescrituras dinámicas."

    $Output = & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy Bypass `
        -File $Original `
        -InstallDir $InstallDir 2>&1

    $ExitCode = $LASTEXITCODE

    foreach ($OutputLine in @($Output)) {
        if ($null -ne $OutputLine) {
            $Text = ($OutputLine | Out-String).Trim()
            if ($Text) {
                Write-EntryLog ("CONFIGURE: " + $Text)
            }
        }
    }

    Write-EntryLog (
        "configure_install.ps1 terminó con código " + $ExitCode + "."
    )

    if ($ExitCode -ne 0) {
        throw "configure_install.ps1 terminó con código $ExitCode."
    }

    try {
        Grant-LocalSystemDataAccess
    }
    catch {
        Write-EntryLog ("ACL FINAL WARNING: " + $_.Exception.Message)
    }

    Write-EntryLog "Configuración local completada correctamente."
    $ExitCode = 0
}
catch {
    $ExitCode = 1

    try {
        Write-EntryLog ("ERROR: " + $_.Exception.Message)

        if ($_.ScriptStackTrace) {
            Write-EntryLog (
                "STACK: " +
                ($_.ScriptStackTrace -replace "`r?`n", " | ")
            )
        }
    }
    catch {
    }
}

exit $ExitCode
