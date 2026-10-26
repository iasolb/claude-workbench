# PermissionRequest/Notification hook: record prompt details without changing
# the prompt decision. WORKBENCH_CONF defaults to ~/.claude/workbench.conf;
# its REPO_PATH and optional MACHINE_BRANCH select prompts/<branch>.tsv.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = [Console]::In.ReadToEnd()
    if (-not $raw) { exit 0 }
    $payload = $raw | ConvertFrom-Json
    $conf = if ($env:WORKBENCH_CONF) { $env:WORKBENCH_CONF } else { Join-Path $HOME '.claude\workbench.conf' }
    if (-not (Test-Path -LiteralPath $conf)) { exit 0 }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $conf) {
        $idx = $line.IndexOf('=')
        if ($idx -gt 0) { $values[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim() }
    }
    $repo = $values['REPO_PATH']
    if (-not $repo -or -not (Test-Path -LiteralPath $repo)) { exit 0 }
    $branch = if ($values['MACHINE_BRANCH']) { $values['MACHINE_BRANCH'] } else { 'local' }
    $tool = 'unresolved'; $command = ''; $disposition = '?'
    if ($payload.hook_event_name -eq 'PermissionRequest') {
        $disposition = 'exact'
        $tool = [string]$payload.tool_name
        $inputValue = $payload.tool_input
        $command = $inputValue.command
        if (-not $command) { $command = $inputValue.file_path }
        if (-not $command) { $command = $inputValue.path }
        if (-not $command) { $command = $inputValue | ConvertTo-Json -Compress }
    } elseif ($payload.notification_type -ne 'permission_prompt') { exit 0 }
    if ($disposition -ne 'exact' -and $payload.transcript_path -and (Test-Path -LiteralPath $payload.transcript_path)) {
        $transcriptLines = @(Get-Content -LiteralPath $payload.transcript_path -Tail 400)
        for ($i = $transcriptLines.Count - 1; $i -ge 0; $i--) {
            try { $entry = $transcriptLines[$i] | ConvertFrom-Json } catch { continue }
            $uses = @($entry.message.content | Where-Object { $_.type -eq 'tool_use' })
            if ($uses.Count -gt 0) {
                $last = $uses[-1]
                $tool = $last.name; $command = $last.input.command
                if (-not $command) { $command = $last.input.file_path }
                if (-not $command) { $command = $last.input | ConvertTo-Json -Compress }
                break
            }
        }
    }
    if (-not $command) { $command = "no matching tool call found ($($payload.cwd))" }
    $command = [string]$command
    $command = $command -replace '(?i)((?:secret|token|password|passwd|api[_-]?key|credential)\w*\s*[=:]\s*["'']?)[^"''\s]+', '${1}[REDACTED]'
    $command = $command -replace 'gh[pousr]_[A-Za-z0-9]{16,}', '[REDACTED]'
    $command = $command -replace 'eyJ[\w-]{8,}\.[\w-]{8,}(\.[\w-]+)?', '[REDACTED]'
    $command = $command -replace '(?i)(bearer\s+)[\w.~+/=-]{8,}', '${1}[REDACTED]'
    if ($command.Length -gt 300) { $command = $command.Substring(0, 300) }
    $command = (($command -replace "[`t`r`n]", ' ') -replace ' {2,}', ' ').Trim()
    $tool = (($tool -replace "[`t`r`n]", ' ')).Trim()
    $now = Get-Date -Format 'yyyy-MM-dd HH:mm'
    $dir = Join-Path $repo 'prompts'
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $log = Join-Path $dir "$branch.tsv"
    $header = "count`tfirst_seen`tlast_seen`tdisposition`ttool`tcommand"
    if (-not (Test-Path -LiteralPath $log)) { Set-Content -LiteralPath $log -Value $header -Encoding UTF8 }
    $rows = @(Get-Content -LiteralPath $log | Select-Object -Skip 1 | Where-Object { $_ })
    $out = New-Object System.Collections.Generic.List[string]
    $found = $false
    foreach ($row in $rows) {
        $fields = $row -split "`t"
        if ($fields.Count -ge 6 -and $fields[3] -eq $disposition -and $fields[4] -eq $tool -and $fields[5] -eq $command) {
            $found = $true; $count = [int]$fields[0] + 1
            $out.Add(($count, $fields[1], $now, $fields[3], $fields[4], $fields[5]) -join "`t")
        } else { $out.Add($row) }
    }
    if (-not $found) { $out.Add((1, $now, $now, $disposition, $tool, $command) -join "`t") }
    Set-Content -LiteralPath $log -Value (@($header) + $out) -Encoding UTF8
} catch { }
exit 0
