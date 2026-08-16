# parse-check.ps1 , syntax-check a PowerShell file WITHOUT running it.
#
# WHY: a session that edits a hook needs to know it still parses. Every hook
# here is invoked by the harness, not by a session, so a syntax error is
# SILENT: the hook fails, SessionStart prints nothing, and the next session
# infers from the quiet that nothing happened. Never infer that something did
# not happen from the absence of a record; that is evidence about the recorder.
#
# Before this file existed there was no allowed form for the check, so a
# session improvised one and it cost a permission click:
#   [scriptblock]::Create((Get-Content -Raw "<hook>")).GetType().Name
# An allowlist entry for that would have been the wrong fix: [scriptblock]::Create
# with a `:*` wildcard permits building and invoking ANY script text. This
# script parses and never invokes, so one narrow entry covers the whole need.
#
# DECLARED SCOPE, enumerated, never inferred:
#   reads   : exactly the file paths passed as arguments
#   writes  : NOTHING. No file, no network, no state
#   runs    : NOTHING. The parser builds an AST; the code is never executed
#   exit    : 0 when every file parses, 1 when any file has a parse error or
#             is missing
#
# Usage (PowerShell tool, first word is the absolute path, no `&`):
#   <repo>\tools\parse-check.ps1 <file.ps1> [more.ps1 ...]
#
# The `.sh` half of a hook pair has a shell built-in for this and needs no
# script: `bash -n <absolute path>` on the Bash tool. Both forms are rows in
# docs/command-forms.md.

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Path
)

if (-not $Path -or $Path.Count -eq 0) {
    Write-Output "parse-check: give it one or more .ps1 paths"
    exit 1
}

$bad = 0

foreach ($p in $Path) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
        Write-Output "MISSING  $p"
        $bad++
        continue
    }

    $full = (Resolve-Path -LiteralPath $p).Path
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($full, [ref]$null, [ref]$errors)

    if ($errors -and $errors.Count -gt 0) {
        Write-Output "FAIL     $full ($($errors.Count) parse error(s))"
        foreach ($e in $errors) {
            Write-Output "         line $($e.Extent.StartLineNumber): $($e.Message)"
        }
        $bad++
    }
    else {
        Write-Output "OK       $full"
    }
}

if ($bad -gt 0) { exit 1 }
exit 0
