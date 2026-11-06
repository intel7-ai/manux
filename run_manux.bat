@echo off
setlocal
call "%~dp0apps\manux\run_manux.bat" %*
exit /b %errorlevel%
