#!/bin/sh
# Case matrix for hooks/shell-form-guard.{sh,ps1}: the committed proof that
# each rule denies what it should and stays out of the way otherwise.
# Each case: expected|tool|command
#
# Paths in the cases below are PLACEHOLDERS (/home/user/..., C:\Users\you\...).
# Every rule keys on the FORM of a command, never on a particular directory, so
# point them at your own repo paths if you prefer; the assertions do not change.
#
# Locate the guard RELATIVE TO THIS SCRIPT. A matrix that hardcodes a path can
# point at nothing, report every case as "allow", and hand back a perfect green
# while testing no guard at all. A test that picks its own target is not
# evidence about production.
GUARD=$(dirname "$0")/../hooks/shell-form-guard.sh
# BOTH mirrors are tested. Testing only one means every green it reports is
# evidence about the platform you are not running on.
GUARD_PS1=$(dirname "$0")/../hooks/shell-form-guard.ps1

# PARSE GATE. The guard body is one single-quoted shell string, so a single
# apostrophe anywhere inside it closes that string early and the rest of the
# file is parsed as shell. The file then exits non-zero with an empty stdout,
# which this harness reads as "allow", so a broken guard shows up as forty
# confusing FAIL lines instead of one true one. It has happened: one apostrophe
# left the .sh mirror unparseable for six days. Fail loudly and first.
if ! bash -n "$GUARD" 2>/dev/null; then
    echo "FATAL: $GUARD does not parse. Every case below would report allow."
    echo "Most likely an apostrophe inside the single-quoted python body:"
    bash -n "$GUARD" 2>&1 | sed "s/^/  /"
    exit 1
fi
PSEXE=$(command -v powershell.exe 2>/dev/null || command -v powershell 2>/dev/null || true)
failures=0

# The harness needs a REAL interpreter, by the same execution probe the guard
# itself uses. A bare `python3` on Git Bash is the Microsoft Store stub: it
# exists, prints nothing, and fails. When that happens the JSON is never built,
# the guard reads empty stdin and exits 0, and every single case comes back
# "allow". Add your own absolute interpreter to the candidate list if the bare
# names do not resolve on your machine.
PY=""
for _cand in \
    /c/Users/you/AppData/Local/Programs/Python/Python313/python.exe \
    python3 python py; do
    _p=$(command -v "$_cand" 2>/dev/null) || continue
    [ -n "$_p" ] || continue
    if [ "$("$_p" -c 'print(9)' 2>/dev/null)" = "9" ]; then PY="$_p"; break; fi
done
if [ -z "$PY" ]; then
    echo "NO WORKING PYTHON FOUND. The matrix cannot run and is reporting"
    echo "nothing rather than a green it did not earn."
    exit 1
fi

payload() {
    printf '%s' "$1" | "$PY" -c '
import json,sys
cmd=sys.stdin.read()
json.dump({"tool_name":"'"$2"'","tool_input":{"command":cmd}},sys.stdout)
'
}

run() {
    expected=$1; tool=$2; cmd=$3
    out=$(payload "$cmd" "$tool" | sh "$GUARD")
    if [ -n "$out" ]; then sh_actual=deny; else sh_actual=allow; fi

    # Both mirrors must agree with each other AND with the expectation. A
    # parity edit that was never executed is not a mirror, it is a guess.
    if [ -n "$PSEXE" ]; then
        out_ps=$(payload "$cmd" "$tool" | "$PSEXE" -NoProfile -ExecutionPolicy Bypass -File "$GUARD_PS1" 2>/dev/null)
        if [ -n "$out_ps" ]; then ps_actual=deny; else ps_actual=allow; fi
    else
        ps_actual=skip
    fi

    if [ "$sh_actual" = "$expected" ] && { [ "$ps_actual" = "$expected" ] || [ "$ps_actual" = skip ]; }; then
        printf 'ok    %-5s sh=%-5s ps=%-5s %-10s %s\n' "$expected" "$sh_actual" "$ps_actual" "$tool" "$cmd"
    else
        printf 'FAIL  want=%-5s sh=%-5s ps=%-5s %-10s %s\n' "$expected" "$sh_actual" "$ps_actual" "$tool" "$cmd"
        failures=$((failures+1))
    fi
}

