#!/usr/bin/env bash
# PreToolUse guard: after writing a reentry handoff, this session is finished.
# The PowerShell companion is handoff-is-final.ps1; keep both behaviors aligned.
#
# Scope:
#   reads   : the hook JSON payload; one marker; git branch and unpushed commits
#             in WORKBENCH_ROOT
#   writes  : one marker per session under state/handoff-sessions/
#   decides : deny an unpushed handoff or a state-changing call after handoff
#   network : none
#
# WORKBENCH_ROOT defaults to the parent of this hook's directory. Set it when
# the hook is installed outside the repository. The marker directory defaults
# to <WORKBENCH_ROOT>/state/handoff-sessions. Malformed input fails open.

set -u
RAW="$(cat 2>/dev/null || true)"
[ -n "$RAW" ] || exit 0

PY=""
for candidate in python3 python; do
    "$candidate" -c 'import sys' >/dev/null 2>&1 && { PY="$candidate"; break; }
done
[ -n "$PY" ] || exit 0

HOOK_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${WORKBENCH_ROOT:-$(dirname -- "$HOOK_DIR")}"
STATE_ROOT="${WORKBENCH_STATE_DIR:-$REPO_ROOT/state}"
MARKER_DIR="$STATE_ROOT/handoff-sessions"

FIELDS="$(printf '%s' "$RAW" | "$PY" -c '
import json, re, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
session = str(payload.get("session_id") or "")
if not session:
    sys.exit(0)
tool = str(payload.get("tool_name") or "")
tool_input = payload.get("tool_input") or {}
target = str(tool_input.get("file_path") or "") if isinstance(tool_input, dict) else ""
safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)
handoff = tool in ("Write", "Edit", "NotebookEdit") and bool(
    re.search(r"[\\/]reentry-[^\\/]*\.md$", target, re.I))
print(safe); print(tool); print(target); print(int(handoff))
' 2>/dev/null || true)"
[ -n "$FIELDS" ] || exit 0

SAFE_ID="$(printf '%s\n' "$FIELDS" | sed -n '1p')"
TOOL="$(printf '%s\n' "$FIELDS" | sed -n '2p')"
TARGET="$(printf '%s\n' "$FIELDS" | sed -n '3p')"
IS_HANDOFF="$(printf '%s\n' "$FIELDS" | sed -n '4p')"
[ -n "$SAFE_ID" ] || exit 0
MARKER="$MARKER_DIR/$SAFE_ID.done"

emit_deny() {
    printf '%s' "$1" | "$PY" -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": sys.stdin.read()}}))
' 2>/dev/null || true
    exit 0
}

if [ "$IS_HANDOFF" = "1" ]; then
    branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    ahead=""
    if [ -n "$branch" ]; then
        ahead="$(git -C "$REPO_ROOT" log "origin/$branch..HEAD" --oneline 2>/dev/null | wc -l | tr -d ' ')"
        [ "$ahead" = "0" ] && ahead=""
    fi
    if [ -n "$ahead" ]; then
        emit_deny "BLOCKED: the handoff must be the final step, after preparation. $ahead commit(s) on this branch are not pushed, so the next session would not have this work. Push the branch, then write the handoff again as the final action. Do not work around this by renaming the file."
    fi
    mkdir -p "$MARKER_DIR" 2>/dev/null || true
    printf '%s' "$TARGET" >"$MARKER" 2>/dev/null || true
    exit 0
fi

[ -f "$MARKER" ] || exit 0
case "$TOOL" in
    Write|Edit|NotebookEdit|Bash|PowerShell) ;;
    *) exit 0 ;;
esac

HANDED_TO="$(cat "$MARKER" 2>/dev/null || true)"
emit_deny "BLOCKED: this session already wrote its handoff and is finished. It wrote $HANDED_TO. Further changes would make the handoff stale. Stop here and let the next session continue. Read-only tools remain available. To resume deliberately, remove this marker: $MARKER"
