#!/bin/sh
# shell-form-guard.sh , PreToolUse guard on the Bash and PowerShell tools.
# POSIX mirror of shell-form-guard.ps1. Same rules, same scope, same limits.
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
#   decides : "deny" only, on these syntactic forms and no others:
#               1. newline in the command
#               2. `&&` chain outside quotes
#               3. RETIRED. Was "first word is `cd`". `cd` and `ls` are in the
#                  built-in read-only set that needs no entry and cannot be
#                  configured
#                  (https://code.claude.com/docs/en/permissions#read-only-commands).
#                  The COMPOUND was always the defect, never the `cd`: every
#                  prompt this rule was written for (`cd X && npm run
#                  build:css`) is denied by rules 1, 2, 6 and 7 regardless.
#                  Number kept, not renumbered, because the docs cite these
#                  rules by number.
#               4. first word is a bare interpreter (python, python3, py,
#                  pytest, pip, pip3), except `-m venv`
#               5. first word is `npm` with no `--prefix`
#               6. a single `&` outside quotes. REDIRECTIONS ARE NOT THIS:
#                  `2>&1`, `>&2`, `&>file` are never denied BY THIS RULE
#                  (a false positive caught live on an `ls ... 2>&1`).
#                  A redirect into a FILE is rule 18, decided separately
#               7. a `;` chain outside quotes
#               9. first word `powershell`, `pwsh` or `cmd`, a SHELL WRAPPER.
#                  No entry can match a command starting with `powershell`
#                  however safe the inside is, and an inner `;` chain hides
#                  from rule 7 inside a quoted span. Usually the wrong tool
#                  too: use Glob/Grep.
#              10. a BACKSLASH-ESCAPED DOLLAR (`\$`) anywhere, quoted or not.
#                  Wrong escape in both shells, and it leaves a dollar the
#                  matcher cannot resolve. Use single quotes, or drop the sigil.
#              11. a BACKSLASH-ESCAPED SPACE OR TAB anywhere, quoted or not.
#                  The harness names the cause itself: "Contains
#                  backslash-escaped whitespace", and offers no Always allow,
#                  so no entry can ever fix one. It is usually the wrong tool
#                  twice over: `git` and `tail` are in the built-in read-only
#                  set and FREE when invoked BARE, and reading a file is the
#                  Read tool. "Absolute paths" means the path ARGUMENTS, never
#                  an invented absolute path to the BINARY.
#              12. on the BASH tool ONLY, first word is a file-inspection
#                  command (`grep`, `rg`, `cat`, `head`, `tail`, `find`, `ls`)
#                  AND it PIPES into something else. All are in the built-in
#                  read-only set and FREE alone, but a pipeline prompts anyway:
#                  the docs carve out `sort` and `sed` as write-capable
#                  (`sort -o` writes a file). A question about file contents is
#                  one Grep tool call. Pipe looked for in the UNQUOTED
#                  remainder, so `grep "a|b"` is text and never denied. NOT
#                  extended to PowerShell: `Get-ChildItem | Select-Object` is
#                  allowlistable and verified silent.
#              15. an IN-PLACE STREAM EDIT (`sed -i`, `perl -pi`, `ruby -i`)
#                  anywhere in the pipeline. The harness names the cause
#                  itself: "sed command contains operations that require
#                  explicit approval", and the docs agree, `sed` is read-only
#                  ONLY while its script writes nothing. So this is the WRONG
#                  TOOL, twice over: editing a file is the Edit tool, which
#                  `acceptEdits` covers and which can never prompt, and an edit
#                  addressed by LINE NUMBER rewrites text nothing read. No
#                  entry is wanted here even if one would match: `sed -i
#                  <path>` is unbounded in-place rewriting of any file on the
#                  disk.
#               8. on the PowerShell tool ONLY, first word `curl` or `wget`:
#                  both are Invoke-WebRequest ALIASES in Windows PowerShell so
#                  curl flags fail, and typically only `Bash(curl:*)` is
#                  allowlisted. Never denied on the Bash tool.
#              16. on the PowerShell tool ONLY, first word starts with `[`,
#                  i.e. a bare .NET TYPE-LITERAL EXPRESSION rather than a
#                  command from the allowed-forms table. Improvised code is
#                  unmatchable as a FAMILY, and the entry that would match it
#                  permits building and invoking any script text, so the narrow
#                  fix is a named tool: `tools/parse-check.ps1 <path>` parses
#                  without invoking, and `bash -n <path>` does the `.sh` half.
#                  PowerShell-only on purpose: a leading `[` on Bash is the
#                  `test` builtin.
#              17. on the Bash tool ONLY, first word `bash` followed by a
#                  token that is not a flag, i.e. running a script as
#                  `bash <path>` instead of invoking the script bare. Every
#                  script entry is written as the script PATH
#                  (`Bash(<abs path>:*)`), so prefixing `bash` makes the
#                  command string start with `bash` and match nothing.
#                  `bash -n <path>` is deliberately untouched: it is the
#                  allowed .sh syntax check and has its own entry.
#              18. OUTPUT REDIRECTION TO A FILE (`> f`, `>> f`, `2> f`,
#                  `&> f`), on both tools. A redirect prompts even when the
#                  bare command IS allowlisted, and an Always allow does not
#                  cover the next run, because a stored approval is keyed to
#                  the EXACT string and output filenames change. WHY the
#                  redirect defeats a `:*` entry is UNVERIFIED, and the
#                  mechanism does not matter because the form is unnecessary:
#                  both tools already RETURN stdout and stderr to the session,
#                  so run the command bare and read what comes back; writing a
#                  file is the Write tool. NOT denied, deliberately: `2>&1`,
#                  `>&2`, a null device target (`/dev/null`, `$null`, `NUL`),
#                  and INPUT redirection, which allowed database-import forms
#                  depend on.
#              21. an INTERPRETER HANDED INLINE CODE (`-c`, `-Command`) at ANY
#                  path spelling: `python`, `python.exe`, `C:\...\python.exe`.
#                  Rules 4 and 9 denied this already but keyed on the BARE first
#                  word, so the absolute spelling of the same form slipped past
#                  the whole guard and prompted the owner, 2026-08-21. No entry
#                  can fix it: the allowlisted interpreter forms are `-m <module>`
#                  and a named script, and an inner `;` chain hides from rule 7
#                  inside the quoted payload while the matcher still splits on
#                  it. Flag looked for in the UNQUOTED remainder, so a `-c`
#                  inside a payload or a grep pattern is text. `-e` is
#                  deliberately NOT covered yet: no instance has cost a click.
# It can NEVER approve, allow, or suppress a prompt. That pattern is
# permanently forbidden: a hook that answers prompts is how an autonomous
# setup gets its whole fleet flagged.
# The correct spelling of every allowed form is docs/command-forms.md.
#
# FAIL-OPEN by construction: no python3, bad JSON, missing field, or any
# other surprise exits 0 with no output, which is "no opinion". A crashing
# PreToolUse hook must never be able to block a shell.
#
# The guard body goes through `python -c` on purpose, NOT a heredoc: a
# heredoc would occupy stdin and the payload would never reach the parser.

