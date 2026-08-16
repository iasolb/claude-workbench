# SessionStart + SessionEnd hook: append one row per event to
# timelog/sessions.csv in the repo, as the raw material for tracking
# hours worked per project (added 2026-07-25, for contract hours).
# Attribution comes from cwd; hours come from pairing
# start/end rows by session_id at analysis time. Windows counterpart of
# time-log.sh (each side self-selects by platform). Reads
# ~/.claude/workbench.conf (WORKBENCH_CONF overrides, mainly for testing).
# Always exits 0: a broken log must never block a session.

try {
    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
    if (-not (Test-Path $conf)) { exit 0 }

    $repo = $null
    foreach ($line in (Get-Content $conf)) {
        if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim(); break }
    }
    if (-not $repo -or -not (Test-Path $repo)) { exit 0 }

    $payload = [Console]::In.ReadToEnd()
    $json = $null
    try { $json = $payload | ConvertFrom-Json } catch {}

    $event = if ($json -and $json.hook_event_name) { $json.hook_event_name } else { "unknown" }
    $source = if ($json -and $json.source) { $json.source } else { "" }
    $sid = if ($json -and $json.session_id) { $json.session_id } else { "" }
    $cwd = if ($json -and $json.cwd) { $json.cwd } else { (Get-Location).Path }
    # permission_mode is the effective mode this session is running in. Logged
    # so the mode is a committed fact instead of something a session infers
    # from settings.json (rules/claude/permission-loops.md). Blank means the payload
    # did not carry it, which is a statement about the payload, never about
    # the mode.
    $pmode = if ($json -and $json.permission_mode) { $json.permission_mode } else { "" }

    $dir = Join-Path $repo 'timelog'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $csv = Join-Path $dir 'sessions.csv'
    if (-not (Test-Path $csv)) {
        [System.IO.File]::AppendAllText($csv, "ts,event,source,machine,session_id,cwd,permission_mode`n")
    }

    $ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
    $row = '{0},{1},{2},{3},{4},"{5}",{6}' -f $ts, $event, $source, $env:COMPUTERNAME, $sid, ($cwd -replace '"', '""'), $pmode
    [System.IO.File]::AppendAllText($csv, "$row`n")
} catch {}
exit 0
