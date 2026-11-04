@echo off
setlocal
cd /d "%~dp0"
if exist "node_modules\electron\dist\electron.exe" (
  "node_modules\electron\dist\electron.exe" .
) else if exist "..\manux-park-20261104\node_modules\electron\dist\electron.exe" (
  "..\manux-park-20261104\node_modules\electron\dist\electron.exe" .
) else (
  echo Electron runtime not found. Run npm install in this folder.
  pause
)