# Pick an interpreter that ACTUALLY RUNS, not merely one that is on PATH.
# On Windows, `command -v python3` resolves to the Microsoft Store stub at
# .../WindowsApps/python3, which exists, executes nothing, and returns
# failure. A one-line `command -v python3 || command -v python` picks that
# stub, the body below fails, `|| exit 0` fires, and the guard ALLOWS EVERY
# FORM IT IS SUPPOSED TO DENY: measured, 11 of 27 cases silently allowed under
# Git Bash, which is the shell the Bash tool uses, while the same matrix run
# through WSL reported all-deny and so looked green. The probe must therefore
# be an execution test, never a PATH lookup.
PY=""
for _cand in python3 python py; do
    _p=$(command -v "$_cand" 2>/dev/null) || continue
    [ -n "$_p" ] || continue
    if [ "$("$_p" -c 'print(9)' 2>/dev/null)" = "9" ]; then
        PY="$_p"
        break
    fi
done
[ -n "$PY" ] || exit 0

"$PY" -c '
import json, re, sys
try:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command") or ""
    # Rule 8 needs the tool name. Absent field means rule 8 does not fire.
    tool_name = payload.get("tool_name") or ""
except Exception:
    sys.exit(0)

if not command.strip():
    sys.exit(0)

# Quoted spans are stripped before the chain check: a && inside a commit
# message or a grep pattern is TEXT, not a chain, and blocking it is a false
# positive. Caught by this guard blocking its own commit.
unquoted = re.sub(r"\"[^\"]*\"", "", command)
q = chr(39)  # single quote, spelled this way because the shell wrapper is single-quoted
unquoted = re.sub(q + "[^" + q + "]*" + q, "", unquoted)

