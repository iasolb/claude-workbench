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

$python = "C:\Users\ians0\AppData\Local\Programs\Python\Python313\python.exe"
$brief  = "C:\Users\ians0\Documents\ai\ai-memory-bank\tools\brief.py"
$out    = "C:\Users\ians0\Documents\ai\state\brief.toon"

# --no-cost by default: the canary costs a real model call, and a session start
# is not the moment to spend one. Run tools/cost-guard.py before dispatching
# agent work instead, which is where the answer actually matters.
& $python $brief --no-cost --quiet | Out-Null

if (-not (Test-Path $out)) {
    Write-Output "[brief] GATHER FAILED and no previous brief exists."
    Write-Output "[brief] Start blind, or run: $python $brief"
    exit 0
}

$age = (New-TimeSpan -Start (Get-Item $out).LastWriteTime -End (Get-Date)).TotalMinutes
if ($age -gt 10) {
    Write-Output "[brief] STALE: this brief is $([math]::Round($age)) minutes old, so the gather did not run just now."
    Write-Output "[brief] Treat everything below as history, not state."
}

Write-Output "[brief] machine + remote state, gathered on demand. Full file: $out"
Get-Content -Path $out -Encoding utf8
