@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"
echo MANUX - testing
echo Preparing the app and training worker. The first launch downloads dependencies.
where uv > nul 2>&1
if errorlevel 1 (
    echo Install uv first: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)
call uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub python -m manux --ui studio %*
set "manux_exit=%errorlevel%"
if not "%manux_exit%"=="0" pause
exit /b %manux_exit%
