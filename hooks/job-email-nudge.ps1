# Stop hook: backstop for the job completion email, Windows counterpart of
# job-email-nudge.sh (each side self-selects by platform). If a job card for
# this machine is status: done but emailed: no/blank, nudge once so the
# session sends the job-done email per rules/jobs.md. The hook can't send
# mail itself; it only ensures the send isn't forgotten. Nudges by exiting 2
# at most once per session.

$raw = [Console]::In.ReadToEnd()
try { $payload = $raw | ConvertFrom-Json } catch { exit 0 }
if ($payload.stop_hook_active) { exit 0 }

$Machine = if ($env:JOB_HOOK_MACHINE) { $env:JOB_HOOK_MACHINE.ToLower() } else { "pc" }

$Conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $Conf)) { exit 0 }
$Repo = (Get-Content $Conf | Where-Object { $_ -match '^REPO_PATH=' } | Select-Object -First 1) -replace '^REPO_PATH=', ''
if (-not $Repo) { exit 0 }
$JobsDir = Join-Path $Repo "queue\jobs"
if (-not (Test-Path $JobsDir)) { exit 0 }

function Get-Field($Text, $Name) {
    $m = [regex]::Match($Text, "(?m)^$($Name):\s*(.+?)\s*$")
    if ($m.Success) { $m.Groups[1].Value.ToLower() } else { "" }
}

$pending = @()
Get-ChildItem -Path $JobsDir -Filter *.md | Where-Object { $_.Name -notlike '_*' } | ForEach-Object {
    $txt = Get-Content $_.FullName -Raw
    if ((Get-Field $txt "status") -ne "done") { return }
    $mach = Get-Field $txt "machine"
    if ($mach -ne $Machine -and $mach -ne "any") { return }
    $emailed = Get-Field $txt "emailed"
    if ($emailed -in @("", "no", "false", "pending")) { $pending += $_.Name }
}

if ($pending.Count -eq 0) { exit 0 }

$SessionId = "$($payload.session_id)"
if (-not $SessionId) { exit 0 }
$Marker = Join-Path ([System.IO.Path]::GetTempPath()) "job-email-nudge-$SessionId"
if (Test-Path $Marker) { exit 0 }
New-Item -ItemType File -Path $Marker -Force | Out-Null

$shown = $pending | Select-Object -First 5
$names = $shown -join ' '
if ($pending.Count -gt $shown.Count) { $names += " (and $($pending.Count - $shown.Count) more)" }

[Console]::Error.WriteLine("$($pending.Count) card(s) done but not stamped emailed: $names. IF THAT COUNT IS LARGE IT IS A BACKLOG, NOT A DEBT: one email covers a whole authorized lane, so a finished lane leaves every card but one reading 'emailed: no' forever. The fix for a backlog is to ARCHIVE those cards into queue/jobs/done/ (both hooks glob the top level only) with the lane's email date recorded ONCE in the inbox Done line, never to send one email per card. CONDITIONAL, read before acting: this hook selects on machine + status + emailed ONLY. It has no session-level signal, so it CANNOT tell which session worked a card and is not asserting that you owe this email. If THIS session worked the card, send the completion email per rules/jobs.md (to the card's notify: address, with the readthrough link), set emailed: to today, and push. If it did NOT, say so in one line and stop: never email a result you did not produce and cannot attest to (rules/memory-integrity.md rule 2). During an authorized overnight lane, ONE email covers the whole lane at the end, not one per card, so an unemailed card mid-lane is correct and needs nothing.")
exit 2
