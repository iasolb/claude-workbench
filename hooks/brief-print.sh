#!/usr/bin/env bash
# brief-print.sh: gather once, print once, at session start.
#
# POSIX mirror of brief-print.ps1. See that file for the reasoning; the short
# version is that a session should read one prepared artifact instead of
# rediscovering the machine, and it should be told loudly when that artifact is
# missing or stale rather than quietly starting blind.
set -u

PYTHON="/c/Users/ians0/AppData/Local/Programs/Python/Python313/python.exe"
BRIEF="/c/Users/ians0/Documents/ai/ai-memory-bank/tools/brief.py"
OUT="/c/Users/ians0/Documents/ai/state/brief.toon"

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

echo "[brief] machine + remote state, gathered on demand. Full file: $OUT"
cat "$OUT"
