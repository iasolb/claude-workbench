# PreToolUse guard: after writing a reentry handoff, this session is finished.
# The POSIX companion is handoff-is-final.sh; keep both behaviors aligned.
#
# Scope:
#   reads   : the hook JSON payload; one marker; git branch and unpushed commits
#             in WORKBENCH_ROOT
#   writes  : one marker per session under state/handoff-sessions/
#   decides : deny an unpushed handoff or a state-changing call after handoff
#   network : none
#
# WORKBENCH_ROOT defaults to the parent of this hook's directory. Set it when
# the hook is installed outside the repository. The marker directory defaults
# to <WORKBENCH_ROOT>/state/handoff-sessions.
#
# Fail open on malformed input or an unexpected hook error. This guard only
# denies; it never grants access or suppresses an authorization prompt.

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload = $raw | ConvertFrom-Json
    $sessionId = ""
    if ($payload.session_id) { $sessionId = [string]$payload.session_id }
    if ([string]::IsNullOrWhiteSpace($sessionId)) { exit 0 }
    $safeId = $sessionId -replace '[^A-Za-z0-9_.-]', '_'
    $toolName = ""
    if ($payload.tool_name) { $toolName = [string]$payload.tool_name }
    $root = if ($env:WORKBENCH_ROOT) {
        $env:WORKBENCH_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    $stateRoot = if ($env:WORKBENCH_STATE_DIR) {
        $env:WORKBENCH_STATE_DIR
    } else {
        Join-Path $root "state"
    }
    $markerDir = Join-Path $stateRoot "handoff-sessions"
    $marker = Join-Path $markerDir ($safeId + ".done")

    $target = ""
    if ($payload.tool_input -and $payload.tool_input.file_path) {
        $target = [string]$payload.tool_input.file_path
    }
    $isHandoff = ($toolName -in @("Write", "Edit", "NotebookEdit")) -and
                 ($target -match '(?i)[\\/]reentry-[^\\/]*\.md$')

    if ($isHandoff) {
        $unpushed = ""
        try {
            $branch = (& git -C $root rev-parse --abbrev-ref HEAD 2>$null)
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($branch)) {
                $branch = $branch.Trim()
                $ahead = (& git -C $root log "origin/$branch..HEAD" --oneline 2>$null)
                if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($ahead)) {
                    $unpushed = (($ahead -split "`n") | Measure-Object).Count
                }
            }
        } catch { $unpushed = "" }

        if (-not [string]::IsNullOrWhiteSpace($unpushed)) {
            $msg = @(
                "BLOCKED: the handoff must be the final step, after preparation.",
                "$unpushed commit(s) on this branch are not pushed, so the next session would not have this work.",
                "Push the branch, then write the handoff again as the final action.",
                "Do not work around this by renaming the file."
            ) -join " "
            @{ hookSpecificOutput = @{ hookEventName = "PreToolUse"; permissionDecision = "deny"; permissionDecisionReason = $msg } } |
                ConvertTo-Json -Depth 4 -Compress
            exit 0
        }

        try {
            if (-not (Test-Path $markerDir)) {
                New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
            }
            Set-Content -Path $marker -Value $target -Encoding utf8
        } catch { }
        exit 0
    }

    if (-not (Test-Path $marker)) { exit 0 }
    if ($toolName -notin @("Write", "Edit", "NotebookEdit", "Bash", "PowerShell")) { exit 0 }

    $handedTo = ""
    try { $handedTo = (Get-Content -Path $marker -Raw -ErrorAction Stop).Trim() } catch { }
    $msg = @(
        "BLOCKED: this session already wrote its handoff and is finished.",
        "It wrote $handedTo. Further changes would make the handoff stale.",
        "Stop here and let the next session continue. Read-only tools remain available.",
        "To resume deliberately, remove this marker: $marker"
    ) -join " "
    @{ hookSpecificOutput = @{ hookEventName = "PreToolUse"; permissionDecision = "deny"; permissionDecisionReason = $msg } } |
        ConvertTo-Json -Depth 4 -Compress
    exit 0
} catch {
    exit 0
}
