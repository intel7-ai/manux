$ErrorActionPreference = 'Stop'
$taskAppRoot = $PSScriptRoot
Set-Location -LiteralPath $taskAppRoot
$taskNode = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $taskNode) { throw 'Node.js is required. Install Node.js LTS and run this launcher again.' }
if (-not (Test-Path -LiteralPath 'node_modules/@mediapipe/tasks-vision/vision_bundle.mjs')) {
  & npm.cmd install --ignore-scripts --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
}
if (-not (Test-Path -LiteralPath 'public/models/gesture_recognizer.task')) {
  & $taskNode.Source 'scripts/setup.mjs'
  if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' }
}
$taskServerReady = $false
try {
  $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:4173/health' -TimeoutSec 2
  if ($taskHealth.app -ne 'MANUX') { throw 'Port 4173 belongs to another application.' }
  $taskServerReady = $true
} catch {
  if ($_.Exception.Message -eq 'Port 4173 belongs to another application.') { throw }
}
if (-not $taskServerReady) {
  New-Item -ItemType Directory -Path 'logs' -Force | Out-Null
  $env:PORT = '4173'
  $taskProcess = Start-Process -FilePath $taskNode.Source -ArgumentList 'server.js' -WorkingDirectory $taskAppRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskAppRoot 'logs/server.log') -RedirectStandardError (Join-Path $taskAppRoot 'logs/server-error.log') -PassThru
  $taskProcess.Id | Set-Content -LiteralPath 'logs/server.pid'
  for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
    Start-Sleep -Milliseconds 200
    try {
      $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:4173/health' -TimeoutSec 1
      if ($taskHealth.app -eq 'MANUX') { $taskServerReady = $true; break }
    } catch {}
    if ($taskProcess.HasExited) { break }
  }
}
if (-not $taskServerReady) { throw 'Could not start MANUX. Check logs/server-error.log.' }
Start-Process 'http://127.0.0.1:4173'
Write-Host 'MANUX is running at http://127.0.0.1:4173'
