#!/usr/bin/env bash
# brief-print.sh: gather once, print once, at session start.
#
# POSIX mirror of brief-print.ps1. See that file for the reasoning; the short
# version is that a session should read one prepared artifact instead of
# rediscovering the machine, and it should be told loudly when that artifact is
# missing or stale rather than quietly starting blind.
set -u

# All three paths come from configuration, following the REPO_PATH pattern the
# other hooks use. The repo (from REPO_PATH) fixes where brief.py lives and
# where the state landing zone is (a sibling of the repo, outside any git
# tree); the interpreter (PYTHON_PATH) is machine-specific and never assumed.
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[ -f "$CONF" ] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
PYTHON="$(sed -n 's/^PYTHON_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[ -n "$REPO" ] || exit 0
[ -n "$PYTHON" ] || exit 0

. "$(dirname "${BASH_SOURCE[0]}")/portable.sh"

BRIEF="$REPO/tools/brief.py"
OUT="$REPO/../state/brief.toon"

# BLOCKERS AND WHAT CHANGED, NOT THE WHOLE STATE (Ian, 2026-09-10, choosing
# between three shapes). This used to print the entire artifact, about 90 lines
# of absolute state, and the two most expensive facts of that evening were in
# none of it: a scheduled task had fired two drivers ten minutes earlier, and
# the executor allowance was fully spent. Absolute state also cannot say what
# is NEW, which is how a week of repeated work stayed invisible.
#
# The full artifact is still written and the headline points at it.
"$PYTHON" "$BRIEF" --no-cost --headline

if [ ! -f "$OUT" ]; then
    echo "[brief] GATHER FAILED and no previous brief exists."
    echo "[brief] Start blind, or run: $PYTHON $BRIEF"
    exit 0
fi

now=$(date +%s)
mt=$(file_mtime "$OUT")
# mtime 0 means the file could not be stat'd at all; do not call that fresh.
[ "$mt" -eq 0 ] && mt=0
age=$(( (now - mt) / 60 ))
if [ "$age" -gt 10 ]; then
    echo "[brief] STALE: the artifact is ${age} minutes old, so the gather above did not actually run."
    echo "[brief] Treat the headline as history, not state."
fi
