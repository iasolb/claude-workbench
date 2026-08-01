#!/usr/bin/env bash
# Stop hook: nudge when real work happened this session but queue/current.md
# never got touched, so the arc state gets summarized into the queue before
# the session wraps instead of going stale. Nudges by exiting 2 (stderr goes
# back to Claude as feedback) at most once per session; every other path
# exits 0 silently. macOS/Linux side; Windows runs the .ps1 counterpart
# (settings.json wires up both and each self-selects by platform).

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

INPUT="$(cat)"

# Built-in loop guard: if a stop hook already forced a continuation this
# turn, never block again.
printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0

json_str() {
    printf '%s' "$INPUT" | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

# WORKBENCH_CONF overrides the conf location (mainly for testing).
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
CURRENT="$REPO/queue/current.md"
[[ -f "$CURRENT" ]] || exit 0

# Small sessions don't owe the queue an update.
TRANSCRIPT="$(json_str transcript_path)"
[[ -f "$TRANSCRIPT" ]] || exit 0
[[ "$(wc -c < "$TRANSCRIPT")" -ge 20000 ]] || exit 0

# Queue touched in the last 4 hours = someone is already keeping it current.
[[ -n "$(find "$CURRENT" -mmin -240 2>/dev/null)" ]] && exit 0

# At most one nudge per session.
SESSION_ID="$(json_str session_id)"
[[ -n "$SESSION_ID" ]] || exit 0
MARKER="${TMPDIR:-/tmp}/queue-nudge-$SESSION_ID"
[[ -e "$MARKER" ]] && exit 0
touch "$MARKER"

echo "queue/current.md has not been updated this session. Summarize where the current arc stands into it (and adjust queue/global.md if priorities moved or an item completed), then stop." >&2
exit 2
