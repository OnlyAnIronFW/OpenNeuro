param(
    [int]$RoomId = 0,
    [int]$Port = 0,
    [string]$ListenHost = "",
    [switch]$Verbose,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$MaiBotRoot = $PSScriptRoot
Set-Location -LiteralPath $MaiBotRoot

if (-not $Python) {
    $venvPython = Join-Path $MaiBotRoot ".venv\\Scripts\\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $Python = $venvPython
    } else {
        $Python = "python"
    }
}

$arguments = @("-m", "src.live_hub")
if ($RoomId -gt 0) {
    $arguments += @("--room-id", [string]$RoomId)
}
if ($Port -gt 0) {
    $arguments += @("--listen-port", [string]$Port)
}
if ($ListenHost) {
    $arguments += @("--listen-host", $ListenHost)
}
if ($Verbose) {
    $arguments += "--verbose"
}

Write-Host "Starting MaiBot Live Hub..." -ForegroundColor Cyan
Write-Host "  Repo:   $MaiBotRoot"
Write-Host "  Python: $Python"
if ($RoomId -gt 0) {
    Write-Host "  Room:   $RoomId"
} else {
    Write-Host "  Room:   config/maibot_live_hub.toml -> source adapter config"
}
if ($Port -gt 0) {
    Write-Host "  Port:   $Port"
}
Write-Host "  Stop:   Ctrl+C"
Write-Host ""

& $Python @arguments
exit $LASTEXITCODE
