#!/usr/bin/env bash
# Stop hook: nudge when declared code paths changed without a matching test change.
# Opt in with TEST_NUDGE_REPO. TEST_NUDGE_CODE_DIRS is colon-separated and
# defaults to app:templates; TEST_NUDGE_TEST_DIR defaults to tests.
REPO="${TEST_NUDGE_REPO:-}"
[[ -n "$REPO" ]] || exit 0
case "$(uname)" in MINGW*|MSYS*|CYGWIN*) [[ -n "${TEST_NUDGE_REPO:-}" ]] || exit 0 ;; esac
INPUT="$(cat)"
printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0
[[ -e "$REPO/.git" ]] || exit 0
IFS=: read -r -a CODE_DIRS <<< "${TEST_NUDGE_CODE_DIRS:-app:templates}"
TEST_DIR="${TEST_NUDGE_TEST_DIR:-tests}"
changed="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" ls-files --others --exclude-standard 2>/dev/null)"
changed="$(printf '%s\n' "$changed" | sed '/^$/d')"
if [[ -z "$changed" ]]; then
    git -C "$REPO" rev-parse --verify HEAD~1 >/dev/null 2>&1 || exit 0
    changed="$(git -C "$REPO" diff --name-only HEAD~1 HEAD 2>/dev/null | sed '/^$/d')"
fi
[[ -n "$changed" ]] || exit 0
code="$(printf '%s\n' "$changed" | awk -v dirs="${TEST_NUDGE_CODE_DIRS:-app:templates}" 'BEGIN { n=split(dirs,a,":") } { for(i=1;i<=n;i++) if(index($0,a[i] "/")==1) print }' | sort -u)"
[[ -n "$code" ]] || exit 0
printf '%s\n' "$changed" | grep -q "^${TEST_DIR}/" && exit 0
SESSION="$(printf '%s' "$INPUT" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
[[ -n "$SESSION" ]] || SESSION=nosession
MARKER="${TMPDIR:-/tmp}/test-nudge-sh-$SESSION"
[[ -f "$MARKER" && "$(cat "$MARKER" 2>/dev/null)" == "$code" ]] && exit 0
printf '%s' "$code" > "$MARKER"
count="$(printf '%s\n' "$code" | wc -l | tr -d ' ')"
printf '%s code file(s) changed without a file under %s/. Add a relevant test or explain why none is needed.\n' "$count" "$TEST_DIR" >&2
exit 2
