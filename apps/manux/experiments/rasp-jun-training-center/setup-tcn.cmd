@echo off
setlocal
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo Install uv first: https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)
uv sync --locked --extra qt --extra train --cache-dir .local/uv-cache --python 3.12
if errorlevel 1 (
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tcn_app.py --check
if errorlevel 1 (
  pause
  exit /b 1
)
echo MANUX TCN is ready. Open MANUX TCN launcher.
