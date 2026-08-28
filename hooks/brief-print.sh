#!/usr/bin/env bash
# brief-print.sh: gather once, print once, at session start.
#
# POSIX mirror of brief-print.ps1. See that file for the reasoning; the short
# version is that a session should read one prepared artifact instead of
# rediscovering the machine, and it should be told loudly when that artifact is
# missing or stale rather than quietly starting blind.
#
# Paths come from workbench.conf, never hardcoded: this file is published in a
# public repository, so an absolute path here would publish a username.
# `tools/brief.py` is part of the maintainer's private tooling and is not
# shipped here, so this hook no-ops cleanly when it is absent rather than
# printing an error at every session start.
set -u

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[ -f "$CONF" ] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[ -n "$REPO" ] || exit 0

BRIEF="$REPO/tools/brief.py"
# The gathered artifact lands beside the repo, not inside it: it is machine
# state, not versioned content.
OUT="$(dirname "$REPO")/state/brief.toon"

# Discover the interpreter rather than naming one. Same approach as env-report.
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

[ -n "$PYTHON" ] && [ -f "$BRIEF" ] || exit 0

"$PYTHON" "$BRIEF" --no-cost --quiet >/dev/null 2>&1

if [ ! -f "$OUT" ]; then
    echo "[brief] GATHER FAILED and no previous brief exists."
    echo "[brief] Start blind, or run: $PYTHON $BRIEF"
    exit 0
fi

now=$(date +%s)
mt=$(stat -c %Y "$OUT" 2>/dev/null || echo "$now")
age=$(( (now - mt) / 60 ))
if [ "$age" -gt 10 ]; then
    echo "[brief] STALE: this brief is ${age} minutes old, so the gather did not run just now."
    echo "[brief] Treat everything below as history, not state."
fi

# The age check alone misses the case where the artifact was rewritten just now
# but its REMOTE half failed, which reads as fresh and is half blind.
if grep -q "remote_gathered: false" "$OUT" 2>/dev/null; then
    echo "[brief] REMOTE GATHER FAILED just now. Everything below is HOST-SIDE ONLY."
    echo "[brief] Check that the automation service is up before trusting remote state."
fi

echo "[brief] machine + remote state, gathered on demand. Full file: $OUT"
cat "$OUT"
