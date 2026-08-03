#!/usr/bin/env bash
# Notification hook, macOS/Linux side (notify.ps1 is the Windows
# counterpart, same stdin JSON contract). Two jobs:
#   1. Local desktop notification (osascript / notify-send).
#   2. One-way ntfy push to the phone when a session is WAITING on the
#      user (permission prompt / needs input), so they know to open the
#      Claude app and answer there. Notify-only by design: no action
#      buttons, no response topic, approvals only ever happen in the
#      app UI.
# Topics come from ~/.claude/ntfy-topics.json (never committed): uses the
# "request" topic and optional "server" (default https://ntfy.sh).
# Always exits 0: a broken notifier must never block a session.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

INPUT=$(cat)
TYPE=$(echo "$INPUT" | jq -r '.notification_type // ""' 2>/dev/null)

WAITING=0
case "$TYPE" in
    permission_prompt)  MESSAGE="Waiting on an approval"; WAITING=1 ;;
    idle_prompt)        MESSAGE="Waiting on you"; WAITING=1 ;;
    agent_needs_input)  MESSAGE="Waiting on your input"; WAITING=1 ;;
    agent_completed)    MESSAGE="Task finished" ;;
    *)                  MESSAGE="Notification" ;;
esac

# Phone push, waiting states only. Completions travel by email instead.
CFG="$HOME/.claude/ntfy-topics.json"
if [[ "$WAITING" == "1" && -f "$CFG" ]]; then
    TOPIC=$(jq -r '.request // ""' "$CFG" 2>/dev/null)
    SERVER=$(jq -r '.server // "https://ntfy.sh"' "$CFG" 2>/dev/null)
    if [[ -n "$TOPIC" ]]; then
        curl -s -m 10 -X POST "$SERVER" \
            -H "Content-Type: application/json" \
            -d "{\"topic\":\"$TOPIC\",\"title\":\"Claude Mac: $MESSAGE\",\"message\":\"Open the Claude app to respond.\",\"priority\":4,\"tags\":[\"hourglass_flowing_sand\"]}" \
            >/dev/null 2>&1 || true
    fi
fi

if [[ "$(uname)" == "Darwin" ]]; then
    osascript -e "display notification \"$MESSAGE\" with title \"Claude Code\"" 2>/dev/null || true
elif command -v notify-send >/dev/null 2>&1; then
    notify-send "Claude Code" "$MESSAGE" 2>/dev/null || true
fi
exit 0
