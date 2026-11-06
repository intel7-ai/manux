@echo off
setlocal
cd /d "%~dp0"

rem Starts hand-mouse with administrator rights and WITHOUT a console window, so it can
rem also control programs that run as administrator (e.g. CodeVisionAVR). Windows blocks
rem mouse/keyboard input from normal programs to elevated ones (UIPI).
rem Messages and errors go to logs\app_latest.log instead of a console.
rem Keep this file ASCII-only with CRLF line endings (same rule as run.bat).
rem Starts with --headless (tray icon + corner overlay; open the camera window from the tray menu).
rem Options are passed through, e.g.  run_admin.bat --phone-web

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" call :setup
if not exist "%PYW%" exit /b 1

net session >nul 2>&1
if %errorlevel%==0 (
  rem already administrator: start without a console and close this window
  start "" "%PYW%" "%~dp0main.py" --headless %*
  exit /b 0
)

if "%~1"=="" (
  powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%PYW%' -ArgumentList '\"%~dp0main.py\" --headless' -WorkingDirectory '%~dp0' -Verb RunAs"
) else (
  powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%PYW%' -ArgumentList '\"%~dp0main.py\" --headless %*' -WorkingDirectory '%~dp0' -Verb RunAs"
)
if errorlevel 1 (
  echo [hand-mouse] Administrator rights were not granted. Run run.bat instead.
  pause
  exit /b 1
)
exit /b 0

:setup
rem First run only: prepare Python and packages with run.bat's uv setup (visible on purpose)
echo [hand-mouse] First run: preparing Python and packages...
where uv >nul 2>nul
if errorlevel 1 (
  echo [hand-mouse] uv is not installed. Run run.bat once first, then use run_admin.bat.
  pause
  exit /b 1
)
uv sync
if errorlevel 1 (
  echo [hand-mouse] uv sync failed. Check the internet connection.
  pause
  exit /b 1
)
exit /b 0
