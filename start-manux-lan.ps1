param(
  [string]$LanAddress = '10.10.21.31'
)
$ErrorActionPreference = 'Stop'
$taskAppRoot = $PSScriptRoot
$taskNode = (Get-Command node.exe -ErrorAction Stop).Source
$taskServer = Join-Path $taskAppRoot 'server.js'
$taskLanRoot = Join-Path $taskAppRoot '.local/lan'
$taskKey = Join-Path $taskLanRoot 'server-key.pem'
$taskCert = Join-Path $taskLanRoot 'server-cert.pem'
if (-not (Test-Path -LiteralPath $taskKey) -or -not (Test-Path -LiteralPath $taskCert)) {
  throw 'LAN certificate is missing. See docs/lan-demo.md to create it.'
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $LanAddress -ErrorAction SilentlyContinue)) {
  throw "This PC does not own LAN address $LanAddress. Update the address and certificate together."
}
& $taskNode --input-type=module -e 'import {X509Certificate} from "node:crypto"; import {readFileSync} from "node:fs"; const cert = new X509Certificate(readFileSync(process.argv[1])); if (!cert.checkIP(process.argv[2]) || Date.parse(cert.validTo) <= Date.now()) throw new Error("LAN certificate expired or does not match the address.");' $taskCert $LanAddress
if ($LASTEXITCODE -ne 0) { throw 'LAN certificate validation failed.' }
$taskSavedEnv = @{}
foreach ($taskName in @('HOST','PORT','TLS_KEY_FILE','TLS_CERT_FILE')) {
  $taskSavedEnv[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}
try {
  foreach ($taskPort in @(4173,4174)) {
    $taskPidFile = Join-Path $taskLanRoot "server-$taskPort.json"
    $taskListener = @(Get-NetTCPConnection -State Listen -LocalAddress $LanAddress -LocalPort $taskPort -ErrorAction SilentlyContinue)
    if ($taskListener.Count) {
      $taskRecorded = if (Test-Path -LiteralPath $taskPidFile) { Get-Content -LiteralPath $taskPidFile -Raw | ConvertFrom-Json } else { $null }
      $taskRunning = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskListener[0].OwningProcess)"
      if (-not $taskRecorded -or $taskRecorded.processId -ne $taskListener[0].OwningProcess -or $taskRecorded.createdTicks -ne $taskRunning.CreationDate.ToUniversalTime().Ticks.ToString() -or $taskRecorded.address -ne $LanAddress -or $taskRunning.ExecutablePath -ne $taskNode -or -not $taskRunning.CommandLine.Contains($taskServer)) {
        throw "Port $taskPort belongs to another process. It has not been stopped."
      }
      Write-Host "MANUX LAN port $taskPort is already running."
      continue
    }
    $env:HOST = $LanAddress
    $env:PORT = [string]$taskPort
    $env:TLS_KEY_FILE = if ($taskPort -eq 4174) { $taskKey } else { $null }
    $env:TLS_CERT_FILE = if ($taskPort -eq 4174) { $taskCert } else { $null }
    $taskProcess = Start-Process -FilePath $taskNode -ArgumentList ('"' + $taskServer + '"') -WorkingDirectory $taskAppRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLanRoot "server-$taskPort.log") -RedirectStandardError (Join-Path $taskLanRoot "server-$taskPort-error.log") -PassThru
    Start-Sleep -Milliseconds 500
    $taskProcess.Refresh()
    if ($taskProcess.HasExited) { throw "MANUX port $taskPort failed. See .local/lan/server-$taskPort-error.log." }
    $taskRunning = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskProcess.Id)"
    @{ processId = $taskProcess.Id; createdTicks = $taskRunning.CreationDate.ToUniversalTime().Ticks.ToString(); address = $LanAddress; port = $taskPort } | ConvertTo-Json | Set-Content -LiteralPath $taskPidFile
  }
} finally {
  foreach ($taskName in $taskSavedEnv.Keys) { [Environment]::SetEnvironmentVariable($taskName, $taskSavedEnv[$taskName], 'Process') }
}
Write-Host "LAN page: http://${LanAddress}:4173"
Write-Host "LAN camera demo: https://${LanAddress}:4174"
