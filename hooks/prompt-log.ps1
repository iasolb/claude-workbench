# Notification hook, Windows side (prompt-log.sh is the macOS counterpart,
# same file format and contract). Logs every PERMISSION PROMPT this
# environment hits, so recurring prompt loops become visible instead of
# costing the same click night after night.
#
# LOGS ONLY. It never answers, suppresses, pre-approves, or influences a
# prompt. A hook that answers prompts is how an autonomous setup gets its
# whole fleet flagged; logging, reporting and narrowing the allowlist by hand
# afterwards is the supported shape.
#
# Storage: <repo>\prompts\<environment>.tsv, one file per environment.
# Condensed to UNIQUE commands: a repeat bumps count and last_seen.
# Columns: count, first_seen, last_seen, disposition, tool, command
# `disposition` starts as `?` and the SESSION fills it in (once / always /
# denied) at wrap-up, because a hook fires BEFORE the answer exists and
# cannot see which button was pressed. Contract: rules/claude/permission-loops.md.
#
# Always exits 0.

$ErrorActionPreference = 'SilentlyContinue'

try {
    $raw = [Console]::In.ReadToEnd()
    if (-not $raw) { exit 0 }
    $payload = $raw | ConvertFrom-Json

    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
    if (-not (Test-Path $conf)) { exit 0 }
    $lines = Get-Content -LiteralPath $conf
    $repo = ($lines | Where-Object { $_ -match '^REPO_PATH=' } | Select-Object -First 1) -replace '^REPO_PATH=', ''
    $repo = $repo.Trim()
    if (-not $repo -or -not (Test-Path (Join-Path $repo '.git'))) { exit 0 }
    $envName = ($lines | Where-Object { $_ -match '^MACHINE_BRANCH=' } | Select-Object -First 1) -replace '^MACHINE_BRANCH=', ''
    $envName = $envName.Trim()
    if (-not $envName) { $envName = 'windows' }

    # TWO EVENTS FEED THIS LOG (2026-09-27). PermissionRequest carries the EXACT
    # tool_name and tool_input of the call that is about to prompt
    # (https://code.claude.com/docs/en/hooks), so its rows are marked `exact`.
    # Notification only says a prompt happened, so its tool is GUESSED from the
    # transcript's last tool_use, which credits a subagent's or a parallel call's
    # prompt to whatever the parent ran last: measured that day, Reads and Edits
    # inside allowed roots were logged as prompts. Still LOG-ONLY on both: this
    # prints nothing and exits 0, which the docs define as no decision.
    $tool = $null
    $cmd = $null
    $disposition = '?'
    if ($payload.hook_event_name -eq 'PermissionRequest') {
        $disposition = 'exact'
        $tool = [string]$payload.tool_name
        $ti = $payload.tool_input
        if ($ti) {
            $cmd = $ti.command
            if (-not $cmd) { $cmd = $ti.file_path }
            if (-not $cmd) { $cmd = $ti.path }
            if (-not $cmd) { $cmd = ($ti | ConvertTo-Json -Compress) }
        }
    } elseif ($payload.notification_type -ne 'permission_prompt') { exit 0 }

    # The Notification payload does NOT carry tool_name or tool_input (checked
    # against the hooks reference: the fields are session_id, transcript_path,
    # cwd, permission_mode, hook_event_name, notification_type). An earlier
    # version read .tool_name/.tool_input and so logged raw JSON at best, which
    # is why overnight prompts went unrecorded. The waiting call is the LAST
    # tool_use block in the transcript, so resolve it from there.
    $tp = $payload.transcript_path
    if ($disposition -ne 'exact' -and $tp -and (Test-Path -LiteralPath $tp)) {
        $lines = @(Get-Content -LiteralPath $tp -Tail 400)
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            $entry = $null
            try { $entry = $lines[$i] | ConvertFrom-Json } catch { continue }
            if (-not $entry) { continue }
            $uses = @($entry.message.content | Where-Object { $_.type -eq 'tool_use' })
            if ($uses.Count -gt 0) {
                $use = $uses[-1]
                $tool = $use.name
                $cmd = $use.input.command
                if (-not $cmd) { $cmd = $use.input.file_path }
                if (-not $cmd) { $cmd = ($use.input | ConvertTo-Json -Compress) }
                break
            }
        }
    }
    if (-not $tool) { $tool = 'unresolved' }
    if (-not $cmd) { $cmd = "no tool_use found in transcript ($($payload.cwd))" }
    # NEVER WRITE A SECRET INTO GIT (2026-10-11). This log is committed, and the
    # Mac's copy held a billing app's client secret verbatim because a command
    # set it inline. Redact BEFORE truncating, so a cut can never leave half a
    # value behind. Test: hooks/prompt-log.test.py.
    $cmd = "$cmd" -replace '(?i)((?:secret|token|password|passwd|api[_-]?key|credential)\w*\s*[=:]\s*["'']?)[^"''\s]+', '${1}[REDACTED]'
    $cmd = $cmd -replace 'gh[pousr]_[A-Za-z0-9]{16,}', '[REDACTED]'
    $cmd = $cmd -replace 'eyJ[\w-]{8,}\.[\w-]{8,}(\.[\w-]+)?', '[REDACTED]'
    $cmd = $cmd -replace '(?i)(bearer\s+)[\w.~+/=-]{8,}', '${1}[REDACTED]'
    if ($cmd.Length -gt 300) { $cmd = $cmd.Substring(0, 300) }

    $cmd  = (("$cmd"  -replace "[`t`r`n]", ' ') -replace ' {2,}', ' ').Trim()
    $tool = (("$tool" -replace "[`t`r`n]", ' ')).Trim()
    $now  = Get-Date -Format 'yyyy-MM-dd HH:mm'

    $dir = Join-Path $repo 'prompts'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $log = Join-Path $dir "$envName.tsv"

    $header = "count`tfirst_seen`tlast_seen`tdisposition`ttool`tcommand"
    if (-not (Test-Path $log)) { Set-Content -LiteralPath $log -Value $header -Encoding UTF8 }

    $rows = @(Get-Content -LiteralPath $log | Select-Object -Skip 1 | Where-Object { $_ })
    $out = New-Object System.Collections.Generic.List[string]
    $found = $false
    foreach ($row in $rows) {
        $f = $row -split "`t"
        if ($f.Count -ge 6 -and $f[3] -eq $disposition -and $f[4] -eq $tool -and $f[5] -eq $cmd) {
            $found = $true
            $count = [int]$f[0] + 1
            $out.Add(($count, $f[1], $now, $f[3], $f[4], $f[5]) -join "`t")
        } else {
            $out.Add($row)
        }
    }
    if (-not $found) { $out.Add((1, $now, $now, $disposition, $tool, $cmd) -join "`t") }

    Set-Content -LiteralPath $log -Value (@($header) + $out) -Encoding UTF8
} catch { }

exit 0
