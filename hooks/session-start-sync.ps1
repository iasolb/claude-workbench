# SessionStart hook: workbench doctor checks + optional cross-machine branch
# sync. Windows counterpart of session-start-sync.sh (invoked via powershell,
# which macOS/Linux lack, so each side self-selects by platform the same way
# the notify hooks do). Reads ~/.claude/workbench.conf, written by the
# installer. Prints [workbench] lines into session context and always exits
# 0: a broken check must never block a session, visibility is the point.

# WORKBENCH_CONF overrides the conf location (mainly for testing).
$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) {
    Write-Output "[workbench] DOCTOR: no config at $conf. Run install\windows.ps1 from your workbench repo to create it."
    exit 0
}

$confValues = @{}
foreach ($line in (Get-Content $conf)) {
    $idx = $line.IndexOf('=')
    if ($idx -gt 0) {
        $confValues[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
    }
}
$repo = $confValues['REPO_PATH']
$machineBranch = $confValues['MACHINE_BRANCH']
$syncBranches = $confValues['SYNC_BRANCHES']

if (-not $repo -or -not (Test-Path (Join-Path $repo '.git'))) {
    Write-Output "[workbench] DOCTOR: repo not found at '$repo' (from $conf). If the repo moved, re-run install\windows.ps1 from its new location."
    exit 0
}

# 1. The project-key memory link must resolve into this repo, or memory
# writes land somewhere git never sees.
$projectKey = (Get-Location).Path -replace '[:\\/]', '-'
$memoryLink = Join-Path $HOME ".claude\projects\$projectKey\memory"
$expected = Join-Path $repo 'memory'
$item = Get-Item $memoryLink -Force -ErrorAction SilentlyContinue
if (-not $item) {
    Write-Output "[workbench] DOCTOR: no memory link at $memoryLink, memory writes are landing nowhere. Recreate it: cmd /c mklink /J `"$memoryLink`" `"$expected`""
} elseif (-not $item.LinkType) {
    Write-Output "[workbench] DOCTOR: $memoryLink is a real directory, not a link into the repo, so memories are not syncing. Merge its contents into $expected, then replace it with a junction (mklink /J)."
} else {
    $target = @($item.Target)[0]
    if ($target -ne $expected) {
        Write-Output "[workbench] DOCTOR: memory link points at $target, expected $expected. Re-point it: cmd /c rmdir `"$memoryLink`" && cmd /c mklink /J `"$memoryLink`" `"$expected`""
    }
}

# 2. This machine should stay on the branch recorded at install time.
if ($machineBranch) {
    $branch = git -C $repo rev-parse --abbrev-ref HEAD
    if ($branch -ne $machineBranch) {
        Write-Output "[workbench] DOCTOR: checkout is on '$branch', expected '$machineBranch'. Switch back (git -C `"$repo`" switch $machineBranch) or re-run the installer if the change is intentional."
        exit 0
    }
}

# No remote means nothing to fetch, merge, or push; doctor checks plus the
# dirty-tree report below are still worth having.
$hasOrigin = $false
git -C $repo remote get-url origin *> $null
if ($LASTEXITCODE -eq 0) {
    $hasOrigin = $true
    git -C $repo fetch origin --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Output '[workbench] fetch failed (offline?), working from local state, possibly stale.'
        exit 0
    }
}

# 3. Leftovers from a previous session (the SessionEnd hook stages, never
# commits). timelog/sessions.csv alone is machine-generated churn (the
# time-log hook appends rows every session), so it gets auto-committed and
# pushed instead of nagging; anything else is a real leftover to report.
$dirty = git -C $repo status --porcelain
$dirtyReal = @($dirty | Where-Object { $_ -notmatch 'timelog/sessions\.csv' })
if ($dirty -and -not $dirtyReal) {
    git -C $repo add timelog/sessions.csv *> $null
    git -C $repo commit --quiet -m 'timelog: session rows (auto-commit, session-start-sync)' *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Output '[workbench] auto-committed timelog session rows.'
        if ($hasOrigin) { git -C $repo push --quiet *> $null }
        $dirty = git -C $repo status --porcelain
    }
}
if ($dirty) {
    Write-Output "[workbench] Uncommitted changes in $repo left from a previous session. Commit them now (draft the message from the diff), then push."
}

# 4. Converge: merge each sync branch from the other machines. Conflicts are
# deliberately left in the worktree for Claude to resolve immediately.
if ($hasOrigin -and $syncBranches) {
    foreach ($b in ($syncBranches -split '[, ]+' | Where-Object { $_ })) {
        $other = "origin/$b"
        git -C $repo rev-parse --verify --quiet $other *> $null
        if ($LASTEXITCODE -ne 0) { continue }
        $behind = [int](git -C $repo rev-list --count "HEAD..$other")
        if ($behind -eq 0) { continue }
        if ($dirty) {
            Write-Output "[workbench] $other has $behind commit(s) not merged here. After committing the local changes above, run: git -C `"$repo`" merge --no-edit $other"
            continue
        }
        git -C $repo merge --no-edit $other *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Output "[workbench] merged $behind commit(s) from $other."
        } else {
            Write-Output "[workbench] MERGE CONFLICT with $other. Resolve it in $repo before any other work: keep both sides' facts, dedupe MEMORY.md, commit, push."
        }
    }
}

# 5. Anything unpushed, including merges just made.
if ($hasOrigin -and $machineBranch) {
    git -C $repo rev-parse --verify --quiet "origin/$machineBranch" *> $null
    if ($LASTEXITCODE -eq 0) {
        $ahead = [int](git -C $repo rev-list --count "origin/$machineBranch..HEAD")
        if ($ahead -gt 0) {
            Write-Output "[workbench] $ahead unpushed commit(s) on $machineBranch. Push: git -C `"$repo`" push"
        }
    } else {
        Write-Output "[workbench] branch $machineBranch has no upstream yet. Push: git -C `"$repo`" push -u origin $machineBranch"
    }
}

exit 0
