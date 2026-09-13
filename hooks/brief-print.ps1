# brief-print.ps1: gather once, print once, at session start.
#
# This is the trigger layer (docs/state-landing-zone.md). It fires the n8n
# gather, waits for it, folds in the host-side facts Docker cannot see, and
# prints ONE artifact. The point is that a session stops rediscovering the
# machine: reading is ~99% of what a session spends (rules/shared/cost.md).
#
# FALLBACK IS THE WHOLE DESIGN. Ian chose "replace most of it, keep a hard
# fallback" (2026-08-22). If the gather fails or the brief is stale, this says
# so loudly rather than printing nothing, because a session that starts blind
# while believing it is informed is worse than one that knows it is blind.

$ErrorActionPreference = "Continue"

# All three paths come from configuration, following the REPO_PATH pattern the
# other hooks use. The repo (from REPO_PATH) fixes where brief.py lives and
# where the state landing zone is (a sibling of the repo, outside any git
# tree); the interpreter (PYTHON_PATH) is machine-specific and never assumed.
$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }
$repo = $null
$python = $null
foreach ($line in (Get-Content $conf)) {
    if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim() }
    if ($line -match '^PYTHON_PATH=(.+)$') { $python = $Matches[1].Trim() }
}
if (-not $repo -or -not $python) { exit 0 }
$brief = Join-Path $repo 'tools\brief.py'
$out   = Join-Path (Split-Path $repo -Parent) 'state\brief.toon'

# BLOCKERS AND WHAT CHANGED, NOT THE WHOLE STATE (Ian, 2026-09-10, choosing
# between three shapes). This used to print the entire artifact, about 90 lines
# of absolute state, and the two most expensive facts of that evening were in
# none of it: a scheduled task had fired two drivers ten minutes earlier, and
# the executor allowance was fully spent. Absolute state also cannot say what
# is NEW, which is how a week of repeated work stayed invisible.
#
# The full artifact is still written and the headline points at it, so nothing
# is lost, it is just no longer the default read.
#
# --no-cost: the canary costs a real model call, and session start is not the
# moment to spend one. Run tools/cost-guard.py before dispatching agent work.
& $python $brief --no-cost --headline

if (-not (Test-Path $out)) {
    Write-Output "[brief] GATHER FAILED and no previous brief exists."
    Write-Output "[brief] Start blind, or run: $python $brief"
    exit 0
}

$age = (New-TimeSpan -Start (Get-Item $out).LastWriteTime -End (Get-Date)).TotalMinutes
if ($age -gt 10) {
    Write-Output "[brief] STALE: the artifact is $([math]::Round($age)) minutes old, so the gather above did not actually run."
    Write-Output "[brief] Treat the headline as history, not state."
}
