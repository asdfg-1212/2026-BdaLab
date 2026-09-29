param(
    [string]$BaseUrl = "https://api.deepseek.com",
    [string]$Model = "deepseek-chat"
)

. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot

$secureKey = Read-Host "Enter LLM API key (input is hidden)" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw "LLM API key cannot be empty."
    }
    if ($plainKey.Contains("`r") -or $plainKey.Contains("`n")) {
        throw "LLM API key cannot contain line breaks."
    }

    $lines = @(
        "# Keep one KEY=value entry per line. Never commit this file.",
        "LLM_API_KEY=$plainKey",
        "LLM_BASE_URL=$BaseUrl",
        "LLM_MODEL=$Model"
    )
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines((Join-Path $script:ProjectRoot ".env"), $lines, $utf8WithoutBom)
} finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $plainKey = $null
}

Write-Host "Model configuration saved to .env."
