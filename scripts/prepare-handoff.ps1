[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = 'Stop'
$taskSourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskDestination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
$taskDestination = [IO.Path]::GetFullPath($taskDestination)
$taskSourcePrefix = $taskSourceRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($taskDestination.Equals($taskSourceRoot, [StringComparison]::OrdinalIgnoreCase) -or $taskDestination.StartsWith($taskSourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Destination must be a new folder outside the app source tree.'
}
if (Test-Path -LiteralPath $taskDestination) { throw 'Destination already exists. Choose a new path; this script never deletes or overwrites it.' }

$taskRequired = @('README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'package.json', 'package-lock.json', 'docs/master-plan.md', 'docs/contracts.md', 'docs/github-handoff.md')
foreach ($taskRelative in $taskRequired) {
  if (-not (Test-Path -LiteralPath (Join-Path $taskSourceRoot $taskRelative) -PathType Leaf)) { throw "Required handoff file not ready: $taskRelative" }
}
$taskWorkstreams = @(Get-ChildItem -LiteralPath (Join-Path $taskSourceRoot 'docs/workstreams') -File -Filter '*.md')
if ($taskWorkstreams.Count -lt 5) { throw 'Expected at least five role workstream documents.' }

$taskFiles = [Collections.Generic.List[IO.FileInfo]]::new()
$taskTopFiles = @('.gitignore', 'README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'package.json', 'package-lock.json', 'server.js', 'start-manux.ps1', 'start-manux-lan.ps1', 'stop-manux-lan.ps1', 'MANUX 실행.cmd')
foreach ($taskRelative in $taskTopFiles) { $taskFiles.Add((Get-Item -LiteralPath (Join-Path $taskSourceRoot $taskRelative))) }
foreach ($taskDir in @('src', 'scripts', 'tests', 'public', 'docs', '.github')) {
  foreach ($taskFile in (Get-ChildItem -LiteralPath (Join-Path $taskSourceRoot $taskDir) -Recurse -File -Force)) {
    $taskRelative = $taskFile.FullName.Substring($taskSourcePrefix.Length).Replace('\', '/')
    $taskAllowed = switch -Regex ($taskRelative) {
      '^src/[^/]+\.js$' { $true; break }
      '^scripts/[^/]+\.(mjs|ps1)$' { $true; break }
      '^tests/[^/]+\.test\.js$' { $true; break }
      '^tests/fixtures/(sources|personalization-reference)\.json$' { $true; break }
      '^public/(index\.html|styles\.css|assets/manux\.svg|models/model-source\.json)$' { $true; break }
      '^docs/[^/]+\.(md|json)$' { $true; break }
      '^docs/workstreams/[^/]+\.md$' { $true; break }
      '^docs/presentation/[^/]+\.(md|json)$' { $true; break }
      '^docs/presentation/build-deck\.mjs$' { $true; break }
      '^docs/presentation/[^/]+\.(pptx|pdf)$' { $true; break }
      '^docs/presentation/assets/README\.md$' { $true; break }
      '^docs/presentation/assets/product-photo-sources\.json$' { $true; break }
      '^docs/presentation/assets/[^/]+\.(png|jpg|jpeg|svg|webp)$' { $true; break }
      '^\.github/(PULL_REQUEST_TEMPLATE\.md|workflows/[^/]+\.ya?ml)$' { $true; break }
      default { $false }
    }
    if ($taskAllowed) { $taskFiles.Add($taskFile) }
  }
}

$taskFiles = @($taskFiles | Sort-Object FullName -Unique)
$taskTokenPattern = '(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[A-Z0-9]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)'
$taskSuspects = @()
foreach ($taskFile in $taskFiles) {
  if (($taskFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Refusing linked source file: $($taskFile.Name)" }
  if ($taskFile.Extension -notin @('.pptx', '.pdf', '.png', '.jpg', '.jpeg', '.webp')) {
    $taskContent = [IO.File]::ReadAllText($taskFile.FullName)
    if ($taskContent -match $taskTokenPattern) { $taskSuspects += $taskFile.FullName.Substring($taskSourcePrefix.Length) }
  }
}
if ($taskSuspects.Count -gt 0) { throw ('Inspect possible credentials before packaging. Files: ' + ($taskSuspects -join ', ')) }

New-Item -ItemType Directory -Path $taskDestination | Out-Null
$taskManifest = @()
foreach ($taskFile in $taskFiles) {
  $taskRelative = $taskFile.FullName.Substring($taskSourcePrefix.Length)
  $taskTarget = Join-Path $taskDestination $taskRelative
  $taskParent = Split-Path -Parent $taskTarget
  if (-not (Test-Path -LiteralPath $taskParent)) { New-Item -ItemType Directory -Path $taskParent -Force | Out-Null }
  Copy-Item -LiteralPath $taskFile.FullName -Destination $taskTarget
  $taskSourceHash = (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  $taskCopiedHash = (Get-FileHash -LiteralPath $taskTarget -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($taskSourceHash -ne $taskCopiedHash) { throw "Copy hash mismatch: $taskRelative" }
  $taskManifest += [ordered]@{path = $taskRelative.Replace('\', '/'); bytes = $taskFile.Length; sha256 = $taskCopiedHash}
}
$taskPayload = [ordered]@{
  purpose = 'MANUX source handoff baseline. Not a release or verified usability result.'
  preparedAt = [DateTimeOffset]::Now.ToString('o')
  source = 'MANUX app working-file snapshot; Git publication is managed separately'
  exclusions = @('.git', 'node_modules', '.npm-cache', '.tools', '.local', 'TLS certificates and private keys', 'logs', 'test-results', 'raw camera recordings', 'user profiles', '*.task', 'public/media videos', 'tests/fixtures/*.jpg')
  fileCount = $taskManifest.Count
  files = $taskManifest
}
$taskJson = $taskPayload | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $taskDestination '.handoff-manifest.json'), $taskJson, [Text.UTF8Encoding]::new($false))
Write-Output "Prepared $($taskManifest.Count) files in $taskDestination"
Write-Output 'Source hashes match. No Git commit, remote, push, or Jira mutation was performed.'
