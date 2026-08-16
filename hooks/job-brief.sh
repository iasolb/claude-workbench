#!/usr/bin/env bash
# SessionStart hook: announce pending/active job cards (queue/jobs/*.md)
# targeted at this machine, so a session picks them up without a treasure
# hunt. macOS side; Windows runs the .ps1 counterpart. Prints [jobs] lines
# and always exits 0.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

# This machine's name in job-card terms. Only a Mac runs the .sh side for
# real; JOB_HOOK_MACHINE overrides for testing.
if [[ -z "$JOB_HOOK_MACHINE" ]]; then
    [[ "$(uname)" == "Darwin" ]] || exit 0
    JOB_HOOK_MACHINE=mac
fi

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO/queue/jobs" ]] || exit 0

field() { sed -n "s/^$2:[[:space:]]*//p" "$1" | head -n 1 | tr -d '\r'; }

for f in "$REPO"/queue/jobs/*.md; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"
    [[ "$base" == _* ]] && continue
    STATUS="$(field "$f" status)"
    [[ "$STATUS" == "pending" || "$STATUS" == "active" ]] || continue
    MACH="$(field "$f" machine)"
    [[ "$MACH" == "$JOB_HOOK_MACHINE" || "$MACH" == "any" ]] || continue
    MODE="$(field "$f" mode)"
    echo "[jobs] $STATUS job for this machine: queue/jobs/$base (mode: $MODE). Read the card and follow rules/jobs.md: flip it active, stay in its workdir, honor the mode, run the test gate, report to the inbox. Work ONE card this session, then stop (schedule a fresh run for the next); don't roll into another job in this context."
done

exit 0
