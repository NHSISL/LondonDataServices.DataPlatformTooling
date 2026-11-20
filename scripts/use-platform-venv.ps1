param([string]$VenvDir)

$platformRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $VenvDir) {
    $VenvDir = Join-Path $platformRoot '.venv'
}
elseif (-not [System.IO.Path]::IsPathRooted($VenvDir)) {
    $VenvDir = Join-Path $platformRoot $VenvDir
}
$venvDir = [System.IO.Path]::GetFullPath($VenvDir)
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvDbt = Join-Path $venvDir 'Scripts\dbt.exe'
$venvActivate = Join-Path $venvDir 'Scripts\Activate.ps1'

foreach ($requiredFile in @($venvPython, $venvDbt, $venvActivate)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Shared virtual environment is not ready. Run '$platformRoot\setup.ps1' first. Missing: $requiredFile"
    }
}

$pythonVersionFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'PYTHON_VERSION'
$expectedPythonVersion = (Get-Content -LiteralPath $pythonVersionFile -Raw).Trim()
$actualPythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $actualPythonVersion -ne $expectedPythonVersion) {
    throw "Shared virtual environment must use Python $expectedPythonVersion. Run '$platformRoot\setup.ps1' with the same -VenvDir."
}
& $venvPython -c "import dbt_platform_tooling"
if ($LASTEXITCODE -ne 0) {
    throw "Platform tooling is not installed in '$venvDir'. Run '$platformRoot\setup.ps1' with the same -VenvDir."
}
Write-Host "Using shared virtual environment: $venvDir" -ForegroundColor Cyan
