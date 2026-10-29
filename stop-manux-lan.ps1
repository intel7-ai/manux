$ErrorActionPreference = 'Stop'
$taskNode = (Get-Command node.exe -ErrorAction Stop).Source
$taskServer = Join-Path $PSScriptRoot 'server.js'
foreach ($taskPort in @(4173,4174)) {
  $taskPidFile = Join-Path $PSScriptRoot ".local/lan/server-$taskPort.json"
  if (-not (Test-Path -LiteralPath $taskPidFile)) { continue }
  $taskRecorded = Get-Content -LiteralPath $taskPidFile -Raw | ConvertFrom-Json
  $taskRecordedPid = [string]$taskRecorded.processId
  if ($taskRecordedPid -notmatch '^\d+$' -or -not $taskRecorded.createdTicks) { throw "Invalid PID file: $taskPidFile" }
  $taskRunning = Get-CimInstance Win32_Process -Filter "ProcessId = $taskRecordedPid"
  if ($taskRunning) {
    if ($taskRunning.CreationDate.ToUniversalTime().Ticks.ToString() -ne $taskRecorded.createdTicks -or $taskRunning.ExecutablePath -ne $taskNode -or -not $taskRunning.CommandLine.Contains($taskServer)) {
      throw "PID $taskRecordedPid no longer belongs to this LAN demo. It has not been stopped."
    }
    Stop-Process -Id ([int]$taskRecordedPid)
  }
  Remove-Item -LiteralPath $taskPidFile
}
Write-Host 'MANUX LAN servers stopped. The separate localhost server is unchanged.'
