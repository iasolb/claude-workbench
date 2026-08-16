# Stop hook: nudge when an app's code changed and no test changed with it.
# Scope is DECLARED here and never inferred at runtime: ONE repo, code is
# app/ and templates/, tests are tests/. Read-only, git plumbing only, it
# edits nothing.
#
# OPT-IN. Set $DeclaredRepo below to the absolute path of the ONE repo this
# machine should watch; while it is empty the hook does nothing at all.
#
# Nudges by exiting 2 (stderr goes back to Claude as feedback), the same way
# queue-nudge and job-email-nudge do, and only when the set of changed code
# files DIFFERS from the last nudge in this session: that reports every new
# change without nagging about one it already reported. Windows counterpart of
# test-nudge.sh (each side self-selects by platform).
#
# TEST_NUDGE_REPO overrides the watched repo for the case matrix, the same way
# WORKBENCH_CONF works for queue-nudge. It cannot widen what this hook DOES:
# every path below is read-only.

$DeclaredRepo = ''

$repo = if ($env:TEST_NUDGE_REPO) { $env:TEST_NUDGE_REPO } else { $DeclaredRepo }
if (-not $repo) { exit 0 }

$raw = [Console]::In.ReadToEnd()
try { $payload = $raw | ConvertFrom-Json } catch { exit 0 }

# Built-in loop guard: if a stop hook already forced a continuation this turn,
# never block again.
if ($payload.stop_hook_active) { exit 0 }

if (-not (Test-Path (Join-Path $repo '.git'))) { exit 0 }

# Uncommitted first (tracked edits, staged or not, plus new files). If the tree
# is clean, fall back to the last commit, so a session that already committed
# still gets the nudge.
$changed = @()
$changed += (git -C $repo diff --name-only HEAD 2>$null)
$changed += (git -C $repo ls-files --others --exclude-standard 2>$null)
$changed = $changed | Where-Object { $_ }
if (-not $changed) {
    git -C $repo rev-parse --verify HEAD~1 *>$null
    if ($LASTEXITCODE -ne 0) { exit 0 }
    $changed = @(git -C $repo diff --name-only HEAD~1 HEAD 2>$null) | Where-Object { $_ }
}
if (-not $changed) { exit 0 }

$code = $changed | Where-Object { $_ -match '^(app|templates)/' } | Sort-Object -Unique
if (-not $code) { exit 0 }
if ($changed | Where-Object { $_ -match '^tests/' }) { exit 0 }

$sessionId = "$($payload.session_id)"
if (-not $sessionId) { $sessionId = 'nosession' }
# The mirror name is in the marker deliberately: on Windows, Git Bash's /tmp IS
# %TEMP%, so a shared marker would let whichever mirror ran first silence the
# other. Only one mirror runs per platform in production, but the case matrix
# runs both, and a test that silences half of what it is testing reports a
# green it did not earn.
$marker = Join-Path ([System.IO.Path]::GetTempPath()) "test-nudge-ps-$sessionId"
$codeText = ($code -join "`n")
if ((Test-Path $marker) -and ((Get-Content $marker -Raw -ErrorAction SilentlyContinue) -eq $codeText)) { exit 0 }
Set-Content -Path $marker -Value $codeText -NoNewline -Encoding utf8

$count = @($code).Count
[Console]::Error.WriteLine("$count file(s) under app/ or templates/ changed with nothing under tests/. Add a test for the behaviour that changed, or state in the card Report why this change does not need one. Declared scope, so this is not a guess about other repos: hooks/test-nudge.ps1 watches one checkout and those two directories.")
exit 2
