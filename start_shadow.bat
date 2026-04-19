@echo off
echo Starting SHADOW Backend...
cd /d "D:\jarvis\jarvis-main"
start "SHADOW Backend" cmd /k ".\jarvis_env\Scripts\python.exe server.py"

echo Starting SHADOW Frontend...
cd frontend
start "SHADOW Frontend" cmd /c "npm run dev"

echo SHADOW is starting up! You can close this small window.
timeout /t 3 >nul
