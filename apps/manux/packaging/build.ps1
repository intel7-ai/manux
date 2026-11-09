# Builds the installed MANUX app from apps/manux: PyInstaller folder -> Velopack installer and update packages.
#   packaging/build.ps1 -Version 0.5.12-pr70                # 기본 Preview, torch/worker 없음
#   packaging/build.ps1 -Version 0.5.12-pr70 -WithTrainer   # 고급 TCN 학습·HF worker 포함
#   packaging/build.ps1 -Version 0.5.12 -Release           # 정식 채널
#   packaging/build.ps1 -Version 0.5.12-pr70 -SkipPack     # PyInstaller 폴더 크기만 측정
# vpk needs the .NET SDK: dotnet tool install -g vpk --version 1.2.161
#
# The PR build installs next to the v0.3 app (different package id and data folder), so testing it does not
# replace what is installed. -Release uses package id MANUX; v0.3와의 업데이트 연속성은 보장하지 않는다.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$Release,
    [switch]$WithTrainer,
    [string]$SourceSha = "",
    [switch]$SkipPack
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Write-PackageSize {
    $dist = Join-Path $root "build/dist/MANUX"
    $distBytes = [long](Get-ChildItem -LiteralPath $dist -File -Recurse | Measure-Object -Property Length -Sum).Sum
    $installers = @()
    if (-not $SkipPack) {
        $installers = @(Get-ChildItem -LiteralPath (Join-Path $root "build/releases") -Filter '*-Setup.exe' -File | ForEach-Object {
            [ordered]@{ file = $_.Name; bytes = $_.Length; mb = [math]::Round($_.Length / 1e6, 3); mib = [math]::Round($_.Length / 1MB, 3) }
        })
    }
    $size = [ordered]@{
        flavor = $(if ($WithTrainer) { "with-trainer" } else { "basic" })
        version = $Version
        with_trainer = [bool]$WithTrainer
        dist_bytes = $distBytes
        dist_mb = [math]::Round($distBytes / 1e6, 3)
        dist_mib = [math]::Round($distBytes / 1MB, 3)
        installers = $installers
    }
    $json = $size | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path $root "build/package-size.json"), $json, [Text.UTF8Encoding]::new($false))
    Write-Output "MANUX $($size.flavor): folder $($size.dist_mb) MB ($($size.dist_mib) MiB)"
    foreach ($installer in $installers) {
        Write-Output "MANUX $($size.flavor): $($installer.file) $($installer.mb) MB ($($installer.mib) MiB)"
    }
}

Push-Location $root
try {
    if ($Release) { $packId = "MANUX"; $title = "MANUX"; $channel = "win"; $updates = $true }
    else { $packId = "MANUXPreview"; $title = "MANUX Preview"; $channel = "preview"; $updates = $false }
    New-Item -ItemType Directory -Force build | Out-Null
    $info = [ordered]@{ version = $Version; pack_id = $packId; channel = $channel; updates = $updates; source_sha = $SourceSha; with_trainer = [bool]$WithTrainer }
    [IO.File]::WriteAllText((Join-Path $root "build/build_info.json"), ($info | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

    # 이전 -WithTrainer 빌드의 worker가 기본 재빌드에 남지 않도록 정확한 경로만 정리한다.
    $workerDest = [IO.Path]::GetFullPath((Join-Path $root "build/dist/MANUX/worker"))
    $appDist = [IO.Path]::GetFullPath((Join-Path $root "build/dist/MANUX"))
    if ((Split-Path -Parent $workerDest) -ne $appDist -or (Split-Path -Leaf $workerDest) -ne "worker") {
        throw "Unexpected worker cleanup path"
    }
    if (Test-Path -LiteralPath $workerDest) { Remove-Item -LiteralPath $workerDest -Recurse -Force }
    uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra package pyinstaller --noconfirm --clean `
        --distpath build/dist --workpath build/work packaging/manux.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    if ($WithTrainer) {
        uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub --extra package pyinstaller --noconfirm --clean `
            --distpath build/dist --workpath build/worker-work packaging/worker.spec
        if ($LASTEXITCODE -ne 0) { throw "Worker PyInstaller failed" }
        New-Item -ItemType Directory -Force $workerDest | Out-Null
        Get-ChildItem -LiteralPath build/dist/MANUXWorker | Copy-Item -Destination $workerDest -Recurse -Force
    } elseif (Test-Path -LiteralPath $workerDest) { throw "Default build contains a trainer worker" }
    if ($SkipPack) { Write-PackageSize; return }
    vpk pack --packId $packId --packVersion $Version --packDir build/dist/MANUX --mainExe MANUX.exe `
        --packTitle $title --packAuthors "MANUX team" --icon packaging/manux.ico `
        --channel $channel --outputDir build/releases
    if ($LASTEXITCODE -ne 0) { throw "vpk pack failed" }
    Write-PackageSize
} finally {
    Pop-Location
}
