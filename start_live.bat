@echo off
cd /d F:\OpenNeuro
set "HUB_URL=http://127.0.0.1:18190"
set "HUB_SCRIPT=live_hub\start_live_hub.ps1"

REM ==========================================
REM 1. Live Hub
REM ==========================================
curl -s -o NUL %HUB_URL%/api/health 2>nul
if %errorlevel% neq 0 (
    echo [1/3] Starting Live Hub...
    start "Live-Hub" powershell -NoExit -ExecutionPolicy Bypass -File "%HUB_SCRIPT%"
    echo   Waiting for hub to be ready...
    :wait_hub
    timeout /t 2 /nobreak >nul
    curl -s -o NUL %HUB_URL%/api/health 2>nul
    if %errorlevel% neq 0 goto wait_hub
    echo   Hub ready: %HUB_URL%
) else (
    echo [1/3] Live Hub already running
)

REM ==========================================
REM 2. GUI Backend
REM ==========================================
curl -s -o NUL http://127.0.0.1:9071/api/status 2>nul
if %errorlevel% neq 0 (
    echo [2/3] Starting GUI backend...
    start "AI-Streamer-GUI" cmd /c "cd /d F:\OpenNeuro && python -m uvicorn src.gui_server:app --host 127.0.0.1 --port 9071"
    timeout /t 4 /nobreak >nul
) else (
    echo [2/3] GUI already running
)

REM ==========================================
REM 3. AI Streamer
REM ==========================================
echo [3/3] Starting AI Streamer...
REM API key loaded from .env via python-dotenv
python run_live.py --platform maibot
pause
