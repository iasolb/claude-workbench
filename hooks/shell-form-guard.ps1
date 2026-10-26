# PreToolUse guard for common shell forms that cannot be represented safely by
# command-prefix permission rules. Reads one hook JSON payload and emits only a
# deny decision; malformed input fails open.
# SHELL_FORM_ALLOWED_PYTHON_VERSION defaults to 313 for absolute-path checks.
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
    $command = [string]$payload.tool_input.command
    if ([string]::IsNullOrWhiteSpace($command)) { exit 0 }
    $tool = [string]$payload.tool_name
    $unquoted = [regex]::Replace($command, '"[^"]*"', '')
    $unquoted = [regex]::Replace($unquoted, "'[^']*'", '')
    $head = [regex]::Replace($command.Trim(), '^([A-Za-z_][A-Za-z0-9_]*=\S+\s+)+', '')
    $words = @($head -split '\s+' | Where-Object { $_ })
    $first = if ($words.Count) { $words[0] } else { '' }
    $bare = if ($first -notmatch '[/\\]') { ($first -replace '(?i)\.exe$', '').ToLower() } else { '' }
    $base = (($first -split '[/\\]')[-1] -replace '(?i)\.exe$', '').ToLower()
    $reasons = New-Object System.Collections.Generic.List[string]
    if ($command.Contains("`n")) { $reasons.Add('multiple command lines can bypass command-prefix rules') }
    if ($unquoted.Contains('&&') -or $unquoted.Contains('||')) { $reasons.Add('chained commands must be separate calls') }
    if ($unquoted.Contains(';')) { $reasons.Add('semicolon-separated commands must be separate calls') }
    if (@('python','python3','py','pytest','pip','pip3') -contains $bare -and $command -notmatch '-m\s+venv\b') { $reasons.Add('use a configured absolute interpreter or a named script') }
    if ($bare -eq 'npm' -and -not $command.Contains('--prefix')) { $reasons.Add('use npm with an explicit --prefix') }
    if ($command.Contains('\$')) { $reasons.Add('remove escaped dollar syntax or quote literal text correctly') }
    if ($command -match '\\[ \t]') { $reasons.Add('remove backslash-escaped whitespace') }
    $amp = $unquoted.Replace('&&','').Replace('>&','').Replace('&>','')
    if ($amp.Contains('&')) { $reasons.Add('do not use a shell call operator or background marker') }
    if (@('powershell','pwsh','cmd') -contains $bare) { $reasons.Add('invoke the inner command directly instead of a shell wrapper') }
    if (@('python','python3','py','node','ruby','perl','bash','sh','zsh','powershell','pwsh','cmd') -contains $base -and ($unquoted -cmatch '(^|\s)-c(\s|$)' -or $unquoted -match '(?i)(^|\s)-command(\s|$)')) { $reasons.Add('put inline code in a named script') }
    if ($tool -eq 'Bash' -and $unquoted -match '(?<![$\w])\{[^\s{}]*,[^\s{}]*\}') { $reasons.Add('list paths instead of using brace expansion') }
    if ($unquoted -match '--(format|pretty)=([%{]|\s|$)') { $reasons.Add('use a plain format option or separate its value with a space') }
    if ($unquoted -match '(^|\s)git(\s|$)' -and $unquoted -match '(^|\s)commit(\s|$)' -and $unquoted -notmatch '\s--(\s|$)' -and $unquoted -notmatch '--amend' -and $unquoted -notmatch '--no-edit') { $reasons.Add('add an explicit pathspec after git commit --') }
    if ($unquoted -match '(?i)(^|\s)opencode(\.exe)?\s+debug\s+config\b') { $reasons.Add('do not print resolved configuration that may contain secrets') }
    if ($first -match '(?i)[/\\]opencode(\.exe)?$') { $reasons.Add('invoke opencode by its configured command name') }
    $allowed = if ($env:SHELL_FORM_ALLOWED_PYTHON_VERSION) { $env:SHELL_FORM_ALLOWED_PYTHON_VERSION } else { '313' }
    if ($first -match '(?i)Programs[/\\]Python[/\\]Python(\d+)[/\\]python(\.exe)?$' -and $Matches[1] -ne $allowed) { $reasons.Add('configure the installed Python version or use a virtual environment') }
    if ($tool -eq 'PowerShell' -and $command -match '(?i)-Confirm:\$(true|false)\b') { $reasons.Add('omit the explicit confirmation flag') }
    if ($tool -eq 'PowerShell' -and $command.TrimStart().StartsWith('(')) { $reasons.Add('use a direct property-selection command') }
    if ($tool -eq 'PowerShell' -and $command -match '[A-Za-z]:[/\\][^\s,]*,\s*["'']?[A-Za-z]:[/\\]') { $reasons.Add('pass one path per call') }
    if ($tool -eq 'PowerShell' -and $bare -in @('curl','wget')) { $reasons.Add('use the HTTP client supported by this shell') }
    if ($tool -eq 'PowerShell' -and $first.StartsWith('[')) { $reasons.Add('use a named command or script instead of an inline type expression') }
    if ($tool -eq 'Bash' -and $first -eq 'bash' -and $words.Count -gt 1 -and -not $words[1].StartsWith('-')) { $reasons.Add('invoke the script directly; bash -n remains suitable for syntax checks') }
    if ($unquoted -match '\s+(\d?>>?|&>>?)\s*[^\s|;&<>]+') { $reasons.Add('capture output from the tool result instead of redirecting to a file') }
    if ($command -match '[A-Za-z]:\\\\') { $reasons.Add('use single path separators') }
    if ($unquoted.Contains('|') -and @('grep','rg','cat','head','tail','find','ls') -contains $bare) { $reasons.Add('use the dedicated file-search or file-read tool') }
    if ($unquoted -match '(?i)(^|\s)(sed|perl|ruby)\s+.*(--in-place|-[A-Za-z]*i(?:\s|\.))') { $reasons.Add('use a file-editing tool instead of an in-place stream edit') }
    if ($tool -eq 'PowerShell' -and $unquoted -match '(?i)(^|\s)select-string\b[^|]*\|') { $reasons.Add('use the dedicated file-search tool instead of piping Select-String') }
    if ($reasons.Count -eq 0) { exit 0 }
    $reason = 'BLOCKED by shell-form-guard. Rewrite the command as a single configured call. ' + ($reasons -join '; ')
    @{ hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason } } | ConvertTo-Json -Depth 4 -Compress
} catch { }
exit 0
