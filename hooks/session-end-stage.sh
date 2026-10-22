#!/usr/bin/env bash
# SessionEnd hook: notify if the workbench repo has uncommitted changes. It
# never stages, commits or pushes: the repo is a SHARED working tree, and
# staging here swept up every live session's in-progress edits. macOS/Linux
# side; Windows runs the .ps1 counterpart.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -e "$REPO/.git" ]] || exit 0

cd "$REPO" || exit 0

if [[ -z "$(git status --porcelain)" ]]; then
    exit 0
fi

MESSAGE="Uncommitted changes in $(basename "$REPO")"
if [[ "$(uname)" == "Darwin" ]]; then
    osascript -e "display notification \"$MESSAGE\" with title \"Claude Code\"" 2>/dev/null || true
elif command -v notify-send >/dev/null 2>&1; then
    notify-send "Claude Code" "$MESSAGE" 2>/dev/null || true
fi
exit 0