echo "--- rule 3 retired: cd and ls are FREE ---"
run allow Bash 'cd /home/user/project'
run allow Bash 'cd /home/user/workbench'
run allow Bash 'ls -la'
run allow Bash 'ls /home/user/workbench/queue'
run allow PowerShell 'cd C:/Users/you/claude'
run allow Bash 'cd ..'

echo "--- the compound was always the real defect: still DENIED ---"
run deny Bash 'cd /home/user/project && npm run build:css'
run deny Bash 'cd /home/user/project; ls'
run deny PowerShell 'cd C:/repo && git status'
run deny Bash 'cd /x && cd /y && ls'

echo "--- every other rule unchanged ---"
run deny Bash 'python -c "print(1)"'
run deny Bash 'pytest tests/'
run deny Bash 'pip install requests'
run deny Bash 'npm run build:css'
run deny PowerShell '& "C:/repo/.venv/Scripts/python.exe" -c "print(2)"'
run deny PowerShell 'powershell -NoProfile -Command "Get-ChildItem"'
run deny Bash 'cmd /c dir'
run deny PowerShell 'curl -sS https://example.com'
run deny Bash 'git status
git log'
run deny Bash 'git add -A ; git commit -m "x"'

echo "--- rule 10: a backslash-escaped dollar ---"
run deny PowerShell 'git -C C:/repo commit -m "PowerShell read \$b: as a drive"'
run deny Bash 'grep -c "origin/\${b}" /home/user/workbench/hooks/env-report.ps1'
run deny Bash 'git -C /home/user/workbench log -1 --format="\$x"'
# KNOWN and accepted false positive: this file documents the sequence, so a
# commit message that quotes it is denied too. The rewrite is trivial (say
# "escaped dollar" in words), and the alternative, ignoring quoted spans,
# would blind the rule to the exact place the real cases hid it.
run deny Bash 'git -C /home/user/workbench commit -m "guard: deny \$ in args"'

echo "--- rule 11: backslash-escaped whitespace ---"
run deny Bash '/c/Program\ Files/Git/usr/bin/tail -n 30 /c/Users/you/workbench/notes.md'
run deny Bash '/c/Program\ Files/Git/cmd/git.exe -C /c/Users/you/workbench status --short'
run deny PowerShell 'Get-Content C:/Users/you/My\ Documents/x.md'
# KNOWN and accepted false positive, trivially rewritable: drop the trailing
# backslash. Recorded as a case so nobody rediscovers it as a bug.
run deny PowerShell 'Get-ChildItem C:\Users\you\claude\ -Filter *.md'

echo "--- rule 12: file-inspection command piped, Bash only ---"
run deny Bash 'grep -h "^order:" /c/Users/you/workbench/queue/jobs/*.md | sort -t" " -k2 -n | tail -8'
run deny Bash 'cat /home/user/notes.md | head -5'
run deny Bash 'ls /d/backups/data | sort'
# A pipe inside a quoted regex is TEXT, never a pipeline. False positive guard.
run allow Bash 'grep -rn "origin/main|origin/testing" /home/user/workbench/hooks'
# PowerShell is deliberately OUT of rule 12: this exact form is allowlistable
# and verified silent. Denying it would break a working form.
run allow PowerShell 'Get-ChildItem D:/backups/data -File | Select-Object Name,Length,LastWriteTime'
run allow PowerShell 'Get-ChildItem C:/Users/you/workbench/rules -File | Select-Object Name,Length'

echo "--- rule 13: git commit with no -- pathspec ---"
run deny Bash 'git -C /home/user/workbench commit -m "queue: claim a card"'
run deny PowerShell 'git -C C:/Users/you/workbench commit -m "permissions: add an entry"'
run deny Bash 'git commit -am "sweep everything"'
run allow Bash 'git -C /home/user/workbench commit -m "queue: claim a card" -- queue/inbox.md'
# Carve-out: the documented message-body form has no pathspec by design
# (docs/command-forms.md). Denying it would break a prescribed command.
run allow Bash 'git -C /home/user/workbench commit --amend -F /tmp/msg.txt'
# A commit MESSAGE that talks about committing is text, not a pathspec-less
# commit. Quoted spans are stripped before this rule looks.
run allow Bash 'git -C /home/user/workbench log --format="commit %H" -- hooks/'