# First word, after any leading VAR=value env assignments. A token carrying a
# path separator is an absolute or relative invocation, not a bare one.
head = re.sub(r"^(\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", command.strip())
first = head.split()[0] if head.split() else ""
bare = "" if re.search(r"[/\\\\]", first) else re.sub(r"(?i)\.exe$", "", first).lower()

# wsl is a WRAPPER, so a rule that keys on the first word sees the launcher and
# not the command that actually runs.
#
# WHICH RULES APPLY INSIDE WSL, AND WHY IT IS NOT ALL OF THEM. Most rules here
# exist because a form MATCHES NO ENTRY and therefore costs a click. Inside wsl
# that premise is false when the wrapper itself is blanket-allowed: a bare
# interpreter, an npm with no prefix, or a bash <script> all run silent.
# Firing those rules inside wsl would be a false block, which is worse than the
# hole, so they deliberately do NOT fire. What survives is the family that was
# never about clicks: reaching for the WRONG TOOL (rule 12, which costs a round
# trip and returns worse answers than the Grep tool) and rewriting a file
# nobody read (rule 15). Rules 1, 2, 7, 10, 11, 13 and 18 already scan the
# whole command string, so they were never fooled by the wrapper.
def _unwrap(toks):
    if not toks or re.sub(r"(?i)\.exe$", "", toks[0]).lower() != "wsl":
        return toks, False
    rest = toks[1:]
    valued = ("-d", "--distribution", "-u", "--user", "--cd", "--shell-type")
    while rest:
        t = rest[0]
        if t in valued:
            rest = rest[2:]
        elif t.startswith("-"):
            rest = rest[1:]
        elif t == "env" or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            rest = rest[1:]
        else:
            break
    return rest, True

_inner, wsl_wrapped = _unwrap(head.split())
inner_first = _inner[0] if _inner else ""
inner_bare = "" if re.search(r"[/\\\\]", inner_first) else re.sub(r"(?i)\.exe$", "", inner_first).lower()

reasons = []
if "\n" in command:
    reasons.append("it spans multiple lines, and the permission matcher treats a newline as a command separator, so the trailing lines match no allowlist entry")
if "&&" in unquoted:
    reasons.append("it chains with && , so the matcher sees sub-commands that match no allowlist entry")
# Rule 3 RETIRED: a bare `cd` is free and wanted. The chain that used to follow
# it is still denied by rules 1/2/6/7.
if bare in ("python", "python3", "py", "pytest", "pip", "pip3") and not re.search(r"-m\s+venv\b", command):
    reasons.append("it invokes a bare " + bare + " , and the allowlisted form is the ABSOLUTE venv interpreter path (docs/command-forms.md)")
if bare == "npm" and "--prefix" not in command:
    reasons.append("it invokes npm with no --prefix , so it depends on the cwd and matches no entry (use npm --prefix <absolute repo path> run <script>)")
if ";" in unquoted:
    reasons.append("it chains with ; , so the matcher sees sub-commands that match no allowlist entry (one command per tool call)")
