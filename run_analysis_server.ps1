$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Import-DotEnvIfPresent {
    param(
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$") {
            continue
        }

        $name = $matches[1]
        $value = $matches[2].Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if (-not [string]::IsNullOrWhiteSpace($value) -and -not (Test-Path "Env:$name")) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

Import-DotEnvIfPresent -Path (Join-Path $root ".env")

if ($env:KEEP_SYSTEM_PROXY -ne "1") {
    Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY,Env:http_proxy,Env:https_proxy,Env:all_proxy -ErrorAction SilentlyContinue
}

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is required. Put it in .env or set it in this shell."
}

if (-not $env:OPENAI_MODEL) {
    $env:OPENAI_MODEL = "gpt-5-mini"
}

if (-not $env:PLAYER_LOG_AI_SERVER_HOST) {
    $env:PLAYER_LOG_AI_SERVER_HOST = "0.0.0.0"
}

if (-not $env:PLAYER_LOG_AI_SERVER_PORT) {
    $env:PLAYER_LOG_AI_SERVER_PORT = "8765"
}

Write-Host "Starting analysis server on http://$($env:PLAYER_LOG_AI_SERVER_HOST):$($env:PLAYER_LOG_AI_SERVER_PORT) with model $($env:OPENAI_MODEL)"
python .\analysis_server.py