echo '--- rule 14: JSON escaping leaked into a command ---'
# The real case: a lint command copied out of settings.json with every
# separator doubled.
run deny PowerShell 'C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe C:\\Users\\you\\workbench\\tools\\lint.py'
# The QUADRUPLED spelling, which turns up when a session copies out of a file
# that was itself already JSON-escaped.
run deny Bash 'C:\\\\Users\\\\you\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\Python313\\\\python.exe C:\\\\Users\\\\you\\\\workbench\\\\tools\\\\lint.py'
run deny PowerShell 'git -C C:\\Users\\you\\workbench status --short'
# NOT false positives: a UNC path has no drive letter, and the correct single
# separator form is the one docs/command-forms.md prescribes.
run allow PowerShell 'Get-ChildItem \\server\share\backups'
run allow PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe C:\Users\you\workbench\tools\lint.py'

echo "--- rule 15: in-place stream edit ---"
run deny Bash 'sed -i "334,417d" /c/Users/you/workbench/queue/inbox.md'
run deny Bash 'sed -i.bak s/a/b/ /home/user/workbench/queue/inbox.md'
run deny Bash 'sed --in-place s/a/b/ /home/user/notes.md'
run deny Bash 'perl -pi -e s/a/b/ /home/user/notes.md'
run deny PowerShell 'sed -i "1,5d" C:\Users\you\workbench\queue\inbox.md'
run deny Bash '/usr/bin/sed -i "1d" /home/user/notes.md'

echo "--- rule 16: bare .NET type literal on PowerShell ---"
run deny PowerShell '[scriptblock]::Create((Get-Content -Raw "C:\Users\you\workbench\hooks\env-report.ps1")).GetType().Name'
run deny PowerShell '[System.IO.File]::ReadAllText("C:\Users\you\workbench\settings.json")'
# NOT a false positive: on Bash a leading [ is the test builtin, never denied.
run allow Bash '[ -f /home/user/workbench/settings.json ]'
# The sanctioned replacements, both allowlistable.
run allow PowerShell 'C:\Users\you\workbench\tools\parse-check.ps1 C:\Users\you\workbench\hooks\env-report.ps1'
run allow Bash 'bash -n /c/Users/you/workbench/hooks/env-report.sh'

echo "--- rule 17: bash <script> instead of the bare path ---"
run deny Bash 'bash /c/Users/you/workbench/tools/test-nudge-matrix.sh'
run deny Bash 'bash tools/guard-matrix.sh'
# The bare form is the fix, and it is the form that actually carries an entry.
run allow Bash '/c/Users/you/workbench/tools/test-nudge-matrix.sh'
# NOT a false positive: the .sh syntax check is a flag, and it is allowlistable.
run allow Bash 'bash -n /c/Users/you/workbench/hooks/test-nudge.sh'
# PowerShell is untouched by rule 17.
run allow PowerShell 'C:\Users\you\workbench\hooks\test-nudge.ps1'

echo "--- rule 18: output redirect to a file ---"
# The real cases. The bare lint is allowlisted in every spelling; these prompt
# anyway, and an Always allow on the first does not cover the second, which
# differs only in the output filename.
run deny PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe C:\Users\you\workbench\tools\lint.py > C:\Users\you\AppData\Local\Temp\lint.txt 2>&1'
run deny PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe C:\Users\you\workbench\tools\lint.py > C:\Users\you\AppData\Local\Temp\lint2.txt 2>&1'
run deny Bash '/c/Users/you/AppData/Local/Programs/Python/Python313/python.exe /c/Users/you/workbench/tools/lint.py > /tmp/lint.txt'
run deny Bash 'git -C /home/user/workbench diff >> /tmp/diff.txt'
run deny Bash 'ls /home/user &> /tmp/out.txt'
# NOT false positives. A null device is fine, and so is 2>&1 with no file:
# denying redirections outright was already caught as a false positive once
# (rule 6), and this rule must not reintroduce it.
run allow Bash 'ls /home/user 2>/dev/null'
run allow PowerShell 'C:\Users\you\workbench\tools\parse-check.ps1 C:\Users\you\workbench\hooks\env-report.ps1 2>$null'
run allow Bash 'grep -rn "cd" /home/user/workbench/hooks 2>&1'
# INPUT redirection is the allowed database-import form. Never denied.
run allow Bash 'docker exec -i appdb sh -c "mysql -u root appdb" < /c/Users/you/project/migrations/001.sql'
# A `>` inside a quoted commit message is TEXT, not a redirect.
run allow Bash 'git -C /home/user/workbench commit -m "guard: deny > to a file" -- hooks/shell-form-guard.sh'
# The fix for those cases: the same command, bare. This is the form that
# actually carries the entry, and it returns its output to the session anyway.
run allow PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe C:\Users\you\workbench\tools\lint.py'

