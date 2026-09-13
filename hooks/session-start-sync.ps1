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
# A dirty tree used to SKIP the merge entirely, so a rule or settings fix
# pushed from another environment could sit unmerged for sessions while this
# machine kept running the old config. Convergence is not optional: stash,
# merge, restore.
# A stash pop that conflicts leaves BOTH the markers in the worktree AND the
# stash on the list. Stashing that state again next session compounds it:
# observed 2026-08-07, three orphan stashes and one card's frontmatter
# corrupted twice. So never stash a tree that still carries markers.
#
# DETECTION IS A CONTENT GREP, NOT `diff --check`, and that is the whole point
# (fixed 2026-08-15 after the guard failed and 17 stashes had accumulated).
# `git diff --check` compares the WORKTREE TO THE INDEX, so it is blind to
# exactly the two states this guard exists to catch: a failed `stash pop`
# STAGES its conflicted result, and four separate passes have COMMITTED
# markers outright. In both cases worktree and index agree, `diff --check`
# says nothing, and the hook cheerfully re-stashed a corrupted tree. `git
# grep` reads file CONTENT and finds a marker wherever it sits. Verified on
# a real repo the day it was written: zero false positives, and prose that
# merely discusses markers does not match because the pattern is anchored.
$markers = $false
if ($hasOrigin -and $syncBranches) {
    # No stderr redirection on purpose: in Windows PowerShell 5.1 redirecting a
    # native exe's stderr wraps each line in a NativeCommandError.
    git -C $repo grep -l -I -E '^<<<<<<< |^>>>>>>> ' *> $null
    $markers = ($LASTEXITCODE -eq 0)
}
if ($markers) {
    Write-Output "[workbench] CONFLICT MARKERS in $repo, staged or committed or both. Convergence SKIPPED this pass ON PURPOSE: re-stashing a marker-corrupted tree is how orphan stashes accumulate. Name the files with git -C `"$repo`" grep -l -E '^<<<<<<< ', fix them, commit, then start a fresh session to merge."
}
elseif ($hasOrigin -and $syncBranches) {
    # CONVERGENCE TAKES A LOCK, because several sessions start at once and every
    # one of them runs this hook against the SAME repo. Measured 2026-09-02 on
    # the Mac: three SessionStart rows landed inside one second, and one
    # pre-merge stash was left on the list with its content already back in the
    # worktree, which is exactly what an interleaved push/pop pair looks like.
    # `git stash pop` with no argument takes stash@{0}, so when two hooks stash
    # at once one of them pops the OTHER one's entry: the tree ends up correct,
    # and an entry is orphaned with nothing to show it happened. That is also
    # the shape of the seventeen stashes that had piled up by 2026-08-15, and
    # the reason a person had to inspect and drop one by hand (golden rule A3:
    # a guard whose failure mode needs a person is not automation).
    #
    # The lock is a directory create, which fails atomically when it already
    # exists, and it lives under .git so it can never dirty the worktree,
    # never be committed, and never be stashed.
    # The loop is bounded by its own condition and the catch block contains
    # neither `continue` nor `break`, deliberately: both behave differently
    # inside a catch than inside a plain loop body in PowerShell, and this file
    # runs before every PC session, so a control-flow surprise here would stop
    # that machine converging silently. Bounding the count instead needs no
    # such knowledge to read.
    $lock = Join-Path $repo '.git\session-start-sync.lock'
    $held = $false
    $waited = 0
    while ((-not $held) -and ($waited -lt 20)) {
        try {
            [void](New-Item -ItemType Directory -Path $lock -ErrorAction Stop)
            $held = $true
        } catch {
            $waited++
            # Nothing under this lock should ever take a minute, so a lock
            # older than that belonged to a session that died. Break it rather
            # than skipping convergence on this machine forever.
            $stale = $false
            if (Test-Path $lock) {
                $stale = (((Get-Date) - (Get-Item $lock).LastWriteTime).TotalSeconds -gt 120)
            }
            if ($stale) {
                Remove-Item -Recurse -Force $lock -ErrorAction SilentlyContinue
            } else {
                Start-Sleep -Seconds 1
            }
        }
    }
    if (-not $held) {
        Write-Output "[workbench] another session is converging $repo right now, so this pass skipped the merge on purpose rather than racing it. Nothing is lost: whoever holds the lock is doing the same merge."
    }
}
if ($held) {
    # Re-read the tree under the lock: whoever held it may have merged,
    # committed, or restored a stash since $dirty was measured above.
    $dirty = (git -C $repo status --porcelain) -join "`n"
    $stashed = $false
    $stashSha = ''
    if ($dirty) {
        git -C $repo stash push --include-untracked --quiet -m 'session-start-sync: pre-merge autostash' *> $null
        $stashed = ($LASTEXITCODE -eq 0)
        if ($stashed) {
            $stashSha = (git -C $repo rev-parse -q --verify 'stash@{0}')
            Write-Output '[workbench] stashed local changes to merge; restored below.'
        }
    }
    foreach ($b in ($syncBranches -split '[, ]+' | Where-Object { $_ })) {
        $other = "origin/$b"
        git -C $repo rev-parse --verify --quiet $other *> $null
        if ($LASTEXITCODE -ne 0) { continue }
        $behind = [int](git -C $repo rev-list --count "HEAD..$other")
        if ($behind -eq 0) { continue }
        git -C $repo merge --no-edit $other *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Output "[workbench] merged $behind commit(s) from $other."
        } else {
            Write-Output "[workbench] MERGE CONFLICT with $other. Resolve it in $repo before any other work: keep both sides' facts, dedupe MEMORY.md, commit, push."
        }
    }
    if ($stashed) {
        # Pop OUR entry, never "whatever is on top". The lock should make those
        # the same thing; assert it instead of trusting it, because popping
        # another session's stash SUCCEEDS and leaves no trace, which is why
        # this went unnoticed for weeks.
        $topSha = (git -C $repo rev-parse -q --verify 'stash@{0}')
        if ($stashSha -and $topSha -ne $stashSha) {
            Write-Output "[workbench] NOT popping: the top stash is not the one this hook pushed, so something is stashing without the lock. Both were left alone. Inspect: git -C `"$repo`" stash list"
        } else {
            git -C $repo stash pop *> $null
            # Do NOT infer the pop failed from its exit code. Observed 2026-08-15:
            # this fired with an EMPTY file list ("CONFLICT MARKERS in  ,") against
            # a clean tree and an empty stash list, because the pop had fully
            # applied and dropped. A false alarm that says the repo is corrupt
            # costs the same trust as a missed real one. Assert on CONTENT.
            $conflicted = @(git -C $repo diff --name-only --diff-filter=U) -join ', '
            if ($conflicted) {
                Write-Output "[workbench] STASH DID NOT REAPPLY. The worktree now has CONFLICT MARKERS in: $conflicted. Fix those files by hand FIRST (do not pop again, it will re-conflict), then commit."
            }
            elseif ($stashSha -and (git -C $repo rev-parse -q --verify 'stash@{0}') -eq $stashSha) {
                # A clean pop drops its own entry. One still sitting there means
                # the pop refused for a reason that produced no conflict, so the
                # content may NOT have landed: report it, never drop it blind.
                Write-Output "[workbench] the pre-merge stash is STILL on the list after a pop that reported no conflict, so its content may not have landed. Check it with git -C `"$repo`" stash show -p and drop it only if it did."
            }
        }
    }
}
# Release it whether or not anything above succeeded. A lock left behind would
# make every later session skip convergence for two minutes, which is the same
# silent staleness this whole section exists to prevent.
if ($held) { Remove-Item -Recurse -Force $lock -ErrorAction SilentlyContinue }

