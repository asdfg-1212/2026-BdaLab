. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot
Initialize-ProjectEnvironment
$uv = Get-UvCommand
Write-Host "Installing locked local development dependencies into: $script:VenvDir"
& $uv sync --frozen --extra dev --no-install-project
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed. Keep the complete output above."
}
$python = Get-ProjectPython
& $uv pip check --python $python
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency integrity check failed."
}
Write-Host "Local development dependencies are installed and valid."
