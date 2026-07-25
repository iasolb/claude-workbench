# SessionEnd hook: stage (never commit or push) any pending changes in the
# workbench repo, and notify if anything got staged. Windows counterpart of
# session-end-stage.sh.

$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }
$repo = (Get-Content $conf | Where-Object { $_ -match '^REPO_PATH=' } | Select-Object -First 1) -replace '^REPO_PATH=', ''
$repo = $repo.Trim()
if (-not $repo -or -not (Test-Path (Join-Path $repo ".git"))) { exit 0 }

Push-Location $repo
git add -A
$staged = git diff --cached --name-only
Pop-Location

if (-not $staged) {
    exit 0
}

$repoName = Split-Path -Leaf $repo
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.BalloonTipTitle = "Claude Code"
$notify.BalloonTipText = "Changes staged in $repoName, ready to review"
$notify.ShowBalloonTip(4000)
Start-Sleep -Seconds 3
$notify.Dispose()
