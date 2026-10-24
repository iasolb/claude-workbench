#!/usr/bin/env bash
# Run one named SessionStart hook with bounded time and visible failures.
# Usage: run-hook.sh <hook-name> [--limit <seconds>]
#
# Hooks are resolved beside this file. Only a simple filename stem is accepted;
# names cannot select a path outside the hook directory. Stdin is captured once
# and passed to the child so parallel hooks cannot leave one another blocked.
# The wrapper always returns success to the host application, but reports a
# timeout or nonzero child status on stdout.

HOOK_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NAME=""
LIMIT=25
while [[ $# -gt 0 ]]; do
    case "$1" in
        --limit)
            [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || { echo "[hook] --limit needs seconds"; exit 0; }
            LIMIT="$2"
            shift 2
            ;;
        *) NAME="$1"; shift ;;
    esac
done

if [[ ! "$NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "[hook] provide one simple hook filename stem."
    exit 0
fi

PAYLOAD="$(mktemp "${TMPDIR:-/tmp}/claude-hook-payload.XXXXXX" 2>/dev/null)" || {
    echo "[hook] could not create a temporary payload file."
    exit 0
}
cleanup() { rm -f "$PAYLOAD"; }
trap cleanup EXIT

set -m
bounded() {
    local seconds="$1"; shift
    ( "$@" ) &
    local pid=$!
    ( sleep "$seconds"; kill -9 -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null ) </dev/null >/dev/null 2>&1 &
    local watchdog=$!
    wait "$pid" 2>/dev/null
    local rc=$?
    kill -9 -"$watchdog" 2>/dev/null || kill -9 "$watchdog" 2>/dev/null
    wait "$watchdog" 2>/dev/null
    return "$rc"
}

bounded 5 cat >"$PAYLOAD" 2>/dev/null
case "$(uname 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*)
        SCRIPT="$HOOK_DIR/$NAME.ps1"
        if [[ -f "$SCRIPT" ]]; then
            set -- powershell -NoProfile -ExecutionPolicy Bypass -File "$SCRIPT"
        else
            SCRIPT="$HOOK_DIR/$NAME.sh"
            [[ -f "$SCRIPT" ]] || { echo "[hook] no hook script for $NAME."; exit 0; }
            set -- bash "$SCRIPT"
        fi
        ;;
    *)
        SCRIPT="$HOOK_DIR/$NAME.sh"
        [[ -f "$SCRIPT" ]] || { echo "[hook] no POSIX hook script for $NAME."; exit 0; }
        set -- bash "$SCRIPT"
        ;;
esac

bounded "$LIMIT" "$@" <"$PAYLOAD"
RC=$?
if [[ "$RC" -eq 137 || "$RC" -eq 143 ]]; then
    echo "[hook] $NAME was killed after ${LIMIT}s; its check did not complete."
elif [[ "$RC" -ne 0 ]]; then
    echo "[hook] $NAME exited with status $RC."
fi
exit 0
