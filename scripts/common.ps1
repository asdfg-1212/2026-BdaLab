$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$script:SafeUserName = if ($currentIdentity) {
    $currentIdentity -replace '[^A-Za-z0-9_.-]', '_'
} else {
    "local-user"
}
$script:RuntimeRoot = Join-Path $script:ProjectRoot ".runtime"
$script:UvCacheDir = Join-Path $script:RuntimeRoot "uv-cache\$script:SafeUserName"
$script:VenvDir = Join-Path $script:RuntimeRoot "venvs\$script:SafeUserName"
$script:RuffCacheDir = Join-Path $script:RuntimeRoot "ruff-cache\$script:SafeUserName"

function Initialize-ProjectEnvironment {
    New-Item -ItemType Directory -Force -Path $script:UvCacheDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path $script:VenvDir -Parent) | Out-Null
    $env:UV_CACHE_DIR = $script:UvCacheDir
    $env:UV_PROJECT_ENVIRONMENT = $script:VenvDir
    $env:RUFF_CACHE_DIR = $script:RuffCacheDir
}

function Get-UvCommand {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        throw "uv was not found. Run: winget install --id astral-sh.uv -e, then reopen PowerShell."
    }
    return $uv.Source
}

function Get-ProjectPython {
    $python = Join-Path $script:VenvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Project dependencies are not installed. Run: .\scripts\setup.ps1 -Mode Local"
    }
    return $python
}

function Assert-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was not found. Install and start Docker Desktop with Linux containers."
    }

    function Test-DockerEngine {
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        docker info --format '{{.OSType}}' *> $null
        $engineReady = $LASTEXITCODE -eq 0
        $ErrorActionPreference = $previousErrorAction
        return $engineReady
    }

    if (Test-DockerEngine) {
        return
    }

    $dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $dockerDesktop -PathType Leaf)) {
        throw "Docker engine is not running and Docker Desktop was not found at its default path."
    }

    Write-Host "Docker engine is not running. Starting Docker Desktop..."
    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $dockerDesktop
    }

    for ($attempt = 1; $attempt -le 90; $attempt++) {
        if (Test-DockerEngine) {
            Write-Host "Docker engine is ready."
            return
        }
        if ($attempt % 5 -eq 0) {
            Write-Host "Waiting for Docker engine... ($($attempt * 2)s)"
        }
        Start-Sleep -Seconds 2
    }

    throw "Docker Desktop did not become ready within 180 seconds. Open Docker Desktop and check its error message."
}
