# Symlinks this repo's tracked config into ~/.claude and writes
# ~/.claude/workbench.conf so the hooks know where the repo lives.
# Run from anywhere; resolves paths relative to this script's location.
# Existing targets are backed up with a .bak suffix before being replaced,
# never silently overwritten.
#
# Usage:
#   install\windows.ps1              single-machine setup (no branch sync)
#   install\windows.ps1 -Sync mac    multi-machine: merge origin/mac at
#                                    session start (comma-separate multiple)
param(
    [string]$Sync = ""
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$claudeDir = Join-Path $env:USERPROFILE ".claude"

# Links are created with cmd's mklink, not New-Item: mklink passes the flag
# that lets Developer Mode create symlinks unelevated, while Windows
# PowerShell 5.1's New-Item never does and demands elevation even with
# Developer Mode on.
function New-Symlink($target, $source) {
    if (Test-Path $source -PathType Container) {
        cmd /c mklink /D $target $source | Out-Null
    } else {
        cmd /c mklink $target $source | Out-Null
    }
    if ($LASTEXITCODE -ne 0) {
        throw "mklink exited with code $LASTEXITCODE"
    }
}

# Removing a directory link with Remove-Item triggers a confirmation prompt
# in PowerShell 5.1; rmdir removes the link itself without prompting and can
# never recurse into the link's target.
function Remove-Link($path) {
    if ((Get-Item $path -Force).PSIsContainer) {
        cmd /c rmdir $path | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "rmdir exited with code $LASTEXITCODE"
        }
    } else {
        Remove-Item $path -Force -ErrorAction Stop
    }
}

# Preflight: symlink creation needs Developer Mode or admin rights. Test in
# a throwaway location BEFORE touching any real target, so a run with
# insufficient privilege fails loudly up front instead of removing existing
# config and then failing to replace it.
$preflightTarget = Join-Path $env:TEMP "claude-workbench-symlink-test-$(Get-Random)"
try {
    New-Symlink $preflightTarget $repoRoot
    Remove-Link $preflightTarget
} catch {
    Write-Host "Cannot create symlinks: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Nothing has been touched. Either:" -ForegroundColor Red
    Write-Host "  - Enable Developer Mode (Settings > Privacy & Security > For Developers), then re-run normally, or" -ForegroundColor Red
    Write-Host "  - Re-run this script from an elevated (Run as Administrator) PowerShell." -ForegroundColor Red
    exit 1
}

$items = @("CLAUDE.md", "settings.json", "commands", "agents", "rules", "hooks")

function Replace-WithSymlink($target, $source, $label) {
    if (Test-Path $target) {
        $isLink = [bool](Get-Item $target -Force).LinkType
        if ($isLink) {
            try {
                Remove-Link $target
            } catch {
                Write-Host "FAILED to remove existing link at $label : $($_.Exception.Message)" -ForegroundColor Red
                return
            }
        } else {
            Move-Item $target "$target.bak" -Force
            Write-Host "Backed up existing $label to $label.bak"
        }
    }

    try {
        New-Symlink $target $source
        Write-Host "Linked $label"
    } catch {
        Write-Host "FAILED to link $label : $($_.Exception.Message)" -ForegroundColor Red
    }
}

foreach ($item in $items) {
    $source = Join-Path $repoRoot $item
    $target = Join-Path $claudeDir $item
    Replace-WithSymlink $target $source $item
}

# Cross-machine session memory: link the current project's memory dir to the
# repo's memory/ so memories written on any machine sync through git.
# Claude Code derives the project key from the session's actual working
# directory by swapping path separators (and the drive colon) for dashes
# (C:\Users\You -> C--Users-You, bare C:\ -> C--). That's NOT always the home
# dir: a session launched with cwd C:\ gets project key "C--", not
# "C--Users-You". So run this script from whatever directory you actually
# launch `claude` from on this machine, and re-run it if that changes.
$memorySource = Join-Path $repoRoot "memory"
$projectKey = (Get-Location).Path -replace '[:\\/]', '-'
$memoryTarget = Join-Path $claudeDir "projects\$projectKey\memory"

New-Item -ItemType Directory -Path (Split-Path -Parent $memoryTarget) -Force | Out-Null
Replace-WithSymlink $memoryTarget $memorySource "memory"

# Record where the repo lives and how this machine syncs, so the hooks never
# need hard-coded paths. If the repo ever moves, re-run this installer.
$machineBranch = ""
try {
    $machineBranch = (git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null)
} catch {}
$confPath = Join-Path $claudeDir "workbench.conf"
$confLines = @(
    "REPO_PATH=$repoRoot",
    "MACHINE_BRANCH=$machineBranch",
    "SYNC_BRANCHES=$Sync"
)
# WriteAllLines writes UTF-8 without a BOM, so non-PowerShell readers of the
# conf never see a stray byte-order mark on the first key.
[System.IO.File]::WriteAllLines($confPath, $confLines)
$syncLabel = if ($Sync) { $Sync } else { "none" }
$branchLabel = if ($machineBranch) { $machineBranch } else { "unknown" }
Write-Host "Wrote workbench.conf (branch: $branchLabel, sync: $syncLabel)"

# Ensure the TOON CLI is available globally for hooks that read/write .toon files.
# Idempotent: npm install -g is a no-op when already installed at the current version.
$toonCheck = npm list -g @toon-format/cli 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing @toon-format/cli globally..." -ForegroundColor Cyan
    npm install -g @toon-format/cli 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Installed @toon-format/cli"
    } else {
        Write-Host "WARNING: failed to install @toon-format/cli. TOON hooks will not work." -ForegroundColor Yellow
    }
} else {
    Write-Host "@toon-format/cli already installed"
}
