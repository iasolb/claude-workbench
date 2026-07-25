#!/usr/bin/env bash
# SessionEnd hook: stage (never commit or push) any pending changes in the
# workbench repo, and notify if anything got staged. macOS/Linux side;
# Windows runs the .ps1 counterpart.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO/.git" ]] || exit 0

cd "$REPO" || exit 0
git add -A

if git diff --cached --quiet; then
    exit 0
fi

MESSAGE="Changes staged in $(basename "$REPO"), ready to review"
if [[ "$(uname)" == "Darwin" ]]; then
    osascript -e "display notification \"$MESSAGE\" with title \"Claude Code\"" 2>/dev/null || true
elif command -v notify-send >/dev/null 2>&1; then
    notify-send "Claude Code" "$MESSAGE" 2>/dev/null || true
fi
exit 0
