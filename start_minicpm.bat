@echo off
cd /d "%~dp0"
echo Starting MiniCPM-o 4.5 on port 9060...
echo Model will load in ~30 seconds.
"F:\llm\llama.cpp-upstream\build\bin\Release\llama-server.exe" -m "F:\llm\models\MiniCPM-o-4_5-Q4_K_M.gguf" --port 9060 --host 127.0.0.1 -ngl 99 -c 4096 --temp 0.1
pause
