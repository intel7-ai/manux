# Everyday mouse checks use the existing environment and never open a camera or send OS input.
param(
    [ValidateSet('mouse', 'full')]
    [string]$Suite = 'mouse',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create the environment first: uv sync --locked --python 3.12'
}
$testArgs = @('-q')
if ($Suite -eq 'mouse') {
    $testArgs += @(
        'tests/unit/features/test_drag_speed_guard.py',
        'tests/unit/features/test_fist_speed_output.py',
        'tests/unit/features/test_pinch_responsiveness.py',
        'tests/unit/features/test_spatial_desktop_safety.py',
        'tests/unit/output/test_windows_interp.py',
        'tests/unit/output/test_windows_responsiveness.py',
        'tests/unit/output/test_windows_sink.py',
        'tests/unit/engine',
        'tests/contract/test_contracts.py',
        'tests/architecture'
    )
}

$oldUtf8, $oldTemp, $oldTmp = $env:PYTHONUTF8, $env:TEMP, $env:TMP
Push-Location $repo
try {
    $env:PYTHONUTF8 = '1'
    if ($Suite -eq 'full') {
        # Avoid sandbox-inherited Temp permissions and repository-only data restrictions.
        $tempRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Temp'
        $runTemp = Join-Path $tempRoot ('manux-tests-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $runTemp | Out-Null
        $env:TEMP = $runTemp
        $env:TMP = $runTemp
        $testArgs += @('--basetemp', (Join-Path $runTemp 'pytest'))
    }
    & $python -m pytest @testArgs @PytestArgs
    $testExit = $LASTEXITCODE
}
finally {
    $env:PYTHONUTF8, $env:TEMP, $env:TMP = $oldUtf8, $oldTemp, $oldTmp
    Pop-Location
}
exit $testExit
