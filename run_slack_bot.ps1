param(
    [switch]$UseExe
)

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

if (-not $env:SLACK_BOT_TOKEN) {
    throw "SLACK_BOT_TOKEN is required. Put it in .env or set it in this shell."
}

if (-not $env:SLACK_ANALYSIS_MODE) {
    $env:SLACK_ANALYSIS_MODE = "server"
}

if (-not $env:SLACK_BOT_HOST) {
    $env:SLACK_BOT_HOST = "0.0.0.0"
}

if (-not $env:SLACK_BOT_PORT) {
    $env:SLACK_BOT_PORT = "8780"
}

if ($env:SLACK_ANALYSIS_MODE -eq "server" -and -not $env:SLACK_ANALYSIS_SERVER_URL) {
    $env:SLACK_ANALYSIS_SERVER_URL = "http://127.0.0.1:8765"
}

$entry = ".\slack_bot.py"
if ($UseExe -and (Test-Path ".\dist\PlayerLogSlackBot\PlayerLogSlackBot.exe")) {
    $entry = ".\dist\PlayerLogSlackBot\PlayerLogSlackBot.exe"
}
elseif ($UseExe -and (Test-Path ".\dist\PlayerLogSlackBot.exe")) {
    $entry = ".\dist\PlayerLogSlackBot.exe"
}

Write-Host "Starting Slack bot on http://$($env:SLACK_BOT_HOST):$($env:SLACK_BOT_PORT) using mode $($env:SLACK_ANALYSIS_MODE)"
if ($env:SLACK_ALLOWED_CHANNELS) {
    Write-Host "Allowed channels: $($env:SLACK_ALLOWED_CHANNELS)"
}

if ($entry -like "*.exe") {
    & $entry
}
else {
    python $entry
}
