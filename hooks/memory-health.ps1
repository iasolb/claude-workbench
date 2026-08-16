# SessionStart hook: memory bloat check. Silent when everything is
# within budget (no tokens wasted on good news); prints [memory-health]
# warnings with numbers when something needs a consolidation or trim pass.
# This is the trigger for efficiency passes: act on it when it fires.
# Windows counterpart of memory-health.sh. Always exits 0.

$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME ".claude\workbench.conf" }
if (-not (Test-Path $conf)) { exit 0 }

$repo = $null
foreach ($line in (Get-Content $conf)) {
    if ($line -match '^REPO_PATH=(.+)$') { $repo = $Matches[1].Trim(); break }
}
if (-not $repo -or -not (Test-Path $repo)) { exit 0 }

# Budgets. Rationale: MEMORY.md and queue/current.md are printed or loaded
# into EVERY session, so their budgets are tight; individual memory files
# are loaded on demand, so only outliers matter. Tune these to your repo.
$MemoryMdMaxLines = 45
$CurrentMdMaxLines = 150
$FileMaxKB = 10
# Set the directory budget to something a real cleanup pass can actually
# reach. A budget nothing can meet stops being a signal, and the per-FILE
# limit above is the earlier and more useful warning anyway.
$TotalMaxKB = 185

$script:warned = $false
function Say($msg) {
    if (-not $script:warned) {
        Write-Output "[memory-health] over budget, run a consolidation/trim pass:"
        $script:warned = $true
    }
    Write-Output "[memory-health]   $msg"
}

$mem = Join-Path $repo 'memory'
$memoryMd = Join-Path $mem 'MEMORY.md'
if (Test-Path $memoryMd) {
    $n = (Get-Content $memoryMd).Count
    if ($n -gt $MemoryMdMaxLines) { Say "MEMORY.md is $n lines (budget $MemoryMdMaxLines): merge or drop index entries" }
}

$cur = Join-Path $repo 'queue\current.md'
if (Test-Path $cur) {
    $n = (Get-Content $cur).Count
    if ($n -gt $CurrentMdMaxLines) { Say "queue/current.md is $n lines (budget $CurrentMdMaxLines): archive finished-arc detail into memory/ and cut it back to live state" }
}

if (Test-Path $mem) {
    $files = Get-ChildItem $mem -Filter '*.md' -File
    $totalKB = [math]::Ceiling((($files | Measure-Object Length -Sum).Sum) / 1KB)
    if ($totalKB -gt $TotalMaxKB) { Say "memory/ totals ${totalKB}KB (budget ${TotalMaxKB}KB)" }
    foreach ($f in $files) {
        if ($f.Name -eq 'MEMORY.md') { continue }
        $kb = [math]::Ceiling($f.Length / 1KB)
        if ($kb -gt $FileMaxKB) { Say "$($f.Name) is ${kb}KB (budget ${FileMaxKB}KB): split or condense" }
    }
}

exit 0