# Strip chains first, then REDIRECTIONS (2>&1, >&2, &>file). What survives is
# a genuine call operator or background job. A redirection is not an `&`
# operator; redirecting into a FILE is rule 18, decided separately.
amp = unquoted.replace("&&", "").replace(">&", "").replace("&>", "")
if "&" in amp:
    reasons.append("it uses a single & , which the parser reads as an operator rather than part of a command string, so no allowlist entry can ever match it (call the absolute path directly)")
if bare in ("powershell", "pwsh", "cmd"):
    reasons.append("it wraps the real work in a " + bare + " -Command/-c string, which can never match an allowlist entry (the entries are written against the INNER command) and which hides any inner ; chain from this guard inside a quoted span. Run the inner command directly on the PowerShell tool, or use Glob to find files and Grep or Read to inspect them")
# Rule 21. An interpreter handed INLINE CODE (`-c`, `-Command`), at ANY spelling.
# Rules 4 and 9 already deny this form, but both key on `bare`, which is blanked
# above for any token carrying a path separator. So the ABSOLUTE spelling of the
# identical command slipped past every rule in this guard, and the owner was
# prompted on exactly that, 2026-08-21:
#   C:\...\Python313\python.exe -c "import json,os,subprocess; root=r(...); ..."
# NOTE: no apostrophes in this comment on purpose. The whole guard body is a
# single-quoted shell string, so one would close it early (guard-matrix.sh says
# so by name when the guard stops parsing).
# Two independent reasons no entry can EVER fix it, which is why this is a deny
# and not an allowlist gap: the allowlisted interpreter forms are `-m <module>`
# and a NAMED SCRIPT, so a `-c` payload prefix-matches none of them; and the `;`
# chain lives INSIDE a quoted span, invisible to rule 7 while the permission
# matcher still splits the string on it. The dialog proves the split, it listed
# the fragments as separate commands.
# Basename, so `python`, `python.exe` and `C:\...\python.exe` are one rule.
# The flag is looked for in the UNQUOTED remainder, so a `-c` inside a payload
# or a grep pattern is text and never denied.
_inline_interp = re.sub(r"(?i)\.exe$", "", re.split(r"[/\\\\]", first)[-1]).lower() if first else ""
if _inline_interp in ("python", "python3", "py", "node", "ruby", "perl", "bash", "sh", "zsh", "powershell", "pwsh", "cmd") and re.search(r"(?i)(^|\s)-(c|command)(\s|$)", unquoted):
    reasons.append("it hands INLINE CODE to " + _inline_interp + " with -c/-Command, which matches no allowlist entry at any path spelling: the allowed interpreter forms are -m <module> and a named script. An inner ; chain also hides from the chain rule inside the quoted payload while the permission matcher still splits on it. A question about a file is the Read, Grep or Glob tool, none of which can ever prompt; if it is genuinely a script, put it in a file and invoke that file")
# Rule 10. Checked against the WHOLE command, not `unquoted`: the entire point
# is that it hides inside a quoted argument, where it still leaves a dollar the
# matcher cannot resolve.
if "\\$" in command:
    reasons.append("it contains a backslash-escaped dollar , which is not the escape either shell here uses (PowerShell escapes with a backtick, Bash wants single quotes for a literal dollar) and which leaves a dollar sigil the permission matcher cannot resolve, so it cannot prove the command read-only and cannot prefix-match an entry. Observed twice: one dialog offered no Always allow at all, and the other prompted on a bare grep. Use single quotes, or drop the sigil from the text")
# Rule 11. Whole command, same reasoning as rule 10: the escape usually sits
# inside a path argument, and the harness labels it out loud.
if "\\ " in command or "\\\t" in command:
    reasons.append("it contains a backslash-escaped space or tab , which the harness itself flags as \"Contains backslash-escaped whitespace\" and which leaves a command string the matcher cannot resolve, so no allowlist entry can ever match it, and those dialogs offer no Always allow. No allowed form needs an escaped space: built-in read-only commands like git, tail, ls and grep are FREE when invoked BARE, and absolute paths means the path ARGUMENTS, never an invented absolute path to the binary itself")
