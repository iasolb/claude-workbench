# SessionStart hook: print the navigation map (nav/map.md) into session
# context, shared section plus this machine's section only. PURPOSE: spend
# zero session tokens rediscovering paths, ssh targets, and conventions.
# Windows counterpart of nav-print.sh (each side self-selects by platform).
# Reads ~/.claude/workbench.conf for the repo path. Prints [nav] lines and
# always exits 0: a broken print must never block a session.

$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }

$repo = $null
foreach ($line in (Get-Content $conf)) {
    if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim(); break }
}
if (-not $repo -or -not (Test-Path $repo)) { exit 0 }

$map = Join-Path $repo 'nav\map.md'
# Opt-in like the queue: no map, no output.
if (-not (Test-Path $map)) { exit 0 }

$machine = 'windows'

Write-Output "[nav] map (shared + $machine; source: nav/map.md, edit there):"
$sect = ''
foreach ($line in (Get-Content $map)) {
    if ($line -match '^## (.+)$') { $sect = $Matches[1].Trim(); continue }
    if (($sect -eq 'shared' -or $sect -eq $machine) -and $line.Trim() -ne '') {
        Write-Output $line
    }
}

exit 0
