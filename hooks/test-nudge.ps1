# Stop hook: nudge when declared code paths changed without a matching test change.
# Opt in with TEST_NUDGE_REPO. TEST_NUDGE_CODE_DIRS and TEST_NUDGE_TEST_DIR
# configure repository-relative paths; defaults are app;templates and tests.
$repo = $env:TEST_NUDGE_REPO
if (-not $repo) { exit 0 }
try { $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json } catch { exit 0 }
if ($payload.stop_hook_active -or -not (Test-Path -LiteralPath (Join-Path $repo '.git'))) { exit 0 }
$codeDirs = if ($env:TEST_NUDGE_CODE_DIRS) { $env:TEST_NUDGE_CODE_DIRS -split ';' } else { @('app', 'templates') }
$testDir = if ($env:TEST_NUDGE_TEST_DIR) { $env:TEST_NUDGE_TEST_DIR } else { 'tests' }
$changed = @()
$changed += git -C $repo diff --name-only HEAD 2>$null
$changed += git -C $repo ls-files --others --exclude-standard 2>$null
$changed = @($changed | Where-Object { $_ })
if (-not $changed) {
    git -C $repo rev-parse --verify HEAD~1 *> $null
    if ($LASTEXITCODE -ne 0) { exit 0 }
    $changed = @(git -C $repo diff --name-only HEAD~1 HEAD 2>$null | Where-Object { $_ })
}
$code = @($changed | Where-Object { $path = $_; $codeDirs | Where-Object { $path -match ('^' + [regex]::Escape($_) + '/') } } | Sort-Object -Unique)
if (-not $code -or ($changed | Where-Object { $_ -match ('^' + [regex]::Escape($testDir) + '/') })) { exit 0 }
$session = if ($payload.session_id) { [string]$payload.session_id } else { 'nosession' }
$marker = Join-Path ([System.IO.Path]::GetTempPath()) "test-nudge-ps-$session"
$text = $code -join "`n"
if ((Test-Path -LiteralPath $marker) -and (Get-Content -LiteralPath $marker -Raw) -eq $text) { exit 0 }
Set-Content -LiteralPath $marker -Value $text -NoNewline -Encoding utf8
[Console]::Error.WriteLine("$($code.Count) code file(s) changed without a file under $testDir/. Add a relevant test or explain why none is needed.")
exit 2
