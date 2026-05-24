# ============================================================
# Hermes Matzip — Push configs & secrets to EC2 via SCP
#
# Usage (Git Bash / PowerShell with OpenSSH):
#   .\deploy\push-to-vm.ps1 -IP 1.2.3.4 -KeyFile C:\path\to\key.pem
#
# Prerequisites:
#   1. EC2 Ubuntu 22.04 instance running with SSH access
#   2. hbst-agent repo cloned on VM:
#        ssh -i key.pem -A ubuntu@IP
#        git clone git@github.com:shchun/hbst-agent.git ~/hbst-agent
#   3. Run this script from Windows to push secrets
#   4. Then on VM: bash ~/hbst-agent/deploy/setup-vm.sh
# ============================================================

param(
    [Parameter(Mandatory=$true)][string]$IP,
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [string]$User = "ubuntu"
)

$ErrorActionPreference = "Stop"
$REMOTE = "${User}@${IP}"
$SSH_OPTS = @("-i", $KeyFile, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes")

# ── Read local Hermes secrets ─────────────────────────────────────────────────
$localEnv  = "$env:USERPROFILE\AppData\Local\hermes\.env"
$localConf = "$env:USERPROFILE\AppData\Local\hermes\config.yaml"
$localSoul = "$env:USERPROFILE\AppData\Local\hermes\SOUL.md"

if (-not (Test-Path $localEnv)) { throw "Not found: $localEnv" }
if (-not (Test-Path $localConf)) { throw "Not found: $localConf" }

function Read-EnvFile($path) {
    $map = @{}
    Get-Content $path | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
        $k, $v = $_ -split '=', 2
        $map[$k.Trim()] = $v.Trim()
    }
    return $map
}

function Read-YamlValue($content, $key) {
    if ($content -match "$key\s*:\s*[`"']?([^`"'\r\n]+)[`"']?") { return $Matches[1].Trim('"').Trim("'") }
    return $null
}

$env = Read-EnvFile $localEnv
$conf = Get-Content $localConf -Raw

$openaiKey      = $env['OPENAI_API_KEY']
$slackBot       = $env['SLACK_BOT_TOKEN']
$slackApp       = $env['SLACK_APP_TOKEN']
$slackUsers     = $env['SLACK_ALLOWED_USERS']
$slackHomeChannel     = $env['SLACK_HOME_CHANNEL']
$slackHomeChannelName = $env['SLACK_HOME_CHANNEL_NAME']
$googleKey   = Read-YamlValue $conf 'GOOGLE_MAPS_API_KEY'
$slackChannel= Read-YamlValue $conf 'SLACK_CHANNEL'

Write-Host "Secrets loaded from local Hermes config" -ForegroundColor Green
Write-Host "  OPENAI_API_KEY  : $($openaiKey.Substring(0,10))..."
Write-Host "  SLACK_BOT_TOKEN : $($slackBot.Substring(0,15))..."
Write-Host "  SLACK_CHANNEL   : $slackChannel"
Write-Host "  GOOGLE_MAPS_KEY : $($googleKey.Substring(0,10))..."

# ── Generate VM config.yaml from deploy/config.template.yaml ──────────────────
$homeDir  = "/home/$User"
$appDir = "$homeDir/hbst-agent"
$vaultDir = "$homeDir/hbst-obsidian"

$templatePath = Join-Path $PSScriptRoot "config.template.yaml"
if (-not (Test-Path $templatePath)) { throw "Not found: $templatePath" }

$vmConfig = (Get-Content $templatePath -Raw).
    Replace('__APP_DIR__', $appDir).
    Replace('__VAULT_DIR__', $vaultDir).
    Replace('__GOOGLE_MAPS_API_KEY__', $googleKey).
    Replace('__SLACK_BOT_TOKEN__', $slackBot).
    Replace('__SLACK_CHANNEL__', $slackChannel)

$vmEnv = @"
OPENAI_API_KEY=$openaiKey
SLACK_BOT_TOKEN=$slackBot
SLACK_APP_TOKEN=$slackApp
SLACK_ALLOWED_USERS=$slackUsers
SLACK_HOME_CHANNEL=$slackHomeChannel
SLACK_HOME_CHANNEL_NAME=$slackHomeChannelName
"@

# Write to temp files
$tmpConfig = [System.IO.Path]::GetTempFileName() + ".yaml"
$tmpEnv    = [System.IO.Path]::GetTempFileName()
$vmConfig | Out-File -FilePath $tmpConfig -Encoding utf8 -NoNewline
$vmEnv    | Out-File -FilePath $tmpEnv    -Encoding utf8 -NoNewline

try {
    # ── Ensure ~/.hermes exists on VM ─────────────────────────────────────────
    Write-Host "`nCreating ~/.hermes on VM..." -ForegroundColor Cyan
    & ssh @SSH_OPTS $REMOTE "mkdir -p ~/.hermes"

    # ── SCP config files ──────────────────────────────────────────────────────
    Write-Host "Pushing config.yaml..." -ForegroundColor Cyan
    & scp @SSH_OPTS $tmpConfig "${REMOTE}:~/.hermes/config.yaml"

    Write-Host "Pushing .env..." -ForegroundColor Cyan
    & scp @SSH_OPTS $tmpEnv "${REMOTE}:~/.hermes/.env"

    if (Test-Path $localSoul) {
        Write-Host "Pushing SOUL.md..." -ForegroundColor Cyan
        & scp @SSH_OPTS $localSoul "${REMOTE}:~/.hermes/SOUL.md"
    }

    Write-Host "`nAll secrets pushed successfully!" -ForegroundColor Green
} finally {
    Remove-Item $tmpConfig, $tmpEnv -ErrorAction SilentlyContinue
}

Write-Host @"

Next steps:
  1. SSH to VM (with agent forwarding to clone private repo):
       ssh -A -i "$KeyFile" $REMOTE

  2. Clone hbst-agent repo (if not done yet):
       git clone git@github.com:shchun/hbst-agent.git ~/hbst-agent

  3. Run setup script:
       bash ~/hbst-agent/deploy/setup-vm.sh

  4. Install gateway service:
       source ~/.bashrc
       hermes gateway setup
       hermes gateway install
"@
