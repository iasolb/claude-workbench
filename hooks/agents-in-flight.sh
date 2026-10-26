#!/usr/bin/env bash
# Stop hook for configured GitHub-agent work. The public tool reads the
# OPENCODE_AGENT_REPOS environment variable and returns distinct statuses for
# clear, in-flight, and unavailable results. An unavailable query is not idle.
#
# Scope: one configured checkout's tools/agents-in-flight.py and its explicitly
# configured repository list. It reads GitHub state through that tool; this
# wrapper writes nothing.

set -u
input="$(cat)"
# A second consecutive stop while the state is UNKNOWN passes: blocking again
# would loop the session forever on a question nothing in it can fix.
active=no
case "$input" in *'"stop_hook_active":true'*|*'"stop_hook_active": true'*) active=yes ;; esac
HOOK_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${WORKBENCH_ROOT:-$(dirname -- "$HOOK_DIR")}"
TOOL="${AGENTS_IN_FLIGHT_TOOL:-$REPO_ROOT/tools/agents-in-flight.py}"
PYTHON="${PYTHON:-}"

if [ -z "$PYTHON" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys' >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
fi

if [ -z "$PYTHON" ] || [ ! -f "$TOOL" ]; then
    [ "$active" = yes ] && exit 0
    printf '%s\n' "COULD NOT CHECK configured GitHub-agent activity; work state is unknown." >&2
    exit 2
fi

output="$("$PYTHON" "$TOOL" 2>&1)"
status=$?
case "$status" in
    0) exit 0 ;;
    3)
        printf '%s\n%s\n' "AGENT WORK IS IN FLIGHT. Inspect each run and review each pull request's diff and checks before authorized next steps." "$output" >&2
        ;;
    *)
        [ "$active" = yes ] && exit 0
        printf '%s\n%s\n' "COULD NOT CHECK GitHub-agent activity (exit $status); work state is unknown, not absent:" "$output" >&2
        ;;
esac
exit 2
