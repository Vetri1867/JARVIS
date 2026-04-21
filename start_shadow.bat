@echo off
setlocal

echo ===================================================
echo   SHADOW - Desktop Assistant (Local)
echo ===================================================

cd /d "D:\jarvis\jarvis-main"

echo [1/2] Starting SHADOW Backend (FastAPI) on port 8340...
start "SHADOW Backend" cmd /k "python server.py"

echo [2/2] Starting SHADOW Frontend (Vite) on port 5173...
cd frontend
start "SHADOW Frontend" cmd /k "npm run dev"

echo.
echo ===================================================
echo   SHADOW is initializing...
echo   Open Chrome and go to: http://localhost:5173
echo ===================================================

timeout /t 3 >nul
exit
