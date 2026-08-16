#!/usr/bin/env bash
# Stop hook: backstop for the job completion email. If a job card for this
# machine is status: done but was never emailed (emailed: no/blank), nudge
# once so the session sends the job-done email per rules/claude/jobs.md before
# wrapping. The hook can't send mail itself (that's a Claude tool call); it
# only ensures the send isn't forgotten. Nudges by exiting 2 (stderr goes back
# to Claude) at most once per session; every other path exits 0. macOS side;
# Windows runs the .ps1 counterpart.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

INPUT="$(cat)"
printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0

json_str() {
    printf '%s' "$INPUT" | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

if [[ -z "$JOB_HOOK_MACHINE" ]]; then
    [[ "$(uname)" == "Darwin" ]] || exit 0
    JOB_HOOK_MACHINE=mac
fi

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO/queue/jobs" ]] || exit 0

field() { sed -n "s/^$2:[[:space:]]*//p" "$1" | head -n 1 | tr -d '\r'; }

PENDING_EMAIL=""
PENDING_COUNT=0
for f in "$REPO"/queue/jobs/*.md; do
    [[ -f "$f" ]] || continue
    [[ "$(basename "$f")" == _* ]] && continue
    [[ "$(field "$f" status)" == "done" ]] || continue
    MACH="$(field "$f" machine)"
    [[ "$MACH" == "$JOB_HOOK_MACHINE" || "$MACH" == "any" ]] || continue
    EMAILED="$(field "$f" emailed)"
    case "$EMAILED" in
        ""|no|No|NO|false|False|pending) ;;   # not sent
        *) continue ;;                          # a date or yes = sent
    esac
    PENDING_EMAIL+=" $(basename "$f")"
    PENDING_COUNT=$((PENDING_COUNT + 1))
done

[[ -n "$PENDING_EMAIL" ]] || exit 0

# Name at most five. A finished lane can leave dozens unstamped, and a wall of
# card filenames is internal bookkeeping nobody should be reading (rules/shared/style.md).
SHOWN="$(printf '%s\n' $PENDING_EMAIL | head -n 5 | tr '\n' ' ')"
if (( PENDING_COUNT > 5 )); then
    SHOWN+="(and $((PENDING_COUNT - 5)) more)"
fi

SESSION_ID="$(json_str session_id)"
[[ -n "$SESSION_ID" ]] || exit 0
MARKER="${TMPDIR:-/tmp}/job-email-nudge-$SESSION_ID"
[[ -e "$MARKER" ]] && exit 0
touch "$MARKER"

echo "$PENDING_COUNT card(s) done but not stamped emailed: $SHOWN. IF THAT COUNT IS LARGE IT IS A BACKLOG, NOT A DEBT: one email covers a whole authorized lane, so a finished lane leaves every card but one reading 'emailed: no' forever. The fix for a backlog is to ARCHIVE those cards into queue/jobs/done/ (both hooks glob the top level only) with the lane's email date recorded ONCE in the inbox Done line, never to send one email per card. CONDITIONAL, read before acting: this hook selects on machine + status + emailed ONLY. It has no session-level signal, so it CANNOT tell which session worked a card and is not asserting that you owe this email. If THIS session worked the card, send the completion email per rules/claude/jobs.md (to the card's notify: address, with the readthrough link), set emailed: to today, and push. If it did NOT, say so in one line and stop: never email a result you did not produce and cannot attest to (rules/shared/memory-integrity.md rule 2). During an authorized overnight lane, ONE email covers the whole lane at the end, not one per card, so an unemailed card mid-lane is correct and needs nothing." >&2
exit 2
