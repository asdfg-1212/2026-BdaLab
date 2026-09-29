. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot
Assert-DockerReady

$envFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $script:ProjectRoot ".env.example") -Destination $envFile
}

$keyLine = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*LLM_API_KEY\s*=\s*.+$' } | Select-Object -First 1
if (-not $keyLine -or $keyLine -match '^\s*LLM_API_KEY\s*=\s*$') {
    Write-Host "Model configuration is missing. Starting secure configuration..."
    & (Join-Path $PSScriptRoot "configure.ps1")
    $keyLine = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*LLM_API_KEY\s*=\s*.+$' } | Select-Object -First 1
    if (-not $keyLine) {
        throw "Model configuration was not completed."
    }
}

$requiredData = @("users.dat", "movies.dat", "ratings.dat")
foreach ($name in $requiredData) {
    $path = Join-Path $script:ProjectRoot "ml-1m\$name"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing data file: $path"
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $script:ProjectRoot "artifacts") | Out-Null
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker compose config --quiet
$composeExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($composeExitCode -ne 0) {
    throw "Docker Compose configuration validation failed."
}

Write-Host "Building and starting the system in background mode."
Write-Host "Using TUNA mirrors for Ubuntu, PyPI and Hadoop (lean package)."
Write-Host "The first build still downloads a Java base image and about 489 MB of Hadoop files."
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker compose up --build --detach --wait --wait-timeout 300 --quiet-pull
$composeExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($composeExitCode -ne 0) {
    docker compose ps
    docker compose logs --tail 80
    throw "Docker Compose startup failed. Keep the complete output above."
}

Write-Host "System is running."
Write-Host "Frontend: http://localhost:8501"
Write-Host "Backend API: http://localhost:8000/docs"
Write-Host "Stop command: scripts\stop.cmd"