# 4b. Orphan stashes. A failed pop leaves its stash on the list and nothing
# has ever swept them: SEVENTEEN had piled up by 2026-08-15, each one a
# convergence that silently half-failed. The count is the only signal that
# ever existed, so print it.
if ($hasOrigin) {
    $stashCount = @(git -C $repo stash list).Count
    if ($stashCount -gt 0) {
        Write-Output "[workbench] $stashCount leftover stash(es) in $repo from failed pops. Inspect the newest with git -C `"$repo`" stash show -p and drop what already landed: git -C `"$repo`" stash drop"
    }
}

# 4c. IS THE AUTOMATION SERVICE UP? Full reasoning in the .sh counterpart. The
# short version: golden rule 1 tells every session to call the session-checklist
# endpoint instead of re-deriving its opening order, and STEP 1 of what that
# endpoint returns is "start the automation service". The checklist is served BY
# that service, so when it is down the instruction to check it cannot be
# delivered. On 2026-09-02 it had been down long enough for all fourteen
# workflows, the driver-end ping, the queue tick and the farm app's staging
# containers to be dead, with nothing reporting it. The probe has to live in a
# hook, which depends on nothing.
$n8nUrl = $confValues['N8N_URL']
if (-not $n8nUrl) { $n8nUrl = 'http://127.0.0.1:5678' }
try {
    $resp = Invoke-WebRequest -Uri "$n8nUrl/webhook/session-checklist" `
            -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop
    Write-Output "[workbench] automation service answering at $n8nUrl (HTTP $($resp.StatusCode))."
} catch {
    $code = 000
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    if ($code -eq 404) {
        Write-Output "[workbench] automation service answering at $n8nUrl (HTTP 404, so it is up but that workflow is missing)."
    } else {
        Write-Output "[workbench] AUTOMATION SERVICE NOT ANSWERING at $n8nUrl (HTTP $code). Every workflow is dead while this is down, including the driver-end ping and the queue tick, and the cheap tier is unavailable so work will silently route to an expensive one. Fix: start Docker Desktop, then check docker ps."
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
