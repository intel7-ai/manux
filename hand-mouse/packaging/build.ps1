# Builds the installed MANUX app: PyInstaller folder -> Velopack installer and update packages.
#   packaging/build.ps1 -Version 0.3.7            # build/dist/MANUX and build/releases/MANUX-win-Setup.exe
#   packaging/build.ps1 -Version 0.3.7 -SkipPack  # PyInstaller only (no .NET / vpk needed)
# vpk needs the .NET SDK: dotnet tool install -g vpk --version 1.2.161
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Channel = "win",
    [switch]$SkipPack
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    uv run --locked --extra qt --extra package pyinstaller --noconfirm --clean `
        --distpath build/dist --workpath build/work packaging/manux.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    if ($SkipPack) { return }
    vpk pack --packId MANUX --packVersion $Version --packDir build/dist/MANUX --mainExe MANUX.exe `
        --packTitle MANUX --packAuthors "MANUX team" --icon packaging/manux.ico `
        --channel $Channel --outputDir build/releases
    if ($LASTEXITCODE -ne 0) { throw "vpk pack failed" }
} finally {
    Pop-Location
}
