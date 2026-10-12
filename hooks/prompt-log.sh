#!/usr/bin/env bash
# Notification hook: log every PERMISSION PROMPT this environment hits, so
# recurring prompt loops become visible instead of costing the same click
# night after night.
#
# LOGS ONLY. It never answers, suppresses, pre-approves, or influences a
# prompt. A hook that answers prompts is how an autonomous setup gets its
# whole fleet flagged; logging, reporting and narrowing the allowlist by hand
# afterwards is the supported shape. Reading the request and writing a line to
# a file is the whole job.
#
# Storage: $REPO/prompts/<environment>.tsv, one file per environment so
# machines never collide on push. Condensed to UNIQUE commands: a repeat
# bumps count and last_seen instead of appending a new row.
# Columns: count, first_seen, last_seen, disposition, tool, command
# `disposition` starts as `?`; the SESSION fills it in (once / always /
# denied) at wrap-up, because a hook fires BEFORE the answer exists and
# cannot see which button was pressed. Contract: rules/claude/permission-loops.md.
#
# macOS side; Windows runs prompt-log.ps1. Always exits 0.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

INPUT=$(cat)
[[ -n "$INPUT" ]] || exit 0

# TWO EVENTS FEED THIS LOG (2026-09-27): PermissionRequest carries the EXACT
# tool_name/tool_input, so its rows are `exact`; Notification only says a prompt
# happened and its tool is GUESSED from the transcript (see prompt-log.ps1 for
# the measured misattribution). Log-only on both: prints nothing, exits 0.
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // ""' 2>/dev/null)
DISP="?"
TOOL=""
CMD=""
if [[ "$EVENT" == "PermissionRequest" ]]; then
    DISP="exact"
    TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null)
    CMD=$(echo "$INPUT" | jq -r '.tool_input | (.command // .file_path // .path // tostring)' 2>/dev/null)
else
    TYPE=$(echo "$INPUT" | jq -r '.notification_type // ""' 2>/dev/null)
    [[ "$TYPE" == "permission_prompt" ]] || exit 0
fi

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -e "$REPO/.git" ]] || exit 0
ENVN="$(sed -n 's/^MACHINE_BRANCH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$ENVN" ]] || ENVN="$(uname | tr '[:upper:]' '[:lower:]')"

# The Notification payload does NOT carry tool_name or tool_input (checked
# against the hooks reference: the fields are session_id, transcript_path,
# cwd, permission_mode, hook_event_name, notification_type). An earlier
# version read .tool_name/.tool_input and so logged raw JSON at best. The
# waiting call is the LAST tool_use block in the transcript, so resolve it
# from there.
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null)
if [[ "$DISP" != "exact" && -n "$TRANSCRIPT" && -f "$TRANSCRIPT" ]]; then
    while IFS= read -r line; do
        OUT=$(printf '%s' "$line" | jq -r '
            [ (.message.content // [])[] | select(.type == "tool_use") ] | last
            | if . == null then empty
              else [ .name, (.input.command // .input.file_path //
                             (.input | tostring)) ] | @tsv end' 2>/dev/null)
        if [[ -n "$OUT" ]]; then
            IFS=$'\t' read -r TOOL CMD <<< "$OUT"
            break
        fi
    done < <(tail -n 400 "$TRANSCRIPT" | tail -r)
fi
[[ -n "$TOOL" ]] || TOOL="unresolved"
if [[ -z "$CMD" ]]; then
    CMD="no tool_use found in transcript ($(echo "$INPUT" | jq -r '.cwd // "?"' 2>/dev/null))"
fi
# NEVER WRITE A SECRET INTO GIT (2026-10-11): this log is committed, and this
# machine's copy held a billing app's client secret verbatim. Redact BEFORE
# truncating. perl, because macOS sed has no case-insensitive flag. Same
# patterns as prompt-log.ps1, whose test is hooks/prompt-log.test.py.
CMD=$(printf '%s' "$CMD" | perl -pe '
    s/((?:secret|token|password|passwd|api[_-]?key|credential)\w*\s*[=:]\s*["\x27]?)[^"\x27\s]+/${1}[REDACTED]/gi;
    s/gh[pousr]_[A-Za-z0-9]{16,}/[REDACTED]/g;
    s/eyJ[\w-]{8,}\.[\w-]{8,}(\.[\w-]+)?/[REDACTED]/g;
    s/(bearer\s+)[\w.~+\/=-]{8,}/${1}[REDACTED]/gi;')
CMD=$(printf '%s' "$CMD" | cut -c1-300)

# One line, tabs are the delimiter so they cannot appear in a field.
CMD=$(printf '%s' "$CMD" | tr '\t\n\r' '   ' | sed 's/  */ /g; s/^ //; s/ $//')
TOOL=$(printf '%s' "$TOOL" | tr '\t\n\r' '   ')
NOW=$(date '+%Y-%m-%d %H:%M')

LOG_DIR="$REPO/prompts"
LOG="$LOG_DIR/$ENVN.tsv"
mkdir -p "$LOG_DIR" 2>/dev/null || exit 0
if [[ ! -f "$LOG" ]]; then
    printf 'count\tfirst_seen\tlast_seen\tdisposition\ttool\tcommand\n' > "$LOG" 2>/dev/null || exit 0
fi

TMP="$LOG.$$"
awk -F'\t' -v OFS='\t' -v tool="$TOOL" -v cmd="$CMD" -v now="$NOW" -v disp="$DISP" '
    NR == 1 { print; next }
    $4 == disp && $5 == tool && $6 == cmd { $1 = $1 + 1; $3 = now; found = 1 }
    { print }
    END { if (!found) print 1, now, now, disp, tool, cmd }
' "$LOG" > "$TMP" 2>/dev/null && mv "$TMP" "$LOG" 2>/dev/null
rm -f "$TMP" 2>/dev/null

exit 0
