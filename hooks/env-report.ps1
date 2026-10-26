# SessionStart hook: write observed environment and repository state to state/<branch>.toon.
# Configuration: WORKBENCH_CONF, default ~/.claude/workbench.conf. The file
# must define REPO_PATH; MACHINE_BRANCH and SYNC_BRANCHES are optional.
$ErrorActionPreference = 'SilentlyContinue'

function Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 'absent' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
}

try {
    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
    if (-not (Test-Path -LiteralPath $conf)) { exit 0 }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $conf) {
        $idx = $line.IndexOf('=')
        if ($idx -gt 0) { $values[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim() }
    }
    $repo = $values['REPO_PATH']
    if (-not $repo -or -not (Test-Path -LiteralPath $repo)) { exit 0 }
    $branchName = $values['MACHINE_BRANCH']
    if (-not $branchName) { $branchName = 'local' }
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $branch = git -C $repo rev-parse --abbrev-ref HEAD 2>$null
    $commit = git -C $repo log -1 --oneline 2>$null
    $dirty = @(git -C $repo status --porcelain 2>$null).Count
    $behind = New-Object System.Collections.Generic.List[string]
    foreach ($name in ($values['SYNC_BRANCHES'] -split '[, ]+' | Where-Object { $_ })) {
        git -C $repo rev-parse --verify --quiet "origin/$name" *> $null
        if ($LASTEXITCODE -ne 0) { $behind.Add("origin/$name : not fetched"); continue }
        $count = git -C $repo rev-list --count "HEAD..origin/$name" 2>$null
        $behind.Add("origin/$name : $count commits ahead")
    }
    $settings = Join-Path $HOME '.claude\settings.json'
    $settingsLink = Get-Item -LiteralPath $settings -Force
    $linkState = if ($settingsLink.LinkType) { "symlink -> $($settingsLink.Target)" } else { 'regular file' }
    $now = Get-Date -Format 'yyyy-MM-dd HH:mm zzz'
    $out = @"
# $branchName, state at last SessionStart
# Observed facts only; regenerated on session start.

when: $now
session: $($payload.session_id)
cwd: $(if ($payload.cwd) { $payload.cwd } else { (Get-Location).Path })
permission_mode: $(if ($payload.permission_mode) { $payload.permission_mode } else { 'not included in payload' })

git:
  branch: $branch
  HEAD: $commit
  dirty_files: $dirty
$($behind | ForEach-Object { "  $_" } | Out-String)
settings:
  file_state: $linkState
  fingerprint: $(Sha256 $settings)
"@
    $stateDir = Join-Path $repo 'state'
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    $statePath = Join-Path $stateDir "$branchName.toon"
    Set-Content -LiteralPath $statePath -Value $out -Encoding UTF8
    git -C $repo add "state/$branchName.toon" *> $null
    git -C $repo commit --quiet -m "state: $branchName session start (env-report)" -- "state/$branchName.toon" *> $null
    if ($LASTEXITCODE -eq 0) { git -C $repo push --quiet *> $null }
} catch { Write-Output "ENV-REPORT ERROR: $($_.Exception.Message)" }
exit 0
