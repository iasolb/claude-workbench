#!/usr/bin/env bash
# Stop-hook nudge for a resumable Markdown worklist.
#
# Set WORKBENCH_STATE_DIR to choose its state directory. The default is
# ~/.local/state/claude-workbench. The worklist is worklist.md and the guard
# counter is worklist.fires. A line beginning with "paused: yes" disables the
# hook. Each unchecked Markdown box produces one continuation; after twelve
# unchanged fires the hook goes quiet to prevent an unattended loop.
#
# Scope: reads the configured worklist and counter; writes only the counter;
# emits the next unchecked item to stderr. It never reads a repository or
# contacts a service.

set -u
STATE_DIR="${WORKBENCH_STATE_DIR:-$HOME/.local/state/claude-workbench}"
LIST="${WORKLIST_FILE:-$STATE_DIR/worklist.md}"
STATE="$STATE_DIR/worklist.fires"
MAX_STALL="${WORKLIST_MAX_STALL:-12}"

[ -f "$LIST" ] || exit 0

if grep -qi '^paused:[[:space:]]*yes' "$LIST" 2>/dev/null; then
    rm -f "$STATE" 2>/dev/null || true
    exit 0
fi

next="$(grep -m1 -n '^- \[ \]' "$LIST" 2>/dev/null || true)"
if [ -z "$next" ]; then
    rm -f "$STATE" 2>/dev/null || true
    exit 0
fi

stamp="$(wc -c <"$LIST" 2>/dev/null | tr -d ' ')"
prev_stamp=""
fires=0
if [ -f "$STATE" ]; then
    prev_stamp="$(sed -n '1p' "$STATE" 2>/dev/null)"
    fires="$(sed -n '2p' "$STATE" 2>/dev/null)"
    case "$fires" in ''|*[!0-9]*) fires=0 ;; esac
fi

[ "$stamp" = "$prev_stamp" ] || fires=0
fires=$((fires + 1))
mkdir -p "$STATE_DIR" 2>/dev/null || true
printf '%s\n%s\n' "$stamp" "$fires" >"$STATE" 2>/dev/null || true

if [ "$fires" -gt "$MAX_STALL" ]; then
    {
        echo "[worklist] $MAX_STALL nudges without a worklist change; stopping to avoid a loop."
        echo "[worklist] Review $LIST, then update or pause it."
    } >&2
    exit 0
fi

item="$(printf '%s' "$next" | sed 's/^[0-9]*://; s/^- \[ \] *//')"
{
    echo "[worklist] Work remains in the configured list ($fires/$MAX_STALL since it changed):"
    echo "[worklist]   $item"
    echo "[worklist] Mark the item complete in $LIST when finished."
} >&2
exit 2
