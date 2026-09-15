param(
    [switch]$Stop,
    [string]$StagingUrl = "https://racknova-dashboard-2dtv.vercel.app"
)

$ErrorActionPreference = "Stop"

$DevRoot = Join-Path $env:LOCALAPPDATA "RackNovaDev"
$SourceRoot = Join-Path $DevRoot "source"
$BackendRoot = Join-Path $SourceRoot "racknova-backend-develop"
$ToolsRoot = Join-Path $DevRoot "tools"
$StateFile = Join-Path $DevRoot "lab-state.json"
$TunnelStdout = Join-Path $DevRoot "cloudflared.out.log"
$TunnelStderr = Join-Path $DevRoot "cloudflared.err.log"
$Cloudflared = Join-Path $ToolsRoot "cloudflared.exe"

function Stop-ProcessTree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    $Existing = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($Existing) {
        & taskkill.exe /PID $ProcessId /T /F *> $null
    }
}

function Stop-RackNovaLab {
    if (Test-Path $StateFile) {
        try {
            $State = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
            Stop-ProcessTree ([int]($State.tunnel_pid))
            Stop-ProcessTree ([int]($State.backend_pid))
        }
        catch {
            Write-Warning ("No pude leer el estado anterior: " + $_.Exception.Message)
        }
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    }

    $StopDev = Join-Path $BackendRoot "scripts\stop-dev-zero-cost.ps1"
    if (Test-Path $StopDev) {
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StopDev
        }
        catch {
            Write-Warning ("No pude detener PostgreSQL DEV: " + $_.Exception.Message)
        }
    }

    Write-Host "RackNova Lab detenido."
}

if ($Stop) {
    Stop-RackNovaLab
    exit 0
}

New-Item -ItemType Directory -Force -Path $DevRoot, $SourceRoot, $ToolsRoot | Out-Null

# Limpiar una sesión anterior para evitar puertos o túneles duplicados.
if (Test-Path $StateFile) {
    Stop-RackNovaLab
}

Write-Host "1/5 Descargando la versión de pruebas de RackNova..."
$ZipPath = Join-Path $DevRoot "racknova-backend-develop.zip"
$TempExtract = Join-Path $DevRoot "source-new"
Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $TempExtract -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $TempExtract | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/racknova096-dot/racknova-backend/archive/refs/heads/develop.zip" -OutFile $ZipPath
Expand-Archive -LiteralPath $ZipPath -DestinationPath $TempExtract -Force

$DownloadedBackend = Join-Path $TempExtract "racknova-backend-develop"
if (-not (Test-Path (Join-Path $DownloadedBackend "main.py"))) {
    throw "La descarga de RackNova develop no contiene main.py."
}

Remove-Item -LiteralPath $BackendRoot -Recurse -Force -ErrorAction SilentlyContinue
Move-Item -LiteralPath $DownloadedBackend -Destination $BackendRoot
Remove-Item -LiteralPath $TempExtract -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue

Write-Host "2/5 Iniciando base y backend de pruebas..."
$StartDev = Join-Path $BackendRoot "scripts\start-dev-zero-cost.ps1"
if (-not (Test-Path $StartDev)) {
    throw "No encontré scripts\start-dev-zero-cost.ps1 en develop."
}

$BackendArgs = @(
    "-NoProfile",
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    ('"{0}"' -f $StartDev)
) -join " "

$BackendProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $BackendArgs -PassThru

$ApiReady = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8010/openapi.json" -TimeoutSec 2
        if ($Response.StatusCode -eq 200) {
            $ApiReady = $true
            break
        }
    }
    catch {
        if ($BackendProcess.HasExited) {
            throw "El backend de pruebas terminó antes de iniciar. Revisa la ventana de PowerShell que se abrió."
        }
    }
}

if (-not $ApiReady) {
    Stop-ProcessTree $BackendProcess.Id
    throw "El backend no respondió en http://127.0.0.1:8010 después de 3 minutos."
}

$DevSecretsFile = Join-Path $DevRoot "dev-secrets.ps1"
$LabAdminPassword = ""
if (Test-Path $DevSecretsFile) {
    . $DevSecretsFile
    $LabAdminPassword = [string]$RackNovaDevWebAdminPassword
}

Write-Host "3/5 Preparando conexión segura temporal..."
if (-not (Test-Path $Cloudflared)) {
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $Cloudflared
}

Remove-Item -LiteralPath $TunnelStdout, $TunnelStderr -Force -ErrorAction SilentlyContinue
$TunnelProcess = Start-Process -FilePath $Cloudflared -ArgumentList @(
    "tunnel",
    "--url",
    "http://127.0.0.1:8010",
    "--no-autoupdate"
) -RedirectStandardOutput $TunnelStdout -RedirectStandardError $TunnelStderr -WindowStyle Hidden -PassThru

$TunnelUrl = ""
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    $Text = ""
    if (Test-Path $TunnelStdout) { $Text += (Get-Content -LiteralPath $TunnelStdout -Raw -ErrorAction SilentlyContinue) }
    if (Test-Path $TunnelStderr) { $Text += "`n" + (Get-Content -LiteralPath $TunnelStderr -Raw -ErrorAction SilentlyContinue) }
    $Match = [regex]::Match($Text, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($Match.Success) {
        $TunnelUrl = $Match.Value.TrimEnd("/")
        break
    }
    if ($TunnelProcess.HasExited) {
        Stop-ProcessTree $BackendProcess.Id
        throw "Cloudflare Tunnel terminó antes de generar una URL. Revisa $TunnelStderr"
    }
}

if (-not $TunnelUrl) {
    Stop-ProcessTree $TunnelProcess.Id
    Stop-ProcessTree $BackendProcess.Id
    throw "No pude obtener la URL temporal del túnel."
}

$State = [ordered]@{
    backend_pid = $BackendProcess.Id
    tunnel_pid = $TunnelProcess.Id
    tunnel_url = $TunnelUrl
    started_at = (Get-Date).ToUniversalTime().ToString("o")
}
$State | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8

Write-Host "4/5 Conectando el dashboard de pruebas..."
$EncodedTunnel = [Uri]::EscapeDataString($TunnelUrl)
$LabUrl = $StagingUrl.TrimEnd("/") + "/?api=" + $EncodedTunnel

Write-Host "5/5 RackNova Lab listo."
Write-Host ""
Write-Host "Dashboard: $StagingUrl"
Write-Host "Backend DEV: http://127.0.0.1:8010"
Write-Host "Base DEV: PostgreSQL local puerto 54339 / racknova_dev"
Write-Host "Cloud de producción: NO CONECTADO"
Write-Host ""
Write-Host "Usuario inicial: admin@racknova.com"
Write-Host "Password inicial: $LabAdminPassword"
Write-Host ""
Write-Host "Para apagar el laboratorio:"
Write-Host "powershell -ExecutionPolicy Bypass -File .\RackNovaLab.ps1 -Stop"
Write-Host ""

Start-Process $LabUrl
