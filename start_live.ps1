# AI Streamer - B站直播全链路启动 (PowerShell)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $MyInvocation.MyCommand.Path)
$HubUrl = "http://127.0.0.1:18190"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AI Streamer - B站直播全链路" -ForegroundColor Cyan
Write-Host "  房间: 4538234 | S1: MiniCPM | S2: DeepSeek" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# API key loaded from .env via python-dotenv in run_live.py

# 1. Live Hub
try {
    $null = Invoke-WebRequest -Uri "$HubUrl/api/health" -TimeoutSec 2
    Write-Host "[1/4] Live Hub 已在运行 ($HubUrl)" -ForegroundColor Green
} catch {
    Write-Host "[1/4] 启动 Live Hub..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell" `
        -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-File","live_hub\start_live_hub.ps1" `
        -WindowStyle Minimized
    Write-Host "  等待 Hub 就绪..." -ForegroundColor Yellow
    $hubReady = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep 2
        try {
            $null = Invoke-WebRequest -Uri "$HubUrl/api/health" -TimeoutSec 2
            $hubReady = $true
            break
        } catch {}
    }
    if ($hubReady) {
        Write-Host "  Hub 就绪: $HubUrl" -ForegroundColor Green
    } else {
        Write-Host "  [警告] Hub 启动超时，继续执行..." -ForegroundColor Yellow
    }
}

# 2. MiniCPM
try {
    $null = Invoke-WebRequest -Uri "http://localhost:9060/health" -TimeoutSec 2
    Write-Host "[2/4] MiniCPM 已在运行" -ForegroundColor Green
} catch {
    Write-Host "[2/4] 启动 MiniCPM-o 4.5..." -ForegroundColor Yellow
    Start-Process -FilePath "F:\llm\llama.cpp-upstream\build\bin\Release\llama-server.exe" `
        -ArgumentList "-m","F:\llm\models\MiniCPM-o-4_5-Q4_K_M.gguf",`
                      "--mmproj","F:\llm\models\vision\MiniCPM-o-4_5-vision-F16.gguf",`
                      "--port","9060","--host","127.0.0.1","-ngl","99","-c","4096","--temp","0.1" `
        -WindowStyle Minimized
    Write-Host "  等待模型加载 (30秒)..." -ForegroundColor Yellow
    Start-Sleep 30
}

# 3. GUI
Write-Host "[3/4] 启动 GUI (http://127.0.0.1:9071)..." -ForegroundColor Yellow
Start-Process -FilePath "python" `
    -ArgumentList "-m","uvicorn","src.gui_server:app","--host","127.0.0.1","--port","9071" `
    -WindowStyle Minimized
Start-Sleep 5

# 4. B站 + AI 主循环 (统一入口)
Write-Host "[4/4] 启动 B站弹幕监听 + AI 回复..." -ForegroundColor Yellow
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  全链路已启动!" -ForegroundColor Green
Write-Host "  Hub: $HubUrl | GUI: http://127.0.0.1:9071" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

python run_live.py --platform bilibili

Read-Host "`n按 Enter 退出"
