@echo off
cd /d F:\OpenNeuro
REM API key loaded from .env via python-dotenv
echo Starting GUI on http://127.0.0.1:9071
python -m uvicorn src.gui_server:app --host 127.0.0.1 --port 9071
pause
