# Restart the Docker engine if needed and bring up a configured Compose project.
#
# Configuration:
#   WORKBENCH_COMPOSE_FILE  defaults to ~/.config/claude-workbench/compose.yml
#   WORKBENCH_HEAL_LOG      defaults to ~/.local/state/claude-workbench/docker-heal.log
# The Compose file should describe the service this hook is responsible for.

$ErrorActionPreference = 'Continue'
$configRoot = Join-Path $HOME '.config\claude-workbench'
$stateRoot = Join-Path $HOME '.local\state\claude-workbench'
$compose = if ($env:WORKBENCH_COMPOSE_FILE) {
    $env:WORKBENCH_COMPOSE_FILE
} else {
    Join-Path $configRoot 'compose.yml'
}
$log = if ($env:WORKBENCH_HEAL_LOG) {
    $env:WORKBENCH_HEAL_LOG
} else {
    Join-Path $stateRoot 'docker-heal.log'
}

$logParent = Split-Path -Parent $log
if ($logParent) { New-Item -ItemType Directory -Force -Path $logParent | Out-Null }

function Note($message) {
    "$((Get-Date).ToString('s')) $message" | Out-File -FilePath $log -Append -Encoding utf8
}

if (-not (Test-Path -Path $compose -PathType Leaf)) {
    Note "FAILED: Compose file not found at $compose"
    exit 2
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    if (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue) {
        Note 'engine not answering with Docker Desktop running: restarting it'
        docker desktop restart *> $null
    } else {
        Note 'Docker Desktop not running: starting it'
        docker desktop start *> $null
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Note 'FAILED: Docker engine still not answering'
        exit 1
    }
}

docker compose -f $compose up -d *> $null
if ($LASTEXITCODE -ne 0) {
    Note "FAILED: Compose up on $compose"
    exit 1
}
Note "OK: Docker engine and Compose project are up at $compose"
