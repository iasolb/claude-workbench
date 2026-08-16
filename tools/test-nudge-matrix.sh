#!/bin/sh
# Case matrix for hooks/test-nudge.{sh,ps1}: the committed proof that the
# nudge fires when it should and stays silent when it should not. Modelled on
# tools/guard-matrix.sh, and it inherits that file's two hard-won rules:
#
#   1. BOTH mirrors run, and both must agree with the expectation. A parity
#      edit that was never executed is not a mirror, it is a guess.
#   2. The hooks are located RELATIVE TO THIS SCRIPT, so the matrix cannot
#      quietly test nothing (a matrix that hardcodes a path can point at a
#      directory that does not exist and report a perfect green).
#
# Fixtures are throwaway git repos under a temp dir, handed to the hooks via
# TEST_NUDGE_REPO, so this runs green even when test-nudge has no repo declared
# (which is its default state in this template). Nothing here touches a real
# checkout.

# Resolved absolutely, with no ".." left in them: powershell.exe -File is given
# this path through the MSYS path translation, and a translated path still
# carrying ".." is how the .ps1 half of a matrix silently tests nothing (every
# case comes back silent, which is a green it did not earn).
HOOK_DIR=$(cd "$(dirname "$0")/../hooks" && pwd)
HOOK_SH=$HOOK_DIR/test-nudge.sh
HOOK_PS1=$HOOK_DIR/test-nudge.ps1
PSEXE=$(command -v powershell.exe 2>/dev/null || command -v powershell 2>/dev/null || true)
failures=0

[ -f "$HOOK_SH" ] || { echo "MISSING $HOOK_SH, refusing to report a green it did not earn"; exit 1; }
[ -f "$HOOK_PS1" ] || { echo "MISSING $HOOK_PS1, refusing to report a green it did not earn"; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# Unique per invocation: the per-session markers live in the temp dir and would
# otherwise make a second run of this matrix look silent (a false green).
SESS=$$-$(date +%s)

GIT="git -c user.email=matrix@example.invalid -c user.name=matrix"

mkrepo() { # $1 = name, returns path on stdout
    d="$WORK/$1"
    mkdir -p "$d/app" "$d/templates" "$d/tests" "$d/docs"
    echo x > "$d/app/base.py"
    echo x > "$d/templates/base.html"
    echo x > "$d/tests/test_base.py"
    echo x > "$d/docs/readme.md"
    $GIT init -q "$d" >/dev/null 2>&1
    $GIT -C "$d" add -A >/dev/null 2>&1
    $GIT -C "$d" commit -q -m init >/dev/null 2>&1
    echo "$d"
}

run() { # $1 expected(nudge|silent) $2 session-suffix $3 stop_hook_active $4 repo $5 label
    expected=$1; suffix=$2; stopactive=$3; repo=$4; label=$5
    json=$(printf '{"session_id":"%s","stop_hook_active":%s,"transcript_path":""}' "$SESS-$suffix" "$stopactive")

    out_sh=$(printf '%s' "$json" | TEST_NUDGE_REPO="$repo" sh "$HOOK_SH" 2>&1)
    if [ -n "$out_sh" ]; then sh_actual=nudge; else sh_actual=silent; fi

    if [ -n "$PSEXE" ]; then
        repo_win=$(cygpath -w "$repo" 2>/dev/null || echo "$repo")
        out_ps=$(printf '%s' "$json" | TEST_NUDGE_REPO="$repo_win" "$PSEXE" -NoProfile -ExecutionPolicy Bypass -File "$HOOK_PS1" 2>&1)
        if [ -n "$out_ps" ]; then ps_actual=nudge; else ps_actual=silent; fi
    else
        ps_actual=skip
    fi

    if [ "$sh_actual" = "$expected" ] && { [ "$ps_actual" = "$expected" ] || [ "$ps_actual" = skip ]; }; then
        printf 'ok    %-6s sh=%-6s ps=%-6s %s\n' "$expected" "$sh_actual" "$ps_actual" "$label"
    else
        printf 'FAIL  want=%-6s sh=%-6s ps=%-6s %s\n' "$expected" "$sh_actual" "$ps_actual" "$label"
        failures=$((failures+1))
    fi
}

# 1. app/ changed, nothing under tests/ (the whole point)
R=$(mkrepo case1); echo change >> "$R/app/base.py"
run nudge c1 false "$R" "app/ changed, no test changed"

# 2. app/ changed AND tests/ changed: the session did its job
R=$(mkrepo case2); echo change >> "$R/app/base.py"; echo change >> "$R/tests/test_base.py"
run silent c2 false "$R" "app/ and tests/ both changed"

# 3. clean tree, and the last commit touched no declared code dir
R=$(mkrepo case3); echo change >> "$R/docs/readme.md"
$GIT -C "$R" commit -q -am docs >/dev/null 2>&1
run silent c3 false "$R" "clean tree, last commit was docs only"

# 4. changes only OUTSIDE the declared dirs
R=$(mkrepo case4); echo change >> "$R/docs/readme.md"
run silent c4 false "$R" "only docs/ changed, outside declared scope"

# 5. already committed this turn: tree clean, HEAD vs HEAD~1 carries it
R=$(mkrepo case5); echo change >> "$R/app/base.py"
$GIT -C "$R" commit -q -am code >/dev/null 2>&1
run nudge c5 false "$R" "committed already, clean tree, code in last commit"

# 6. templates/ counts as code too
R=$(mkrepo case6); echo change >> "$R/templates/base.html"
run nudge c6 false "$R" "templates/ changed, no test changed"

# 7. a brand new untracked file under app/ counts
R=$(mkrepo case7); echo new > "$R/app/added.py"
run nudge c7 false "$R" "new untracked file under app/"

# 8. not a git repo at all (the state of any machine with no such checkout)
mkdir -p "$WORK/notarepo"
run silent c8 false "$WORK/notarepo" "declared path is not a git repo"

# 9. the loop guard: never nudge twice inside one turn
R=$(mkrepo case9); echo change >> "$R/app/base.py"
run silent c9 true "$R" "stop_hook_active true, loop guard holds"

# 10 + 11. same session, unchanged file set: report once, then stay quiet
R=$(mkrepo case10); echo change >> "$R/app/base.py"
run nudge c10 false "$R" "first nudge in a session"
run silent c10 false "$R" "same session, same files, no second nudge"

# 12. same session again, but the change GREW: that is new information
echo new > "$R/app/second.py"
run nudge c10 false "$R" "same session, a further file changed, nudges again"

echo
echo "failures=$failures"
[ "$failures" -eq 0 ] || exit 1
