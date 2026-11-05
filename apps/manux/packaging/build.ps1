# Builds the installed MANUX app from apps/manux: PyInstaller folder -> Velopack installer and update packages.
#   packaging/build.ps1 -Version 0.4.12-pr70                 # PR build: "MANUX Preview", never updates
#   packaging/build.ps1 -Version 0.4.12 -Release             # release build: package id MANUX, updates from GitHub
#   packaging/build.ps1 -Version 0.4.12-pr70 -SkipPack       # PyInstaller only (no .NET / vpk needed)
# vpk needs the .NET SDK: dotnet tool install -g vpk --version 1.2.161
#
# The PR build installs next to the v0.3 app (different package id and data folder), so testing it does not
# replace what is installed. Only -Release uses package id MANUX, which updates installed v0.3 apps: that is
# the release-channel switch of architecture.md section 8 (S6) and is not wired into any workflow yet.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$Release,
    [string]$SourceSha = "",
    [switch]$SkipPack
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if ($Release) { $packId = "MANUX"; $title = "MANUX"; $channel = "win"; $updates = $true }
    else { $packId = "MANUXPreview"; $title = "MANUX Preview"; $channel = "preview"; $updates = $false }
    New-Item -ItemType Directory -Force build | Out-Null
    $info = [ordered]@{ version = $Version; pack_id = $packId; channel = $channel; updates = $updates; source_sha = $SourceSha }
    [IO.File]::WriteAllText((Join-Path $root "build/build_info.json"), ($info | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

    uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub --extra package pyinstaller --noconfirm --clean `
        --distpath build/dist --workpath build/work packaging/manux.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub --extra package pyinstaller --noconfirm --clean `
        --distpath build/dist --workpath build/worker-work packaging/worker.spec
    if ($LASTEXITCODE -ne 0) { throw "Worker PyInstaller failed" }
    New-Item -ItemType Directory -Force build/dist/MANUX/worker | Out-Null
    Get-ChildItem -LiteralPath build/dist/MANUXWorker | Copy-Item -Destination build/dist/MANUX/worker -Recurse -Force
    if ($SkipPack) { return }
    vpk pack --packId $packId --packVersion $Version --packDir build/dist/MANUX --mainExe MANUX.exe `
        --packTitle $title --packAuthors "MANUX team" --icon packaging/manux.ico `
        --channel $channel --outputDir build/releases
    if ($LASTEXITCODE -ne 0) { throw "vpk pack failed" }
} finally {
    Pop-Location
}
