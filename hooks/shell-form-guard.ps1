# shell-form-guard.ps1 , PreToolUse guard on the Bash and PowerShell tools.
# Windows mirror of shell-form-guard.sh. Same rules, same scope, same limits;
# the .sh header carries the full rule-by-rule rationale and is the one to
# read. Keep the two in step: a rule that fires on one platform and not the
# other is a hole that only shows up on the machine you are not using.
#
# WHY: some shell FORMS can never match a permission allowlist entry, however
# correct that entry is, because the matcher treats a newline, a chain or a
# redirect as a command separator. Every one of those forms costs the owner a
# click that no settings change can ever retire. This hook refuses the forms
# instead, and tells the session what to write instead.
#
# DECLARED SCOPE, enumerated, never inferred:
#   reads   : stdin (the PreToolUse JSON payload)
#   writes  : NOTHING. No file, no network, no state.
#   decides : "deny" only, on the syntactic forms numbered 1-20 in the .sh
#             mirror, and no others. Numbers are stable and are cited by
#             docs/command-forms.md and rules/claude/permission-loops.md; rule 3 is
#             RETIRED (a bare `cd` is free) and its number is deliberately not
#             reused.
# It can NEVER approve, allow, or suppress a prompt. That pattern is
# permanently forbidden: a hook that answers prompts is how an autonomous
# setup gets its whole fleet flagged. A denial is not an approval bypass: it
# stops the call and tells the session to rewrite the command, which is work
# the session does, not work the owner clicks.
# The correct spelling of every allowed form is docs/command-forms.md.
#
# FAIL-OPEN by construction: any parse error, missing field, or unexpected
# exception exits 0 with no output, which is "no opinion". A crashing
# PreToolUse hook must never be able to block a shell.

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload = $raw | ConvertFrom-Json
    $command = $payload.tool_input.command
    if ([string]::IsNullOrWhiteSpace($command)) { exit 0 }

    # Rule 8 needs to know which shell tool is being called. If the field is
    # absent, rule 8 simply does not fire: no opinion, never a wrong deny.
    $toolName = ""
    if ($payload.tool_name) { $toolName = [string]$payload.tool_name }

    # Quoted spans are stripped before the chain check: a `&&` inside a commit
    # message or a grep pattern is TEXT, not a chain, and blocking it is a
    # false positive. Caught by this guard blocking its own commit.
    $unquoted = [regex]::Replace($command, '"[^"]*"', '')
    $unquoted = [regex]::Replace($unquoted, "'[^']*'", '')

    # First word, after any leading VAR=value env assignments.
    $head = [regex]::Replace($command.Trim(), '^(\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+', '')
    $first = ($head -split '\s+')[0]
    if ($null -eq $first) { $first = "" }
    # A token carrying a path separator is an absolute or relative invocation,
    # not a bare one. Only bare names are checked below.
    $bare = ""
    if ($first -notmatch '[/\\]') { $bare = ($first -replace '(?i)\.exe$', '').ToLower() }

    # wsl is a WRAPPER, so a rule that keys on the first word sees the launcher
    # and not the command that actually runs.
    #
    # WHICH RULES APPLY INSIDE WSL, AND WHY IT IS NOT ALL OF THEM. Most rules
    # here exist because a form MATCHES NO ENTRY and therefore costs a click.
    # Inside wsl that premise is false when the wrapper itself is blanket
    # allowed: a bare interpreter, an npm with no prefix, or a bash <script>
    # all run silent. Firing those rules inside wsl would be a false block,
    # which is worse than the hole, so they deliberately do NOT fire. What
    # survives is the family that was never about clicks: reaching for the
    # WRONG TOOL (rule 12, which costs a round trip and returns worse answers
    # than the Grep tool) and rewriting a file nobody read (rule 15). Rules 1,
    # 2, 7, 10, 11, 13 and 18 already scan the whole command string, so they
    # were never fooled by the wrapper.
    function Get-InnerTokens([string[]]$toks) {
        if ($toks.Count -eq 0 -or ($toks[0] -replace '(?i)\.exe$', '').ToLower() -ne 'wsl') {
            return @{ Tokens = $toks; Wrapped = $false }
        }
        $rest = @($toks[1..($toks.Count - 1)])
        $valued = @('-d', '--distribution', '-u', '--user', '--cd', '--shell-type')
        while ($rest.Count -gt 0) {
            $t = $rest[0]
            if ($valued -contains $t) {
                if ($rest.Count -le 2) { $rest = @() } else { $rest = @($rest[2..($rest.Count - 1)]) }
            } elseif ($t.StartsWith('-') -or $t -eq 'env' -or $t -match '^[A-Za-z_][A-Za-z0-9_]*=') {
                if ($rest.Count -le 1) { $rest = @() } else { $rest = @($rest[1..($rest.Count - 1)]) }
            } else {
                break
            }
        }
        return @{ Tokens = $rest; Wrapped = $true }
    }

    $unwrapped = Get-InnerTokens @($head -split '\s+' | Where-Object { $_ -ne '' })
    $wslWrapped = $unwrapped.Wrapped
    $innerFirst = ""
    if ($unwrapped.Tokens.Count -gt 0) { $innerFirst = $unwrapped.Tokens[0] }
    $innerBare = ""
    if ($innerFirst -notmatch '[/\\]') { $innerBare = ($innerFirst -replace '(?i)\.exe$', '').ToLower() }

    $reasons = @()
    if ($command -match "`n") {
        $reasons += "it spans multiple lines, and the permission matcher treats a newline as a command separator, so the trailing lines match no allowlist entry"
    }
    if ($unquoted.Contains("&&")) {
        $reasons += "it chains with && , so the matcher sees sub-commands that match no allowlist entry"
    }
    # Rule 3 RETIRED: a bare `cd` is free and is wanted. The chain that used to
    # follow it is still denied by rules 1/2/6/7.
    if (@("python", "python3", "py", "pytest", "pip", "pip3") -contains $bare -and $command -notmatch '-m\s+venv\b') {
        $reasons += "it invokes a bare $bare , and the allowlisted form is the ABSOLUTE venv interpreter path (docs/command-forms.md)"
    }
    if ($bare -eq "npm" -and -not $command.Contains("--prefix")) {
        $reasons += "it invokes npm with no --prefix , so it depends on the cwd and matches no entry (use npm --prefix <absolute repo path> run <script>)"
    }
    if ($unquoted.Contains(";")) {
        $reasons += "it chains with ; , so the matcher sees sub-commands that match no allowlist entry (one command per tool call)"
    }
    # Strip chains first, then REDIRECTIONS (`2>&1`, `>&2`, `&>file`). What is
    # left is a genuine call operator or background job. A redirection is not
    # an `&` operator and denying it AS ONE is a false positive (caught live).
    # Redirecting into a FILE is rule 18, decided separately.
    $amp = $unquoted.Replace("&&", "").Replace(">&", "").Replace("&>", "")
    if ($amp.Contains("&")) {
        $reasons += "it uses a single & , which the parser reads as an operator rather than part of a command string, so no allowlist entry can ever match it (call the absolute path directly)"
    }
    if (@("powershell", "pwsh", "cmd") -contains $bare) {
        $reasons += "it wraps the real work in a $bare -Command/-c string, which can never match an allowlist entry (the entries are written against the INNER command) and which hides any inner ; chain from this guard inside a quoted span. Run the inner command directly on the PowerShell tool, or use Glob to find files and Grep or Read to inspect them"
    }
    # Rule 10. Checked against the WHOLE command, not $unquoted: the entire
    # point is that it hides inside a quoted argument, where it still leaves a
    # dollar the matcher cannot resolve.
    if ($command.Contains('\$')) {
        $reasons += "it contains a backslash-escaped dollar , which is not the escape either shell here uses (PowerShell escapes with a backtick, Bash wants single quotes for a literal dollar) and which leaves a dollar sigil the permission matcher cannot resolve, so it cannot prove the command read-only and cannot prefix-match an entry. Observed twice: one dialog offered no Always allow at all, and the other prompted on a bare grep. Use single quotes, or drop the sigil from the text"
    }
    # Rule 11. Whole command, same reasoning as rule 10: the escape is usually
    # inside a path argument, and the harness labels it out loud.
    if ($command -match '\\[ \t]') {
        $reasons += "it contains a backslash-escaped space or tab , which the harness itself flags as 'Contains backslash-escaped whitespace' and which leaves a command string the matcher cannot resolve, so no allowlist entry can ever match it, and those dialogs offer no Always allow. No allowed form needs an escaped space: built-in read-only commands like git, tail, ls and grep are FREE when invoked BARE, and 'absolute paths' means the path ARGUMENTS, never an invented absolute path to the binary itself"
    }
    # Rule 12. Bash tool only, and the pipe is looked for in $unquoted so a `|`
    # inside a quoted regex is TEXT, not a pipeline. PowerShell is deliberately
    # excluded: its Get-* | Select-Object form is allowlistable and silent.
    # Rule 13. `git commit` with no standalone `--` pathspec separator. Checked
    # on $unquoted so a commit MESSAGE mentioning "commit" or "--" is not the
    # trigger. `--amend` is carved out: docs/command-forms.md prescribes
    # `git commit --amend -F <file>` for a message body, which has no pathspec.
    # `--no-edit` is carved out too, because it is the only legal way to
    # CONCLUDE A MERGE: git refuses a partial commit while MERGE_HEAD exists
    # ("fatal: cannot do a partial commit during a merge"), so there is no
    # pathspec spelling to rewrite to. The premise of the rule (a pathspec is
    # always available) is simply false mid-merge. The carve-out is narrow on
    # purpose: `--no-edit` is what concludes a merge, and a session using it to
    # sneak a plain commit past the guard would still be committing a tree it
    # just resolved by hand.
    if ($unquoted -match '(^|\s)commit(\s|$)' -and $unquoted -match '(^|\s)git(\s|$)|(^|\s)git\s' -and $unquoted -notmatch '\s--(\s|$)' -and $unquoted -notmatch '--amend' -and $unquoted -notmatch '--no-edit') {
        $reasons += "it runs git commit with no ' -- <path>' pathspec separator. Plain git commit commits the whole INDEX, including files another session staged, and several sessions share this working tree by design. That is how a permissions diff once shipped inside an unrelated commit. Use git -C <repo> commit -m '<one line>' -- <path> <path> (rules/claude/git-github.md)"
    }
    # Rule 14. A drive letter followed by DOUBLED separators (C:\\ or C:\\\\).
    # Whole command: the doubling is inside a path argument. Anchored on the
    # drive letter on purpose, so a UNC path (\\server\share, no drive letter)
    # and a quoted regex are not false positives.
    if ($command -match '[A-Za-z]:\\\\') {
        $reasons += "it contains a drive letter followed by DOUBLED backslashes (C:\\ or worse), which is JSON escaping that leaked into a real command. settings.json and settings.local.json are JSON, where \\ means ONE backslash, so a command copied out of either file has every separator doubled and matches no entry, however correct that entry is. A doubled AND a quadrupled spelling of one command have both been Always-allowed here and neither ever rematched, because a stored approval is keyed to the exact string. Type the path with single backslashes from docs/command-forms.md, and never copy a command out of a settings file"
    }
    # Rule 15. In-place stream edit (`sed -i`, `perl -pi`, `ruby -i`). Each
    # PIPELINE SEGMENT is checked on its own so a trailing `| grep -i x` after a
    # read-only `sed -n` is not a false positive: the -i must be an option of
    # the sed/perl/ruby invocation itself. Short-option CLUSTERS count
    # (`perl -pi -e`), and so does `-i.bak`.
    foreach ($seg in ($command -split '\|')) {
        $segToks = @((Get-InnerTokens @($seg.Trim() -split '\s+' | Where-Object { $_ -ne "" })).Tokens)
        if ($segToks.Count -lt 2) { continue }
        $prog = (($segToks[0] -split '[/\\]')[-1] -replace '(?i)\.exe$', '').ToLower()
        if (@("sed", "perl", "ruby") -notcontains $prog) { continue }
        $inPlace = $false
        foreach ($t in $segToks[1..($segToks.Count - 1)]) {
            if ($t -match '^-[A-Za-z]*i' -or $t.StartsWith("--in-place")) { $inPlace = $true }
        }
        if ($inPlace) {
            $reasons += "it edits a file IN PLACE with $prog , which is the wrong tool and always prompts. The harness says so itself ('sed command contains operations that require explicit approval'): sed is in the built-in read-only set only while its script writes nothing, so -i takes it straight back out. Editing a file is the Edit tool, which acceptEdits already covers and which can never prompt; and an in-place edit addressed by LINE NUMBER rewrites text no one read"
            break
        }
    }
    if (($toolName -eq "Bash" -or $wslWrapped) -and @("grep", "rg", "cat", "head", "tail", "find", "ls") -contains $innerBare -and $unquoted.Contains("|")) {
      if ($innerBare -eq "rg") {
        $reasons += "it shells out to ripgrep. THE GREP TOOL IS RIPGREP, so this is the same engine reached the long way round: it costs a round trip, it cannot prompt only because the wrapper is blanket-allowed, and it hands back a wall of text instead of structured matches. Use the Grep tool, which takes the same pattern plus glob, type, -A/-B/-C context, and output_mode=files_with_matches when you only need the file list"
      } else {
        $reasons += "it pipes $innerBare into another command to answer a question about file CONTENTS. Every stage may be free on its own and the pipeline still prompts, because the docs treat sort and sed as write-capable (sort -o writes a file) so an unquoted glob anywhere in the pipe forces a click. Use the Grep tool (search inside files), Read (one file) or Glob (find files): none of them can ever prompt"
      }
    }
    if ($toolName -eq "PowerShell" -and @("curl", "wget") -contains $bare) {
        $reasons += "it runs $bare on the PowerShell tool, where $bare is an ALIAS for Invoke-WebRequest and rejects curl flags outright, and where no allowlist entry covers it. Run the identical command on the Bash tool instead, which Bash(curl:*) already allows"
    }
    # Rule 16. PowerShell tool only: on Bash a leading `[` is the test builtin.
    if ($toolName -eq "PowerShell" -and $first.StartsWith("[")) {
        $reasons += "it starts with a .NET type literal, so it is improvised CODE rather than a command from docs/command-forms.md, and no allowlist entry can cover the family without permitting arbitrary script text (the entry for [scriptblock]::Create would allow building and invoking anything). To check a .ps1 parses, run tools/parse-check.ps1 <path> by its absolute path, which parses and never invokes; for a .sh use bash -n <path> on the Bash tool. To read a file use Read, to find one use Glob, to search one use Grep: none of those can ever prompt"
    }
    # Rule 17. Bash tool only. A flag after `bash` is exempt, which keeps the
    # allowed `bash -n <path>` syntax check working.
    $bt = @($head -split '\s+')
    if ($toolName -eq "Bash" -and $first -eq "bash" -and $bt.Count -gt 1 -and -not $bt[1].StartsWith("-")) {
        $reasons += "it runs a script as ``bash <path>``, which matches no entry: every script permission here is written as the script PATH itself, so putting ``bash`` in front makes the command string start with ``bash`` and match nothing. Invoke the script BARE by its absolute path instead. The ``bash -n <path>`` syntax check is unaffected"
    }
    # Rule 18. OUTPUT redirect to a FILE, both tools. Looked for in $unquoted so
    # a `>` inside a quoted commit message or grep pattern is TEXT. `2>&1` and
    # `>&2` fall out for free: the target class excludes `&`, so they match
    # nothing. INPUT redirection is untouched (allowed database-import forms
    # use it).
    $nullTargets = @('/dev/null', '$null', 'nul', 'con', '/dev/stdout', '/dev/stderr')
    $redirTargets = @()
    foreach ($m in [regex]::Matches($unquoted, '(?:\d?>>?|&>>?)\s*([^\s|;&<>]+)')) {
        if ($nullTargets -notcontains $m.Groups[1].Value.ToLower()) { $redirTargets += $m.Groups[1].Value }
    }
    if ($redirTargets.Count -gt 0) {
        $reasons += "it redirects output into a FILE, which prompts even when the bare command IS allowlisted, and an Always allow on one such command does not cover the next, because a stored approval is keyed to the EXACT string and output filenames change. No settings.json entry can retire this, and the form is not needed anyway: this tool already RETURNS stdout and stderr to the session, so run the command bare and read what comes back. If the real problem is that the output is LONG and the interesting line is hidden by the truncation in the middle, filter at the SOURCE instead of capturing everything. To put text in a file, use the Write tool. A null device target, 2>&1, >&2 and INPUT redirection are all still allowed"
    }

    # Rule 19. BASH tool only: an UNQUOTED Windows drive path (`C:\...`). Checked
    # in $unquoted, so a QUOTED backslash path is exempt: double quotes preserve
    # the separators in Git Bash, so that form at least runs (it still matches no
    # entry, but it is not broken). PowerShell is untouched: backslash is correct
    # there, and the identical command was verified silent on it.
    if ($toolName -eq "Bash" -and $unquoted -match '[A-Za-z]:\\') {
        $reasons += "it passes an UNQUOTED Windows path with BACKSLASHES on the Bash tool. Git Bash strips those backslashes before the command runs, so the form cannot work at all, whatever the permission list says: git answers ""cannot change to CUsersyouproject"". It also matches no allowlist entry, and NO ENTRY SHOULD BE ADDED, because that would bless a command that is already broken. Use the POSIX spelling on the Bash tool (/c/Users/...), which every entry here is written against. On the PowerShell tool the backslash spelling is the correct one and is never denied by this rule"
    }

    # Rule 20. A git command run INSIDE wsl against a /mnt/... path, which is the
    # Windows filesystem reached through the WSL bridge. That bridge can serve
    # STALE bytes, and git run that way has falsely reported a clean working
    # tree. The bridge is what makes the mistake reachable: every Windows repo
    # gains a second, worse spelling that looks fine.
    if ($wslWrapped -and $innerBare -eq "git" -and $command -match '/mnt/[A-Za-z]/') {
        $reasons += "it runs git INSIDE wsl against a /mnt/ path, which is the Windows filesystem seen through the WSL bridge. That bridge can serve STALE bytes: git run this way has reported a CLEAN working tree over files that were in fact modified, so the answer looks authoritative and is wrong. It also matches no git entry, since every one of them is written against a Windows or POSIX-on-Windows path. Run git on the WINDOWS side with the forms in docs/command-forms.md: git -C C:/Users/... on PowerShell, or git -C /c/Users/... on Bash. Reserve wsl git for repos that genuinely LIVE in the Linux filesystem"
    }

    if ($reasons.Count -eq 0) { exit 0 }

    $why = $reasons -join "; and "
    $guidance = @(
        "BLOCKED by shell-form-guard: $why.",
        "This form always prompts and no settings.json entry can ever fix it (rules/claude/permission-loops.md).",
        "Rewrite it from the table in docs/command-forms.md: one command per tool call, absolute paths instead of 'cd X &&', the absolute venv interpreter instead of a bare 'python'.",
        "Do NOT ask for permission for this command. Rewriting is the session's work.",
        "If the goal was to read or search a file, use the Read or Grep tool instead. Those need no permission at all and are the intended path."
    ) -join " "

    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = $guidance
        }
    }
    $out | ConvertTo-Json -Depth 4 -Compress
    exit 0
}
catch {
    exit 0
}
