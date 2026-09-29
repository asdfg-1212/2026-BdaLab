. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot
Initialize-ProjectEnvironment

& (Join-Path $PSScriptRoot "setup.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
$python = Get-ProjectPython

$testParent = Join-Path $script:RuntimeRoot "pytest\$script:SafeUserName"
New-Item -ItemType Directory -Force -Path $testParent | Out-Null
$testRoot = Join-Path $testParent ([guid]::NewGuid().ToString("N"))

try {
    & $python -m pytest -q -p no:cacheprovider --basetemp $testRoot
    $testExitCode = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = (Resolve-Path -LiteralPath $testRoot).Path
        $resolvedParent = (Resolve-Path -LiteralPath $testParent).Path
        if ($resolvedTestRoot.StartsWith($resolvedParent + [IO.Path]::DirectorySeparatorChar)) {
            Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($testExitCode -ne 0) {
    throw "Tests failed with exit code $testExitCode. Keep the complete output above."
}

& $python -m ruff check backend processing frontend tests
if ($LASTEXITCODE -ne 0) {
    throw "Code quality checks failed."
}
Write-Host "Tests and code quality checks passed."