echo "--- rule 19: unquoted C:\\ path on the BASH tool ---"
# Measured: on PowerShell this exact command is SILENT, on Bash it prompts, and
# Git Bash mangles the path so it could not have worked even once approved.
run deny Bash 'git -C C:\Users\you\project commit -m "One word per concept" -- docs/naming-sheet.md'
run deny Bash 'git -C C:\Users\you\project log -1 --oneline'
# The POSIX spelling is the fix and it is what every Bash entry is written for.
run allow Bash 'git -C /c/Users/you/project log -1 --oneline'
run allow Bash 'git -C C:/Users/you/project log -1 --oneline'
# QUOTED is exempt: double quotes preserve the separators, so that form runs.
run allow Bash 'git -C "C:\Users\you\project" log -1 --oneline'
# PowerShell is untouched, where backslash is the CORRECT spelling.
run allow PowerShell 'git -C C:\Users\you\project commit --dry-run -m "One word per concept" -- docs/naming-sheet.md tests/unit/test_naming_sheet.py'

echo "--- rule 21: interpreter handed inline code, at ANY path spelling ---"
# The form the owner was prompted on, 2026-08-21. Rules 4 and 9 keyed on the BARE
# first word, so the ABSOLUTE spelling of the same command slipped the guard.
run deny PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe -c "import json,os; root=r(C:\Users\you\workbench); print(root)"'
run deny Bash '/c/Users/you/AppData/Local/Programs/Python/Python313/python.exe -c "import json; print(1)"'
run deny Bash '/home/user/project/.venv/bin/python -c "print(1)"'
run deny PowerShell 'C:/Users/you/workbench/.venv/Scripts/python.exe -c "print(1)"'
run deny Bash 'sh -c "ls /home/user"'
run deny Bash 'node -c "console.log(1)"'
# `-e` is deliberately NOT covered: no instance has cost a click yet.
run allow Bash 'node -e "console.log(1)"'
echo "--- ...and the ALLOWED interpreter forms must survive it ---"
run allow PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe C:\Users\you\workbench\tools\lint.py'
run allow Bash '/c/Users/you/AppData/Local/Programs/Python/Python313/python.exe /c/Users/you/workbench/tools/lint.py'
run allow Bash '/c/Users/you/AppData/Local/Programs/Python/Python313/python.exe -m json.tool /c/Users/you/workbench/settings.json'
run allow Bash '/home/user/project/.venv/bin/python -m pytest /home/user/project/tests'
run allow Bash 'bash -n /c/Users/you/workbench/hooks/env-report.sh'
# The flag is looked for in the UNQUOTED remainder, so a -c belonging to a
# NON-interpreter, or sitting inside a payload, must never fire this rule.
run allow Bash 'grep -c origin /home/user/workbench/hooks/urgent-print.sh'
run allow PowerShell 'git -C C:/Users/you/workbench log -1 --format="%h -c"'
# An allowed database-import form that legitimately contains `sh -c`: the rule
# keys on the FIRST word, which is docker, so it must stay allowed.
run allow Bash 'docker exec -i appdb sh -c "mysql -u root appdb" < /c/Users/you/project/migrations/001.sql'

