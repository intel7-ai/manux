@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" call setup-tcn.cmd
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" tcn_app.py %*
if errorlevel 1 pause
