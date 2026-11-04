@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call npm run verify
set "manux_result=%errorlevel%"
echo.
if "%manux_result%"=="0" (echo 기본 검사 통과. 실제 카메라와 OS 입력 확인은 별도입니다.) else (echo 검사 실패. .local\verification\verify.log 를 팀에 공유하세요.)
pause
exit /b %manux_result%