echo "--- allowed forms stay allowed (no new false positives) ---"
run allow Bash 'sed -n "1,20p" /home/user/notes.md'
run allow Bash 'grep -i origin /home/user/workbench/hooks/urgent-print.sh'
run allow Bash 'git -C /home/user/workbench commit -m "sed -i is denied now" -- hooks/shell-form-guard.sh'
run allow Bash 'tail -n 30 /c/Users/you/workbench/notes.md'
run allow PowerShell 'git -C C:\Users\you\workbench status --short'
run allow PowerShell 'C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe C:\Users\you\workbench\tools\lint.py'
run allow Bash 'git -C /c/Users/you/workbench commit -m "guard: deny escaped whitespace in a command" -- hooks/shell-form-guard.sh'
run allow Bash 'git -C /home/user/workbench commit -m "rule 10 denies an escaped dollar sigil" -- hooks/shell-form-guard.sh'
run allow Bash 'grep -rn "origin" /home/user/workbench/hooks'
run allow Bash 'git -C /home/user/workbench status --short'
run allow Bash 'ls /home/user 2>&1'
run allow Bash 'grep -rn "cd" /home/user/workbench/hooks'
run allow Bash 'curl -sS https://example.com'
run allow Bash '/home/user/project/.venv/bin/python -m pytest'
run allow Bash 'npm --prefix /home/user/project run build:css'
run allow Bash 'git -C /home/user/project commit -m "fix && polish" -- app/main.py'
run allow Bash 'python3 -m venv .venv'
run allow PowerShell 'Get-Culture'

echo
echo "--- wsl is a WRAPPER: the wrong-tool rules must see INSIDE it ---"
run deny  PowerShell 'wsl sed -i "1d" /home/you/.config/nvim/init.lua'
run deny  PowerShell 'wsl perl -pi -e s/a/b/ /home/you/.config/nvim/init.lua'
run deny  PowerShell 'wsl rg "require" /home/you/.config/nvim | head -20'
run deny  PowerShell 'wsl cat /home/you/.zshrc | head -5'
run deny  PowerShell 'wsl -u you grep -r "lazy" /home/you/.config/nvim | sort'
run deny  PowerShell 'wsl git -C /home/you/.config/nvim commit -m "no pathspec"'
run deny  PowerShell 'wsl ls /home/you && wsl ls /tmp'
echo "--- ...but rules whose premise is \"matches no entry\" must NOT fire inside wsl ---"
echo "--- (a blanket PowerShell(wsl:*) covers every verb, so firing them would be a FALSE BLOCK) ---"
run allow PowerShell 'wsl python3 -m pytest /home/you/proj'
run allow PowerShell 'wsl pip install ruff'
run allow PowerShell 'wsl npm run build'
run allow PowerShell 'wsl bash /home/you/scripts/setup.sh'
run allow PowerShell 'wsl curl -sS https://example.com'
echo "--- a Linux-side toolchain, all allowed ---"
run allow PowerShell 'wsl env NVIM_APPNAME=nvim nvim --headless "+Lazy! sync" +qa'
run allow PowerShell 'wsl go version'
run allow PowerShell 'wsl ruff check /home/you/proj'
run allow PowerShell 'wsl basedpyright --outputjson /home/you/proj'
run allow PowerShell 'wsl markdownlint /home/you/.config/nvim/README.md'
run allow PowerShell 'wsl rg "require" /home/you/.config/nvim'
run allow PowerShell 'wsl git -C /home/you/.config/nvim commit -m "one line" -- init.lua'
run allow PowerShell 'wsl -d Ubuntu ls -la /home/you/.config'
run allow PowerShell 'wsl chmod -R 644 /home/you/.config/nvim'
echo "--- rule 20: git inside wsl against /mnt (the bridge serves STALE bytes) ---"
run deny  PowerShell 'wsl git -C /mnt/c/Users/you/workbench status --short'
run deny  PowerShell 'wsl git -C /mnt/c/Users/you/project log -1 --oneline'
run allow PowerShell 'wsl git -C /home/you/.config/nvim status --short'
run allow PowerShell 'git -C C:/Users/you/workbench status --short'
run allow PowerShell 'wsl cp -r /mnt/c/Users/you/session/working/nvim /home/you/.config/nvim'
echo "--- and the wrapper must not leak into NON-wsl PowerShell (no false positives) ---"
run allow PowerShell 'Get-ChildItem C:/Users/you/claude -File | Select-Object Name'
# Pinned as DENY after this case was written expecting allow and failed. Not a
# regression and nothing to do with wsl: `bare` strips a `.exe` suffix on
# purpose, so `curl.exe` reaches rule 8 the same as `curl`. Arguably a false
# block, since `curl.exe` really is the genuine curl and not the
# Invoke-WebRequest alias that rule 8 was written against. Left alone
# deliberately: the blessed path for HTTP is the Bash tool (`Bash(curl:*)`),
# which the command table already prescribes, so nothing is actually blocked
# that has anywhere else to go.
run deny  PowerShell 'curl.exe -sS https://example.com'

echo "failures=$failures"
