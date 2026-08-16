#!/usr/bin/env bash
# SessionStart hook: print the navigation map (nav/map.md) into session
# context, shared section plus this machine's section only. PURPOSE: spend
# zero session tokens rediscovering paths, ssh targets, and conventions.
# Companion to queue-print (what to work on) and memory-health (when to
# consolidate); this one answers "where is everything". macOS/Linux side;
# Windows runs the .ps1 counterpart. Prints [nav] lines, always exits 0.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO" ]] || exit 0

MAP="$REPO/nav/map.md"
# Opt-in like the queue: no map, no output.
[[ -f "$MAP" ]] || exit 0

# Section names in the map: "shared" always prints, then the machine one.
MACHINE="mac"

echo "[nav] map (shared + $MACHINE; source: nav/map.md, edit there):"
awk -v want="$MACHINE" '
    /^## /{ sect = substr($0, 4); next }
    sect == "shared" || sect == want { print }
' "$MAP" | grep -v '^[[:space:]]*$'

exit 0
