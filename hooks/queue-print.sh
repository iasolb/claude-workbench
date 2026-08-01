#!/usr/bin/env bash
# SessionStart hook: print the priority queue into session context, so a
# fresh session starts on the right work without a memory-file treasure
# hunt. Condensed on purpose: only the numbered-item headlines from
# queue/global.md (its format contract keeps first lines self-contained)
# plus all of queue/current.md (the in-flight arc state). macOS/Linux side;
# Windows runs the .ps1 counterpart (settings.json wires up both and each
# self-selects by platform). Reads ~/.claude/workbench.conf for the repo
# path. Prints [queue] lines and always exits 0: a broken print must never
# block a session.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

# WORKBENCH_CONF overrides the conf location (mainly for testing). A missing
# conf or repo is session-start-sync's problem to report, stay quiet here.
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO" ]] || exit 0

# The queue is opt-in: no queue/ dir means this machine doesn't use it, stay
# quiet. A half-present queue (dir exists, a file missing) still warns below.
[[ -d "$REPO/queue" ]] || exit 0

GLOBAL="$REPO/queue/global.md"
CURRENT="$REPO/queue/current.md"

if [[ -f "$GLOBAL" ]]; then
    echo "[queue] standing priorities (headlines; full detail in queue/global.md):"
    grep -E '^[0-9]+\.' "$GLOBAL"
else
    echo "[queue] queue/global.md is missing from the repo. Recreate it (its contract is described in the surviving queue file's header, or in git history)."
fi

if [[ -f "$CURRENT" ]]; then
    echo "[queue] ----- queue/current.md -----"
    cat "$CURRENT"
else
    echo "[queue] queue/current.md is missing from the repo. Recreate it (its contract is described in the surviving queue file's header, or in git history)."
fi

exit 0
