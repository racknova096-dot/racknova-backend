param(
    [Parameter(Mandatory=$true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

$ProgramDataRoot = Join-Path $env:ProgramData "RackNova"
$PgData = Join-Path $ProgramDataRoot "PostgreSQL\data"
$PgInstall = Join-Path $InstallDir "PostgreSQL"
$Original = Join-Path $InstallDir "installer\configure_install.ps1"
$Effective = Join-Path $InstallDir "installer\configure_install_effective.ps1"

function Grant-NetworkServiceAccess {
    if (Test-Path -LiteralPath $PgData) {
        & icacls.exe `
            $PgData `
            /grant:r `
            "*S-1-5-20:(OI)(CI)F" `
            /T `
            /C | Out-Null
    }

    if (Test-Path -LiteralPath $PgInstall) {
        & icacls.exe `
            $PgInstall `
            /grant:r `
            "*S-1-5-20:(OI)(CI)RX" `
            /T `
            /C | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $Original)) {
    throw "No existe configure_install.ps1"
}

Grant-NetworkServiceAccess

$text = [System.IO.File]::ReadAllText($Original)

$old = @'
    & sc.exe config `
        RackNovaPostgreSQL16 `
        obj= LocalSystem `
        start= auto | Out-Null
'@

$new = @'
    & sc.exe config `
        RackNovaPostgreSQL16 `
        obj= "NT AUTHORITY\NetworkService" `
        password= "" `
        start= auto | Out-Null
'@

if ($text.Contains($old)) {
    $text = $text.Replace($old, $new)
}
else {
    throw "No encontré el bloque esperado de cuenta PostgreSQL."
}

[System.IO.File]::WriteAllText(
    $Effective,
    $text,
    (New-Object System.Text.UTF8Encoding($false))
)

try {
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $Effective `
        -InstallDir $InstallDir

    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $Effective -Force -ErrorAction SilentlyContinue
}
