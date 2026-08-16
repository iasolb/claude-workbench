#!/usr/bin/env bash
# SessionStart + SessionEnd hook: append one row per event to
# timelog/sessions.csv in the repo, as the raw material for tracking
# hours worked per project (added 2026-07-25, for contract hours).
# Attribution comes from cwd; hours come from pairing
# start/end rows by session_id at analysis time. macOS/Linux side; Windows
# runs the .ps1 counterpart (each side self-selects by platform). Reads
# ~/.claude/workbench.conf (WORKBENCH_CONF overrides, mainly for testing).
# Always exits 0: a broken log must never block a session.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO" ]] || exit 0

PAYLOAD="$(cat)"
# Pull one string field out of the flat hook JSON without depending on jq.
field() {
    printf '%s' "$PAYLOAD" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}
EVENT="$(field hook_event_name)"; [[ -n "$EVENT" ]] || EVENT="unknown"
SOURCE="$(field source)"
SID="$(field session_id)"
CWD="$(field cwd)"; [[ -n "$CWD" ]] || CWD="$PWD"
# permission_mode is the effective mode this session is running in. Logged so
# the mode is a committed fact instead of something a session infers from
# settings.json (rules/claude/permission-loops.md). Blank means the payload did not
# carry it, which is a statement about the payload, never about the mode.
PMODE="$(field permission_mode)"

DIR="$REPO/timelog"
CSV="$DIR/sessions.csv"
mkdir -p "$DIR" || exit 0
[[ -f "$CSV" ]] || echo "ts,event,source,machine,session_id,cwd,permission_mode" > "$CSV"

TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"
echo "$TS,$EVENT,$SOURCE,$(hostname -s),$SID,\"${CWD//\"/\"\"}\",$PMODE" >> "$CSV"
exit 0
