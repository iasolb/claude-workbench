# Stop hook: nudge when real work happened this session but queue/current.md
# never got touched, so the arc state gets summarized into the queue before
# the session wraps instead of going stale. Nudges by exiting 2 (stderr goes
# back to Claude as feedback) at most once per session; every other path
# exits 0 silently. Windows counterpart of queue-nudge.sh (invoked via
# powershell, which macOS/Linux lack, so each side self-selects by platform).

$raw = [Console]::In.ReadToEnd()
try { $payload = $raw | ConvertFrom-Json } catch { exit 0 }

# Built-in loop guard: if a stop hook already forced a continuation this
# turn, never block again.
if ($payload.stop_hook_active) { exit 0 }

# WORKBENCH_CONF overrides the conf location (mainly for testing).
$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }

$repo = $null
foreach ($line in (Get-Content $conf)) {
    if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim(); break }
}
if (-not $repo) { exit 0 }
$current = Join-Path $repo 'queue\current.md'
if (-not (Test-Path $current)) { exit 0 }

# Small sessions don't owe the queue an update.
$transcript = $payload.transcript_path
if (-not $transcript -or -not (Test-Path $transcript)) { exit 0 }
if ((Get-Item $transcript).Length -lt 20000) { exit 0 }

# Queue touched in the last 4 hours = someone is already keeping it current.
if ((Get-Item $current).LastWriteTime -gt (Get-Date).AddMinutes(-240)) { exit 0 }

# At most one nudge per session.
if (-not $payload.session_id) { exit 0 }
$marker = Join-Path $env:TEMP "queue-nudge-$($payload.session_id)"
if (Test-Path $marker) { exit 0 }
New-Item -ItemType File -Path $marker *> $null

[Console]::Error.WriteLine("queue/current.md has not been updated this session. Summarize where the current arc stands into it (and adjust queue/global.md if priorities moved or an item completed), then stop.")
exit 2