# Rule 12. Bash tool only, and the pipe is looked for in `unquoted` so a `|`
# inside a quoted regex is TEXT, not a pipeline. PowerShell is deliberately
# excluded: its Get-* | Select-Object form is allowlistable and verified silent.
# Rule 13. `git commit` with no standalone `--` pathspec separator. Checked on
# `unquoted` so a commit MESSAGE mentioning "commit" or "--" is not the trigger.
# `--amend` is carved out: the allowed-forms table prescribes
# `git commit --amend -F <file>` for a message body, which has no pathspec.
# `--no-edit` is carved out too, because it is the only legal way to CONCLUDE A
# MERGE: git refuses a partial commit while MERGE_HEAD exists ("fatal: cannot
# do a partial commit during a merge"), so there is no pathspec spelling to
# rewrite to. The premise of the rule, that a pathspec is always available, is
# simply false mid-merge.
# NO APOSTROPHE MAY APPEAR ANYWHERE IN THIS BODY, EVER. The whole body is one
# single-quoted shell string, opened just above the import line. A lone
# apostrophe closes that string early and every line after it is parsed as
# shell, which makes the file unparseable and the guard inert. That is not
# hypothetical: one possessive apostrophe in a comment broke this mirror for
# six days, and `tools/guard-matrix.sh` now refuses the file outright if it
# finds one. The carve-out is narrow on purpose: `--no-edit` is what concludes
# a merge, and a session using it to sneak a plain commit past the guard would
# still be committing a tree it just resolved by hand.
if (re.search(r"(^|\s)git(\s|$)", unquoted) and re.search(r"(^|\s)commit(\s|$)", unquoted)
        and not re.search(r"\s--(\s|$)", unquoted) and "--amend" not in unquoted
        and "--no-edit" not in unquoted):
    reasons.append("it runs git commit with no \" -- <path>\" pathspec separator. Plain git commit commits the whole INDEX, including files another session staged, and several sessions share this working tree by design. That is how a permissions diff once shipped inside an unrelated commit. Use git -C <repo> commit -m \"<one line>\" -- <path> <path> (rules/claude/git-github.md)")

# Rule 22. `Select-String` PIPED into anything, on the PowerShell tool. Searching
# inside files is the Grep tool, which is the same engine, cannot prompt, and
# returns structured matches instead of text to reformat. The owner was prompted
# on 2026-08-21 on
#   Select-String -Path "<file>" -Pattern "replace" | ForEach-Object { ... }
# where `Select-String` IS allowlisted and `ForEach-Object` is not, and the
# matcher requires every stage of a pipe to match independently. NO ENTRY IS
# WANTED for the destination: a `ForEach-Object { ... }` script block is
# improvised code, so the entry that matched it would permit building and
# invoking any script text, which is rule 16 reasoning.
# Deliberately NARROW: only `Select-String` as the SOURCE of a pipe. A bare
# `Select-String` is untouched (it is allowlisted and useful), and so is
# `Get-ChildItem | Select-Object`, which is allowlisted and verified silent.
# That is why this does NOT reuse rule 12, which stays Bash-only.
if tool_name == "PowerShell" and re.search(r"(?i)(^|\s)select-string\b[^|]*\|", unquoted):
    reasons.append("it pipes Select-String into another command to answer a question about file CONTENTS. Select-String is allowlisted but the destination stage is not, and the matcher requires every stage of a pipeline to match on its own, so the pipe prompts. No entry is wanted for a ForEach-Object script block either: that is improvised code, and an entry matching it would permit invoking any script text. Use the Grep tool, which is the same search engine, takes -n for line numbers plus glob/type filters and -A/-B/-C context, and can never prompt")

