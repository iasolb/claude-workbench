# SessionStart hook: announce pending/active job cards (queue/jobs/*.md)
# targeted at this machine, so a session picks them up without a treasure
# hunt. Windows counterpart of job-brief.sh (invoked via powershell, which
# macOS/Linux lack, so each side self-selects by platform). Prints [jobs]
# lines and always exits 0.

$Machine = if ($env:JOB_HOOK_MACHINE) { $env:JOB_HOOK_MACHINE } else { "pc" }

$Conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $Conf)) { exit 0 }
$Repo = (Get-Content $Conf | Where-Object { $_ -match '^REPO_PATH=' } | Select-Object -First 1) -replace '^REPO_PATH=', ''
if (-not $Repo) { exit 0 }
$JobsDir = Join-Path $Repo "queue\jobs"
if (-not (Test-Path $JobsDir)) { exit 0 }

function Get-Field($Text, $Name) {
    $m = [regex]::Match($Text, "(?m)^$($Name):\s*(\S+)")
    if ($m.Success) { $m.Groups[1].Value.ToLower() } else { "" }
}

Get-ChildItem -Path $JobsDir -Filter *.md | Where-Object { $_.Name -notlike '_*' } | ForEach-Object {
    $txt = Get-Content $_.FullName -Raw
    $status = Get-Field $txt "status"
    if ($status -ne "pending" -and $status -ne "active") { return }
    $mach = Get-Field $txt "machine"
    if ($mach -ne $Machine -and $mach -ne "any") { return }
    $mode = Get-Field $txt "mode"
    Write-Output "[jobs] $status job for this machine: queue/jobs/$($_.Name) (mode: $mode). Read the card and follow rules/claude/jobs.md: flip it active, stay in its workdir, honor the mode, run the test gate, report to the inbox. Work ONE card this session, then stop (schedule a fresh run for the next); don't roll into another job in this context."
}

exit 0
