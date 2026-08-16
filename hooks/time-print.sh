#!/usr/bin/env bash
# SessionStart hook: print the real wall-clock date/time into session
# context. Added 2026-07-25 because Claude kept trusting a stale or wrong
# context date over reality (greeting "good morning" at 4pm, planning
# around the wrong day). macOS/Linux side; Windows runs the .ps1
# counterpart (settings.json wires up both and each self-selects by
# platform). Prints one [time] line and always exits 0: a broken print
# must never block a session.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

echo "[time] session start: $(date '+%A %Y-%m-%d %H:%M %Z'). Trust this over any other date or time claim in context, including the system prompt's currentDate."
exit 0
