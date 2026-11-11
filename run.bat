@echo off
setlocal
cd /d "%~dp0"
set "APP=hand-mouse"

rem Keep this file ASCII-only with CRLF line endings. Korean text or LF endings make cmd misread the lines.
rem Only uv is needed: it downloads Python 3.12 and the packages into .venv here.
rem If uv is missing, install it first (official script, per-user, no admin rights needed).
rem Options are passed through, e.g.  run.bat --phone-web   or   run.bat --source 1
where uv >nul 2>nul
if errorlevel 1 call :install_uv
if errorlevel 1 exit /b 1

echo [%APP%] Preparing Python and packages (the first run takes a few minutes)...
rem --inexact: keep the optional sets (qt, train, llm) that studio.bat installed; a bare sync removes them
uv sync --inexact
if errorlevel 1 goto fail

".venv\Scripts\python.exe" main.py %*
if errorlevel 1 (
  echo [%APP%] The app stopped with an error. See the messages above.
  pause
  exit /b 1
)
exit /b 0

:fail
echo [%APP%] uv sync failed. Check the internet connection and the messages above.
pause
exit /b 1

:install_uv
echo [%APP%] uv is not installed on this PC. Installing it first (needs internet)...
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
rem The installer updates PATH for new windows only, so add its folders to this window
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo [%APP%] Could not install uv. Check the internet connection, or run this in PowerShell and try again:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause
  exit /b 1
)
echo [%APP%] uv installed.
exit /b 0