# Rule 14. A drive letter followed by DOUBLED separators. Whole command: the
# doubling sits inside a path argument. Anchored on the drive letter on purpose,
# so a UNC path (no drive letter) and a quoted regex are not false positives.
if re.search(r"[A-Za-z]:\\\\", command):
    reasons.append("it contains a drive letter followed by DOUBLED backslash separators, which is JSON escaping that leaked into a real command. settings.json and settings.local.json are JSON, where a doubled backslash means ONE backslash, so a command copied out of either file has every separator doubled and matches no entry, however correct that entry is. A doubled AND a quadrupled spelling of one command have both been Always-allowed here and neither ever rematched, because a stored approval is keyed to the exact string. Type the path with single separators from docs/command-forms.md, and never copy a command out of a settings file")

# Rule 15. In-place stream edit. Each PIPELINE SEGMENT is checked on its own so
# a trailing `| grep -i x` after a read-only `sed -n` is not a false positive:
# the -i must be an option of the sed/perl/ruby invocation itself. Short-option
# CLUSTERS count (`perl -pi -e`), and so does `-i.bak`.
for _seg in re.split(r"\|", command):
    _toks, _ = _unwrap(_seg.strip().split())
    if not _toks:
        continue
    _prog = re.sub(r"(?i)\.exe$", "", re.split(r"[/\\\\]", _toks[0])[-1]).lower()
    if _prog not in ("sed", "perl", "ruby"):
        continue
    if any(re.match(r"^-[A-Za-z]*i", t) or t.startswith("--in-place") for t in _toks[1:]):
        reasons.append("it edits a file IN PLACE with " + _prog + " , which is the wrong tool and always prompts. The harness says so itself (\"sed command contains operations that require explicit approval\"): sed is in the built-in read-only set only while its script writes nothing, so -i takes it straight back out. Editing a file is the Edit tool, which acceptEdits already covers and which can never prompt; and an in-place edit addressed by LINE NUMBER rewrites text no one read")
        break

if (tool_name == "Bash" or wsl_wrapped) and inner_bare in ("grep", "rg", "cat", "head", "tail", "find", "ls") and "|" in unquoted:
    if inner_bare == "rg":
        reasons.append("it shells out to ripgrep. THE GREP TOOL IS RIPGREP, so this is the same engine reached the long way round: it costs a round trip, it cannot prompt only because the wrapper is blanket-allowed, and it hands back a wall of text instead of structured matches. Use the Grep tool, which takes the same pattern plus glob, type, -A/-B/-C context, and output_mode=files_with_matches when you only need the file list")
    else:
        reasons.append("it pipes " + inner_bare + " into another command to answer a question about file CONTENTS. Every stage may be free on its own and the pipeline still prompts, because the docs treat sort and sed as write-capable (sort -o writes a file) so an unquoted glob anywhere in the pipe forces a click. Use the Grep tool (search inside files), Read (one file) or Glob (find files): none of them can ever prompt")

if tool_name == "PowerShell" and bare in ("curl", "wget"):
    reasons.append("it runs " + bare + " on the PowerShell tool, where " + bare + " is an ALIAS for Invoke-WebRequest and rejects curl flags outright, and where no allowlist entry covers it. Run the identical command on the Bash tool instead, which Bash(curl:*) already allows")

# Rule 16. PowerShell tool only: on Bash a leading [ is the test builtin.
if tool_name == "PowerShell" and first.startswith("["):
    reasons.append("it starts with a .NET type literal, so it is improvised CODE rather than a command from docs/command-forms.md, and no allowlist entry can cover the family without permitting arbitrary script text (an entry for the scriptblock Create factory would allow building and invoking anything). To check that a .ps1 parses, run tools/parse-check.ps1 by its absolute path, which parses and never invokes; for a .sh use bash -n with the absolute path on the Bash tool. To read a file use Read, to find one use Glob, to search one use Grep: none of those can ever prompt")

# Rule 17. Bash tool only. A flag after `bash` is exempt, which keeps the
# allowed `bash -n <path>` syntax check working.
_bt = head.split()
if tool_name == "Bash" and first == "bash" and len(_bt) > 1 and not _bt[1].startswith("-"):
    reasons.append("it runs a script as `bash <path>`, which matches no entry: every script permission here is written as the script PATH itself, so putting `bash` in front makes the command string start with `bash` and match nothing. Invoke the script BARE by its absolute path instead. The `bash -n <path>` syntax check is unaffected")

