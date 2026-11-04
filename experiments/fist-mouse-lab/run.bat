@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" /b ".venv\Scripts\pythonw.exe" -B main.py
    exit /b 0
)
if exist "..\manux-tracking-lab\.venv\Scripts\pythonw.exe" (
    start "" /b "..\manux-tracking-lab\.venv\Scripts\pythonw.exe" -B main.py
    exit /b 0
)
echo Python environment missing. Run setup.bat first with Python 3.12 installed.
exit /b 1
