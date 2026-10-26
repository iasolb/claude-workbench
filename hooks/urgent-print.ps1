# SessionStart hook: print non-README Markdown files from urgent/ in full.
# WORKBENCH_CONF defaults to ~/.claude/workbench.conf and defines REPO_PATH;
# MACHINE_BRANCH optionally selects a remote branch to include.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
    if (-not (Test-Path -LiteralPath $conf)) { exit 0 }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $conf) {
        $idx = $line.IndexOf('=')
        if ($idx -gt 0) { $values[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim() }
    }
    $repo = $values['REPO_PATH']; $dir = Join-Path $repo 'urgent'
    if (-not $repo -or -not (Test-Path -LiteralPath $dir)) { exit 0 }
    $notes = [ordered]@{}
    foreach ($file in (Get-ChildItem -LiteralPath $dir -Filter '*.md' -File | Where-Object { $_.Name -ne 'README.md' })) {
        $notes[$file.Name] = @(Get-Content -LiteralPath $file.FullName)
    }
    $branch = $values['MACHINE_BRANCH']
    if (-not $branch) { $branch = git -C $repo rev-parse --abbrev-ref HEAD 2>$null }
    if ($branch) {
        foreach ($entry in (git -C $repo ls-tree --name-only "origin/$branch" urgent/ 2>$null)) {
            $name = Split-Path $entry -Leaf
            if ($name -eq 'README.md' -or $name -notlike '*.md' -or $notes.Contains($name)) { continue }
            $body = @(git -C $repo show "origin/${branch}:urgent/$name" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $body.Count -gt 0) { $notes[$name] = $body }
        }
    }
    if ($notes.Count -eq 0) { exit 0 }
    Write-Output "[urgent] $($notes.Count) item(s) in urgent/. Read them before the ordinary task list. Fold each item into its permanent home and remove it in the same session."
    foreach ($name in @($notes.Keys | Sort-Object)) {
        Write-Output "[urgent] --- urgent/$name ---"
        foreach ($line in $notes[$name]) { Write-Output "[urgent] $line" }
    }
} catch { }
exit 0