# Rule 18. OUTPUT redirect to a FILE, both tools. Looked for in `unquoted` so a
# `>` inside a quoted commit message or grep pattern is TEXT. `2>&1` and `>&2`
# fall out for free: the target class excludes `&`, so they match nothing. INPUT
# redirection is untouched (allowed database-import forms use it).
_redir = [t for t in re.findall(r"(?:\d?>>?|&>>?)\s*([^\s|;&<>]+)", unquoted)
          if t.lower() not in ("/dev/null", "$null", "nul", "con", "/dev/stdout", "/dev/stderr")]
if _redir:
    reasons.append("it redirects output into a FILE, which prompts even when the bare command IS allowlisted, and an Always allow on one such command does not cover the next, because a stored approval is keyed to the EXACT string and output filenames change. No settings.json entry can retire this, and the form is not needed anyway: this tool already RETURNS stdout and stderr to the session, so run the command bare and read what comes back. If the real problem is that the output is LONG and the interesting line is hidden by the truncation in the middle, filter at the SOURCE instead of capturing everything. To put text in a file, use the Write tool. A null device target, 2>&1, >&2 and INPUT redirection are all still allowed")

# Rule 19. BASH tool only: an UNQUOTED Windows drive path (`C:\...`). Checked in
# `unquoted`, so a QUOTED backslash path is exempt: double quotes preserve the
# separators in Git Bash, so that form at least runs (it still matches no entry,
# but it is not broken). PowerShell is untouched, where backslash is correct.
if tool_name == "Bash" and re.search(r"[A-Za-z]:\\", unquoted):
    reasons.append("it passes an UNQUOTED Windows path with BACKSLASHES on the Bash tool. Git Bash strips those backslashes before the command runs, so the form cannot work at all, whatever the permission list says: git answers \"cannot change to CUsersyouproject\". It also matches no allowlist entry, and NO ENTRY SHOULD BE ADDED, because that would bless a command that is already broken. Use the POSIX spelling on the Bash tool (/c/Users/...), which every entry here is written against. On the PowerShell tool the backslash spelling is the correct one and is never denied by this rule")

# Rule 20. A git command run INSIDE wsl against a /mnt/... path, which is the
# Windows filesystem reached through the WSL bridge. That bridge can serve STALE
# bytes, and git run that way has falsely reported a clean working tree. The
# bridge is what makes the mistake reachable: every Windows repo gains a second,
# worse spelling that looks fine.
if wsl_wrapped and inner_bare == "git" and re.search(r"/mnt/[A-Za-z]/", command):
    reasons.append("it runs git INSIDE wsl against a /mnt/ path, which is the Windows filesystem seen through the WSL bridge. That bridge can serve STALE bytes: git run this way has reported a CLEAN working tree over files that were in fact modified, so the answer looks authoritative and is wrong. It also matches no git entry, since every one of them is written against a Windows or POSIX-on-Windows path. Run git on the WINDOWS side with the forms in docs/command-forms.md: git -C C:/Users/... on PowerShell, or git -C /c/Users/... on Bash. Reserve wsl git for repos that genuinely LIVE in the Linux filesystem")

if not reasons:
    sys.exit(0)

guidance = " ".join([
    "BLOCKED by shell-form-guard: " + "; and ".join(reasons) + ".",
    "This form always prompts and no settings.json entry can ever fix it (rules/claude/permission-loops.md).",
    "Rewrite it from the table in docs/command-forms.md: one command per tool call, absolute paths instead of a cd chain, the absolute venv interpreter instead of a bare python.",
    "Do NOT ask for permission for this command: the session rewrites it.",
    "If the goal was to read or search a file, use the Read or Grep tool instead. Those need no permission at all and are the intended path.",
])

json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": guidance}}, sys.stdout)
' || exit 0
exit 0
