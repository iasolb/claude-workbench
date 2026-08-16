# SessionStart hook, Windows side (urgent-print.sh is the macOS counterpart,
# same behavior). Prints everything in urgent/ into session context, in full:
# the maintainer's fast lane from a phone, for new standing rules and
# organization jobs that must not wait for a session to go looking.
# Prints [urgent] lines and always exits 0: a broken printer must never block
# a session.

$ErrorActionPreference = 'SilentlyContinue'

try {
    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
    if (-not (Test-Path $conf)) { exit 0 }

    # Same key=value parse as session-start-sync.ps1: this hook needs
    # MACHINE_BRANCH as well as REPO_PATH.
    $confValues = @{}
    foreach ($line in (Get-Content $conf)) {
        $idx = $line.IndexOf('=')
        if ($idx -gt 0) {
            $confValues[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
        }
    }
    $repo = $confValues['REPO_PATH']
    if (-not $repo) { exit 0 }

    $dir = Join-Path $repo 'urgent'
    if (-not (Test-Path $dir)) { exit 0 }

    # Notes come from TWO independent sources, deduped by filename: the working
    # tree, and this machine's own remote branch. A session-start merge that
    # no-ops on a dirty tree leaves a pushed note absent from the tree, and a
    # tree-only glob then prints nothing, which swallows the fast lane silently:
    # a sync fix cannot deliver itself. The tree still wins for a note that
    # exists locally and has not been pushed yet.
    $notes = [ordered]@{}
    foreach ($f in @(Get-ChildItem -Path $dir -Filter *.md -File |
                     Where-Object { $_.Name -ne 'README.md' })) {
        $notes[$f.Name] = @(Get-Content -LiteralPath $f.FullName)
    }

    # Remote half. Every git call here can fail (no network, no remote, fresh
    # clone, detached head); each failure just leaves the working-tree list
    # standing, because a SessionStart hook that errors can block a session.
    $branch = $confValues['MACHINE_BRANCH']
    if (-not $branch) { $branch = (git -C $repo rev-parse --abbrev-ref HEAD 2>$null) }
    if ($branch) {
        foreach ($entry in @(git -C $repo ls-tree --name-only "origin/$branch" urgent/ 2>$null)) {
            $name = Split-Path $entry -Leaf
            if ($name -eq 'README.md' -or $name -notlike '*.md') { continue }
            if ($notes.Contains($name)) { continue }
            $body = @(git -C $repo show "origin/${branch}:urgent/$name" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $body.Count -gt 0) { $notes[$name] = $body }
        }
    }

    if ($notes.Count -eq 0) { exit 0 }

    Write-Output "[urgent] $($notes.Count) item(s) in urgent/. This is the fast lane from a phone: read them NOW, before the queue, and honor any rule they state. Clear an item by folding it into its permanent home (rules/, memory/, a job card) and deleting the file, in the same session you act on it."
    foreach ($name in @($notes.Keys | Sort-Object)) {
        Write-Output "[urgent] --- urgent/$name ---"
        foreach ($line in $notes[$name]) {
            Write-Output "[urgent] $line"
        }
    }
} catch { }

exit 0
