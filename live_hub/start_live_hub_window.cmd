@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
start "Live Hub" powershell.exe -NoExit -ExecutionPolicy Bypass -Command "& '%SCRIPT_DIR%start_live_hub.ps1'"
endlocal
