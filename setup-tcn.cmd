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
uv sync --locked --extra qt --extra train --extra llm --cache-dir .local/uv-cache --python 3.12
if errorlevel 1 (
  pause
  exit /b 1
)
where nvidia-smi >nul 2>nul
if not errorlevel 1 (
  echo NVIDIA GPU detected. Installing CUDA-enabled PyTorch for GPU training acceleration...
  uv pip install --python ".venv\Scripts\python.exe" --reinstall-package torch torch --index-url https://download.pytorch.org/whl/cu132 --cache-dir .local/uv-cache
)
".venv\Scripts\python.exe" tcn_app.py --check
if errorlevel 1 (
  pause
  exit /b 1
)
echo MANUX TCN is ready. Open MANUX TCN launcher.
