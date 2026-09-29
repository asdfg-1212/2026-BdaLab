. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found."
}

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker compose down
$composeExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($composeExitCode -ne 0) {
    throw "Docker Compose stop failed."
}
Write-Host "System stopped. Task artifacts were kept."
