@echo off
setlocal

echo ===================================================
echo   SHADOW - Install Auto-Start (Windows)
echo ===================================================

set "ROOT=D:\jarvis\jarvis-main"
set "TARGET=%ROOT%\start_shadow.bat"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\Start_SHADOW.lnk"

if not exist "%TARGET%" (
  echo ERROR: Cannot find "%TARGET%"
  pause
  exit /b 1
)

echo Creating startup shortcut:
echo   %LINK%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$s = $ws.CreateShortcut('%LINK%'); " ^
  "$s.TargetPath = '%TARGET%'; " ^
  "$s.WorkingDirectory = '%ROOT%'; " ^
  "$s.WindowStyle = 1; " ^
  "$s.IconLocation = '%SystemRoot%\\System32\\shell32.dll,1'; " ^
  "$s.Save()"

if exist "%LINK%" (
  echo Done. SHADOW will auto-start on next login.
) else (
  echo ERROR: Failed to create the startup shortcut.
)

pause
exit /b 0

