# SessionStart hook: print the priority queue into session context, so a
# fresh session starts on the right work without a memory-file treasure
# hunt. Condensed on purpose: only the numbered-item headlines from
# queue/global.md (its format contract keeps first lines self-contained)
# plus all of queue/current.md (the in-flight arc state). Windows
# counterpart of queue-print.sh (invoked via powershell, which macOS/Linux
# lack, so each side self-selects by platform). Reads
# ~/.claude/workbench.conf for the repo path. Prints [queue] lines and
# always exits 0: a broken print must never block a session.

# WORKBENCH_CONF overrides the conf location (mainly for testing). A missing
# conf or repo is session-start-sync's problem to report, stay quiet here.
$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }

$repo = $null
foreach ($line in (Get-Content $conf)) {
    if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim(); break }
}
if (-not $repo -or -not (Test-Path $repo)) { exit 0 }

# The queue is opt-in: no queue/ dir means this machine doesn't use it, stay
# quiet. A half-present queue (dir exists, a file missing) still warns below.
if (-not (Test-Path (Join-Path $repo 'queue'))) { exit 0 }

$global = Join-Path $repo 'queue\global.md'
$current = Join-Path $repo 'queue\current.md'
$inbox = Join-Path $repo 'queue\inbox.md'

# Remote drop point: unchecked items filed from elsewhere (the GitHub web
# UI, a phone) for this machine to pick up. Printed first because it is the
# freshest intent in the repo. Silent when empty, and optional: a missing
# inbox.md is not an error.
if (Test-Path $inbox) {
    $pending = Get-Content $inbox | Where-Object { $_ -match '^- \[ \]' }
    if ($pending) {
        Write-Output "[queue] INBOX: remote requests waiting (queue/inbox.md, work these first):"
        $pending
    }
}

if (Test-Path $global) {
    Write-Output "[queue] standing priorities (headlines; full detail in queue/global.md):"
    Get-Content $global | Where-Object { $_ -match '^\d+\.' }
} else {
    Write-Output "[queue] queue/global.md is missing from the repo. Recreate it (its contract is described in the surviving queue file's header, or in git history)."
}

if (Test-Path $current) {
    Write-Output "[queue] ----- queue/current.md -----"
    Get-Content $current
} else {
    Write-Output "[queue] queue/current.md is missing from the repo. Recreate it (its contract is described in the surviving queue file's header, or in git history)."
}

exit 0
