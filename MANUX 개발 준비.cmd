@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo MANUX 개발 환경을 준비합니다. 인터넷 연결이 필요합니다.
where node >nul 2>&1
if errorlevel 1 goto missing
where npm >nul 2>&1
if errorlevel 1 goto missing
call npm ci --ignore-scripts
if errorlevel 1 goto failed
call npm run doctor
if errorlevel 1 goto failed
call npm run verify
if errorlevel 1 goto failed
echo.
echo 기본 준비가 완료되었습니다. 앱 실행은 README.md를 확인하세요.
pause
exit /b 0
:missing
echo Node.js 24 LTS와 Git for Windows를 설치한 뒤 이 파일을 다시 실행하세요.
:failed
echo 준비를 완료하지 못했습니다. 위 FAIL 메시지를 팀 채널에 공유하세요.
pause
exit /b 1
