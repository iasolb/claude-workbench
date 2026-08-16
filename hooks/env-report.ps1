# SessionStart hook, Windows side: write this environment's actual state to
# state/<environment>.md so every other angle (a phone, a cloud session,
# the other machine) can see what config this machine is really running.
#
# Exists because hooks exit 0 on failure by design, so a dead hook and a quiet
# system look identical, and nothing otherwise reports which settings a machine
# actually loaded. This file reports OBSERVED state only. Where a fact cannot be
# established it says so rather than omitting it, because an omission reads as
# "fine".
#
# Runs LAST in SessionStart, after session-start-sync, so the git facts are
# post-merge. Reads only; the single write is its own state file, which it
# commits and pushes. Always exits 0.

$ErrorActionPreference = 'SilentlyContinue'

function Sha256($path) {
    if (-not (Test-Path -LiteralPath $path)) { return 'absent' }
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
}

try {
    $raw = [Console]::In.ReadToEnd()
    $payload = $null
    if ($raw) { $payload = $raw | ConvertFrom-Json }

    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
    if (-not (Test-Path $conf)) { exit 0 }
    $lines = Get-Content -LiteralPath $conf
    $repo = (($lines | Where-Object { $_ -match '^REPO_PATH=' } | Select-Object -First 1) -replace '^REPO_PATH=', '').Trim()
    if (-not $repo -or -not (Test-Path (Join-Path $repo '.git'))) { exit 0 }
    $envName = (($lines | Where-Object { $_ -match '^MACHINE_BRANCH=' } | Select-Object -First 1) -replace '^MACHINE_BRANCH=', '').Trim()
    if (-not $envName) { $envName = 'windows' }
    $syncBranches = (($lines | Where-Object { $_ -match '^SYNC_BRANCHES=' } | Select-Object -First 1) -replace '^SYNC_BRANCHES=', '').Trim()

    $sid = if ($payload) { $payload.session_id } else { '' }
    $pmode = if ($payload -and $payload.permission_mode) { $payload.permission_mode } else { '' }
    $pmodeLine = if ($pmode) { $pmode } else { 'BLANK, the SessionStart payload did not carry permission_mode. A fact about the payload, not about the mode.' }
    $cwd = if ($payload -and $payload.cwd) { $payload.cwd } else { (Get-Location).Path }

    # Git facts, post-merge.
    $branch = git -C $repo rev-parse --abbrev-ref HEAD
    $head = git -C $repo log -1 --format='%h %s'
    $dirty = @(git -C $repo status --porcelain).Count
    $unpushed = (git -C $repo rev-list --count '@{u}..HEAD')
    if (-not $unpushed) { $unpushed = 'no upstream' }
    $behind = New-Object System.Collections.Generic.List[string]
    foreach ($b in ($syncBranches -split '[, ]+' | Where-Object { $_ })) {
        git -C $repo rev-parse --verify --quiet "origin/$b" *> $null
        # ${b}, not $b, before a colon: PowerShell reads "$b:" as a
        # drive-qualified variable and the whole FILE fails to parse, which is
        # how this hook once produced nothing on any machine for three days
        # while looking perfectly registered.
        if ($LASTEXITCODE -ne 0) { $behind.Add("origin/${b}: not fetched"); continue }
        $n = git -C $repo rev-list --count "HEAD..origin/$b"
        $behind.Add("origin/$b" + ": $n" + $(if ([int]$n -gt 0) { ' NOT MERGED, session-start-sync did not converge' } else { ' merged' }))
    }

    # Settings actually in effect. The user file is a symlink to the repo copy
    # when install/windows.ps1 has run; if it is a real file, the repo copy is
    # NOT what this machine loaded.
    $userSettings = Join-Path $HOME '.claude\settings.json'
    $link = (Get-Item -LiteralPath $userSettings -Force).LinkType
    $target = (Get-Item -LiteralPath $userSettings -Force).Target
    $settingsState = if ($link) { "symlink -> $target" } else { 'REAL FILE, not the repo copy: edits to the repo do not reach this machine' }
    $defaultMode = 'not set'
    $allowCount = 'unknown'
    $deadRules = 'unknown'
    try {
        $s = Get-Content -LiteralPath $userSettings -Raw | ConvertFrom-Json
        if ($s.permissions.defaultMode) { $defaultMode = $s.permissions.defaultMode }
        $allow = @($s.permissions.allow)
        $allowCount = $allow.Count
        $deadRules = @($allow | Where-Object { $_ -match '^(Write|NotebookEdit|MultiEdit|Glob)\(' -or $_ -match '^(Edit|Read)\([A-Za-z]:' -or $_ -match '^(Edit|Read)\(.*\\' }).Count
    } catch { }

    # Local settings outrank user settings, so a defaultMode here wins.
    # $HOME\.claude is the USER-level local file and belongs in this list: it is
    # live for sessions started in $HOME and invisible to every other session,
    # so a machine can accumulate a large stored-approval file that nothing
    # ever reports.
    $localCandidates = @((Join-Path $HOME '.claude\settings.local.json'), (Join-Path $HOME 'claude\.claude\settings.local.json'), (Join-Path $cwd '.claude\settings.local.json'))
    $localLines = New-Object System.Collections.Generic.List[string]
    foreach ($lp in ($localCandidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $lp)) { $localLines.Add("- $lp : absent"); continue }
        $entries = 'unknown'; $lmode = 'not set'; $wild = 'unknown'; $star = 'unknown'
        try {
            $ls = Get-Content -LiteralPath $lp -Raw | ConvertFrom-Json
            $la = @($ls.permissions.allow)
            $entries = $la.Count
            # Two counts on purpose: ':*)' is the documented prefix wildcard, but
            # a bare '*' anywhere ("Bash(git push *)") is the shape that actually
            # accumulates from click-throughs, and counting only the first hid it.
            $wild = @($la | Where-Object { $_ -match ':\*\)$' }).Count
            $star = @($la | Where-Object { $_ -like '*`**' }).Count
            if ($ls.permissions.defaultMode) { $lmode = $ls.permissions.defaultMode }
        } catch { }
        $flag = if ($lmode -ne 'not set') { " OVERRIDES the shared defaultMode" } else { '' }
        $localLines.Add("- $lp : $entries allow entries, $wild prefix-wildcards, $star entries containing '*', defaultMode: $lmode$flag")
    }

    # Instruments: did the other machinery actually leave a trace this session?
    $timelog = Join-Path $repo 'timelog\sessions.csv'
    $timelogHit = 'no row for this session, time-log did not write'
    if ($sid -and (Test-Path $timelog)) {
        if (Select-String -LiteralPath $timelog -SimpleMatch $sid -Quiet) { $timelogHit = 'row present' }
    }
    $promptLog = Join-Path $repo "prompts\$envName.tsv"
    $promptState = if (Test-Path $promptLog) {
        $rows = @(Get-Content -LiteralPath $promptLog | Select-Object -Skip 1 | Where-Object { $_ }).Count
        "$rows rows, last written $((Get-Item $promptLog).LastWriteTime.ToString('yyyy-MM-dd HH:mm'))"
    } else { 'ABSENT, no permission prompt has ever been logged on this machine' }
    $urgentCount = @(Get-ChildItem (Join-Path $repo 'urgent') -Filter '*.md' | Where-Object { $_.Name -ne 'README.md' }).Count

    $now = Get-Date -Format 'yyyy-MM-dd HH:mm zzz'
    $out = @"
