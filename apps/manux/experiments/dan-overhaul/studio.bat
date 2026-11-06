@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "APP=MANUX studio"
set "PYTHONIOENCODING=utf-8"

rem MANUX gesture training studio: one menu for run / record / train / report / export.
rem Keep this file ASCII-only with CRLF line endings (Korean text or LF endings make cmd misread the lines).
rem The menu itself (studio.py) explains every choice and the real command it runs.
where uv >nul 2>nul
if errorlevel 1 call :install_uv
if errorlevel 1 exit /b 1

echo [%APP%] Preparing Python and packages (qt + train). The first run takes a few minutes...
uv sync --locked --extra qt --extra train
if errorlevel 1 goto fail

".venv\Scripts\python.exe" studio.py
exit /b 0

:fail
echo [%APP%] uv sync failed. Check the internet connection and the messages above.
pause
exit /b 1

:install_uv
echo [%APP%] uv is not installed on this PC. Installing it first (needs internet)...
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo [%APP%] Could not install uv. Run this in PowerShell and try again:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause
  exit /b 1
)
exit /b 0
