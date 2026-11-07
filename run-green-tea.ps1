param(
    [string]$SourceZip = (Join-Path $HOME "Downloads/Sample.zip"),
    [int]$Port = 8512
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python is missing. Run uv sync --locked in the farmcheck directory first."
}
if (-not (Test-Path -LiteralPath $SourceZip -PathType Leaf)) {
    throw "Source ZIP not found: $SourceZip"
}

$settings = @{
    FARM_DATA_MODE = "green_tea"
    FARM_SOURCE_ZIP = (Resolve-Path -LiteralPath $SourceZip).Path
    FARM_MODEL_PROFILE = "pilot"
    FARM_DB_PATH = (Join-Path $PSScriptRoot "artifacts/private/green-tea-connected.sqlite")
    APP_BASE_PATH = ""
    APP_ROUTE_PREFIX = ""
}
$previous = @{}
foreach ($name in $settings.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $settings[$name], "Process")
}

Push-Location $PSScriptRoot
try {
    Write-Host "FarmCheck green-tea pilot: http://127.0.0.1:$Port/"
    & $python -m uvicorn farmcheck.app:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "FarmCheck exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
    foreach ($name in $settings.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
    }
}
