#!/usr/bin/env bash
# Stop hook: nudge when an app's code changed and no test changed with it.
# Scope is DECLARED here and never inferred at runtime: ONE repo, code is
# app/ and templates/, tests are tests/. Read-only, git plumbing only, it
# edits nothing.
#
# OPT-IN. Set DECLARED_REPO below to the absolute path of the ONE repo this
# machine should watch; while it is empty the hook does nothing at all. Point
# it at a repo rather than deriving one at runtime: a watcher that works out
# its own scope is a watcher that nags about the wrong tree.
#
# Nudges by exiting 2 (stderr goes back to Claude as feedback), the same way
# queue-nudge and job-email-nudge do, and only when the set of changed code
# files DIFFERS from the last nudge in this session: that reports every new
# change without nagging about one it already reported. macOS/Linux side;
# Windows runs the .ps1 counterpart (settings.json wires up both and each
# self-selects by platform).
#
# TEST_NUDGE_REPO overrides the watched repo for the case matrix and lifts the
# platform gate with it, the same way WORKBENCH_CONF works for queue-nudge. It
# cannot widen what this hook DOES: every path below is read-only.

DECLARED_REPO=

REPO="${TEST_NUDGE_REPO:-}"
if [ -z "$REPO" ]; then
    case "$(uname)" in
        MINGW*|MSYS*|CYGWIN*) exit 0 ;;
    esac
    REPO="$DECLARED_REPO"
fi
[ -n "$REPO" ] || exit 0

INPUT="$(cat)"

# Built-in loop guard: if a stop hook already forced a continuation this turn,
# never block again.
printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0

json_str() {
    printf '%s' "$INPUT" | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

[ -d "$REPO/.git" ] || exit 0

# Uncommitted first (tracked edits, staged or not, plus new files). If the tree
# is clean, fall back to the last commit, so a session that already committed
# still gets the nudge.
changed="$(
    git -C "$REPO" diff --name-only HEAD 2>/dev/null
    git -C "$REPO" ls-files --others --exclude-standard 2>/dev/null
)"
changed="$(printf '%s\n' "$changed" | sed '/^$/d')"
if [ -z "$changed" ]; then
    git -C "$REPO" rev-parse --verify HEAD~1 >/dev/null 2>&1 || exit 0
    changed="$(git -C "$REPO" diff --name-only HEAD~1 HEAD 2>/dev/null | sed '/^$/d')"
fi
[ -n "$changed" ] || exit 0

code="$(printf '%s\n' "$changed" | grep -E '^(app|templates)/' | sort -u)"
[ -n "$code" ] || exit 0
printf '%s\n' "$changed" | grep -qE '^tests/' && exit 0

SESSION_ID="$(json_str session_id)"
[ -n "$SESSION_ID" ] || SESSION_ID=nosession
# The mirror name is in the marker deliberately: on Windows, Git Bash's /tmp IS
# %TEMP%, so a shared marker would let whichever mirror ran first silence the
# other. Only one mirror runs per platform in production, but the case matrix
# runs both, and a test that silences half of what it is testing reports a
# green it did not earn.
MARKER="${TMPDIR:-/tmp}/test-nudge-sh-$SESSION_ID"
if [ -f "$MARKER" ] && [ "$(cat "$MARKER" 2>/dev/null)" = "$code" ]; then
    exit 0
fi
printf '%s' "$code" > "$MARKER"

count="$(printf '%s\n' "$code" | wc -l | tr -d ' ')"
echo "$count file(s) under app/ or templates/ changed with nothing under tests/. Add a test for the behaviour that changed, or state in the card Report why this change does not need one. Declared scope, so this is not a guess about other repos: hooks/test-nudge.sh watches one checkout and those two directories." >&2
exit 2
