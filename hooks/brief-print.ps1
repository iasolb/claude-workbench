# brief-print.ps1: gather once, print once, at session start.
#
# It fires the state gather, waits for it, folds in the host-side facts a
# container cannot see, and prints ONE artifact. The point is that a session
# stops rediscovering the machine: reading is most of what a session spends.
#
# FALLBACK IS THE WHOLE DESIGN. If the gather fails or the brief is stale, this
# says so loudly rather than printing nothing, because a session that starts
# blind while believing it is informed is worse than one that knows it is blind.
#
# Paths come from workbench.conf, never hardcoded: this file is published in a
# public repository, so an absolute path here would publish a username.
# `tools/brief.py` is part of the maintainer's private tooling and is not
# shipped here, so this hook no-ops cleanly when it is absent rather than
# printing an error at every session start.

$ErrorActionPreference = "Continue"

$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }

$repo = $null
foreach ($line in Get-Content $conf) {
    if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim(); break }
}
if (-not $repo) { exit 0 }

$brief = Join-Path $repo "tools\brief.py"
# The gathered artifact lands beside the repo, not inside it: it is machine
# state, not versioned content.
$out = Join-Path (Split-Path $repo -Parent) "state\brief.toon"

# Discover the interpreter rather than naming one.
$python = $null
foreach ($candidate in @("python3", "python")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python -or -not (Test-Path $brief)) { exit 0 }

# --no-cost by default: a spend canary costs a real model call, and a session
# start is not the moment to spend one.
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

$content = Get-Content -Path $out -Encoding utf8 -Raw
# The age check alone misses the case where the artifact was rewritten just now
# but its REMOTE half failed, which reads as fresh and is half blind.
if ($content -match "remote_gathered:\s*false") {
    Write-Output "[brief] REMOTE GATHER FAILED just now. Everything below is HOST-SIDE ONLY."
    Write-Output "[brief] Check that the automation service is up before trusting remote state."
}

Write-Output "[brief] machine + remote state, gathered on demand. Full file: $out"
Write-Output $content
