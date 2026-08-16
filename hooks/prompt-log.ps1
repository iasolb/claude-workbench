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
# cannot see which button was pressed. Contract: rules/permission-loops.md.
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

    if ($payload.notification_type -ne 'permission_prompt') { exit 0 }

    # The Notification payload does NOT carry tool_name or tool_input (checked
    # against the hooks reference: the fields are session_id, transcript_path,
    # cwd, permission_mode, hook_event_name, notification_type). An earlier
    # version read .tool_name/.tool_input and so logged raw JSON at best, which
    # is why overnight prompts went unrecorded. The waiting call is the LAST
    # tool_use block in the transcript, so resolve it from there.
    $tool = $null
    $cmd = $null
    $tp = $payload.transcript_path
    if ($tp -and (Test-Path -LiteralPath $tp)) {
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
        if ($f.Count -ge 6 -and $f[4] -eq $tool -and $f[5] -eq $cmd) {
            $found = $true
            $count = [int]$f[0] + 1
            $out.Add(($count, $f[1], $now, $f[3], $f[4], $f[5]) -join "`t")
        } else {
            $out.Add($row)
        }
    }
    if (-not $found) { $out.Add((1, $now, $now, '?', $tool, $cmd) -join "`t") }

    Set-Content -LiteralPath $log -Value (@($header) + $out) -Encoding UTF8
} catch { }

exit 0
