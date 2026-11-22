$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$workspacePath = Split-Path $repoPath -Parent
$pythonPath = Join-Path $workspacePath '.venv-baseline/Scripts/python.exe'
$depsPath = Join-Path $workspacePath '.champion-deps'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
$previousPythonPath = $env:PYTHONPATH
Push-Location $repoPath
try {
    if (Test-Path -LiteralPath $depsPath) { $env:PYTHONPATH = "$depsPath;$previousPythonPath" }
    & $pythonPath -m experiments.online_replay.manual_studio
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
