@echo off
rem Starts the hand mouse app (Qt screen) as administrator so it can also control admin windows such as Task Manager.
rem Windows asks once for permission (UAC). Keep this file ASCII-only with CRLF line endings.
rem It does not run uv sync: a plain uv sync would remove the qt and train packages from .venv.
net session >nul 2>nul
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --ui qt %*
if errorlevel 1 pause
