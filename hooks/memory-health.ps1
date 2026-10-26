# SessionStart hook: warn when configured memory and status files exceed budgets.
# Defaults: MEMORY_DIR=memory, MEMORY_INDEX=memory/MEMORY.md,
# CURRENT_STATUS=queue/current.md. MANDATED_READS is a semicolon-separated list
# of repository-relative paths; set it to monitor additional always-read files.
$ErrorActionPreference = 'SilentlyContinue'
$conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
if (-not (Test-Path -LiteralPath $conf)) { exit 0 }
$repo = ((Get-Content -LiteralPath $conf | Where-Object { $_ -match '^REPO_PATH=' } | Select-Object -First 1) -replace '^REPO_PATH=', '').Trim()
if (-not $repo -or -not (Test-Path -LiteralPath $repo)) { exit 0 }
$memoryDir = if ($env:MEMORY_DIR) { Join-Path $repo $env:MEMORY_DIR } else { Join-Path $repo 'memory' }
$index = if ($env:MEMORY_INDEX) { Join-Path $repo $env:MEMORY_INDEX } else { Join-Path $memoryDir 'MEMORY.md' }
$current = if ($env:CURRENT_STATUS) { Join-Path $repo $env:CURRENT_STATUS } else { Join-Path $repo 'queue\current.md' }
$indexMax = if ($env:MEMORY_INDEX_MAX_LINES) { [int]$env:MEMORY_INDEX_MAX_LINES } else { 45 }
$currentMax = if ($env:CURRENT_STATUS_MAX_LINES) { [int]$env:CURRENT_STATUS_MAX_LINES } else { 150 }
$fileMax = if ($env:MEMORY_FILE_MAX_KB) { [int]$env:MEMORY_FILE_MAX_KB } else { 10 }
$totalMax = if ($env:MEMORY_TOTAL_MAX_KB) { [int]$env:MEMORY_TOTAL_MAX_KB } else { 185 }
$readMax = if ($env:MANDATED_READ_MAX_KB) { [int]$env:MANDATED_READ_MAX_KB } else { 65 }
$warned = $false
function Say([string]$Message) {
    if (-not $script:warned) { Write-Output '[memory-health] over budget, review and consolidate:'; $script:warned = $true }
    Write-Output "[memory-health]   $Message"
}
if (Test-Path -LiteralPath $index) {
    $n = (Get-Content -LiteralPath $index).Count
    if ($n -gt $indexMax) { Say "index has $n lines (budget $indexMax)" }
}
if (Test-Path -LiteralPath $current) {
    $n = (Get-Content -LiteralPath $current).Count
    if ($n -gt $currentMax) { Say "current status has $n lines (budget $currentMax)" }
}
if (Test-Path -LiteralPath $memoryDir) {
    $files = Get-ChildItem -LiteralPath $memoryDir -Filter '*.md' -File
    $total = [math]::Ceiling((($files | Measure-Object Length -Sum).Sum) / 1KB)
    if ($total -gt $totalMax) { Say "memory directory totals ${total}KB (budget ${totalMax}KB)" }
    foreach ($file in $files) {
        if ($file.FullName -eq $index) { continue }
        $kb = [math]::Ceiling($file.Length / 1KB)
        if ($kb -gt $fileMax) { Say "$($file.Name) is ${kb}KB (budget ${fileMax}KB)" }
    }
}
foreach ($relative in ($env:MANDATED_READS -split ';' | Where-Object { $_ })) {
    $path = Join-Path $repo $relative
    if ((Test-Path -LiteralPath $path) -and [math]::Ceiling((Get-Item $path).Length / 1KB) -gt $readMax) {
        Say "$relative exceeds the mandated-read budget of ${readMax}KB"
    }
}
exit 0
