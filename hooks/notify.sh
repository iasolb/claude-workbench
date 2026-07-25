#!/usr/bin/env bash
# Desktop notification when Claude Code is waiting on input or has finished.
# macOS (osascript) and Linux (notify-send, if present); Windows runs the
# .ps1 counterpart. Same stdin JSON contract on both sides.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

INPUT=$(cat)
TYPE=$(echo "$INPUT" | jq -r '.notification_type // ""' 2>/dev/null)

case "$TYPE" in
    permission_prompt|idle_prompt|agent_needs_input)
        MESSAGE="Waiting on you"
        ;;
    agent_completed)
        MESSAGE="Task finished"
        ;;
    *)
        MESSAGE="Notification"
        ;;
esac

if [[ "$(uname)" == "Darwin" ]]; then
    osascript -e "display notification \"$MESSAGE\" with title \"Claude Code\"" 2>/dev/null || true
elif command -v notify-send >/dev/null 2>&1; then
    notify-send "Claude Code" "$MESSAGE" 2>/dev/null || true
fi
exit 0