# $envName, state at last SessionStart

Written by ``hooks/env-report.ps1``. Observed facts only. Regenerated every
session start, so a stale timestamp here means this machine has not started a
session since then.

- when: $now
- session: $sid
- cwd: $cwd
- permission_mode: $pmodeLine

## Repo git, after the sync hook ran

- branch: $branch
- HEAD: $head
- dirty files: $dirty
- unpushed commits: $unpushed
$($behind | ForEach-Object { "- behind $_" } | Out-String)
## Settings this machine actually loaded

- ``~/.claude/settings.json``: $settingsState
- content fingerprint: $(Sha256 $userSettings)
- permissions.defaultMode: $defaultMode
- allow entries: $allowCount
- rules the product ignores (Write/NotebookEdit/MultiEdit/Glob paths, or Windows backslash paths): $deadRules

### Local settings, which OUTRANK the shared file

$($localLines | Out-String)
## Instruments

- timelog row for this session: $timelogHit
- prompts/$envName.tsv: $promptState
- urgent/ items waiting: $urgentCount
- NOT verifiable from any file: queue-print, urgent-print, job-brief and
  time-print only write to session context. Their firing can only be
  confirmed by someone reading the session output.
"@

    $dir = Join-Path $repo 'state'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    # Sessions run concurrently on one working tree by design, so two sessions
    # starting seconds apart both reach this line and the loser hits a sharing
    # violation: the whole hook then dies with an IOException and prints nothing
    # else, which reads like a broken instrument rather than two healthy
    # sessions racing. Retry briefly, then give up QUIETLY: a state file written
    # by the other session moments ago is just as current as ours.
    $statePath = Join-Path $dir "$envName.md"
    $wrote = $false
    foreach ($attempt in 1..4) {
        try {
            Set-Content -LiteralPath $statePath -Value $out -Encoding UTF8 -ErrorAction Stop
            $wrote = $true
            break
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds (150 * $attempt)
        }
    }
    if (-not $wrote) {
        Write-Output "[env-report] state/$envName.md is locked by a concurrent session; skipped this write (not an error)."
        return
    }

    # PATHSPEC form, and it matters: a bare `git commit` commits the whole
    # INDEX, so this hook would file whatever a live session had staged under
    # a message that says "state:". That is not hypothetical; it has happened
    # here, sweeping six unrelated files into one automatic commit.
    git -C $repo add "state/$envName.md" *> $null
    git -C $repo commit --quiet -m "state: $envName session start (auto, env-report)" -- "state/$envName.md" *> $null
    if ($LASTEXITCODE -eq 0) { git -C $repo push --quiet *> $null }
} catch { Write-Output "ENV-REPORT THREW: $($_.Exception.GetType().FullName): $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)" }

exit 0
