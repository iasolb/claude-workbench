#!/usr/bin/env bash
# PermissionRequest/Notification hook: record prompt details without changing
# the prompt decision. WORKBENCH_CONF defaults to ~/.claude/workbench.conf;
# its REPO_PATH and optional MACHINE_BRANCH select prompts/<branch>.tsv.
case "$(uname)" in MINGW*|MSYS*|CYGWIN*) exit 0 ;; esac
INPUT="$(cat)"
[[ -n "$INPUT" ]] || exit 0
EVENT="$(printf '%s' "$INPUT" | jq -r '.hook_event_name // ""' 2>/dev/null)"
TOOL=unresolved; CMD=""; DISP='?'
if [[ "$EVENT" == PermissionRequest ]]; then
    DISP=exact
    TOOL="$(printf '%s' "$INPUT" | jq -r '.tool_name // "unresolved"' 2>/dev/null)"
    CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input | (.command // .file_path // .path // tostring)' 2>/dev/null)"
else
    TYPE="$(printf '%s' "$INPUT" | jq -r '.notification_type // ""' 2>/dev/null)"
    [[ "$TYPE" == permission_prompt ]] || exit 0
fi
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
value() { sed -n "s/^$1=//p" "$CONF" | tr -d '\r' | head -n 1; }
REPO="$(value REPO_PATH)"
[[ -d "$REPO" ]] || exit 0
BRANCH="$(value MACHINE_BRANCH)"
[[ -n "$BRANCH" ]] || BRANCH=local
TRANSCRIPT="$(printf '%s' "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null)"
if [[ "$DISP" != exact && -f "$TRANSCRIPT" ]]; then
    line="$(tail -n 400 "$TRANSCRIPT" | tac | while IFS= read -r row; do
        result="$(printf '%s' "$row" | jq -r '[ (.message.content // [])[] | select(.type == "tool_use") ] | last | if . == null then empty else [ .name, (.input.command // .input.file_path // (.input | tostring)) ] | @tsv end' 2>/dev/null)"
        [[ -n "$result" ]] && { printf '%s' "$result"; break; }
    done)"
    if [[ -n "$line" ]]; then IFS=$'\t' read -r TOOL CMD <<< "$line"; fi
fi
[[ -n "$CMD" ]] || CMD="no matching tool call found ($(printf '%s' "$INPUT" | jq -r '.cwd // "?"' 2>/dev/null))"
CMD="$(printf '%s' "$CMD" | perl -pe 's/((?:secret|token|password|passwd|api[_-]?key|credential)\w*\s*[=:]\s*["\x27]?)[^"\x27\s]+/${1}[REDACTED]/gi; s/gh[pousr]_[A-Za-z0-9]{16,}/[REDACTED]/g; s/eyJ[\w-]{8,}\.[\w-]{8,}(\.[\w-]+)?/[REDACTED]/g; s/(bearer\s+)[\w.~+\/-]{8,}/${1}[REDACTED]/gi')"
CMD="$(printf '%s' "$CMD" | cut -c1-300 | tr '\t\n\r' '   ' | sed 's/  */ /g; s/^ //; s/ $//')"
TOOL="$(printf '%s' "$TOOL" | tr '\t\n\r' '   ')"
NOW="$(date '+%Y-%m-%d %H:%M')"
DIR="$REPO/prompts"; LOG="$DIR/$BRANCH.tsv"
mkdir -p "$DIR" 2>/dev/null || exit 0
[[ -f "$LOG" ]] || printf 'count\tfirst_seen\tlast_seen\tdisposition\ttool\tcommand\n' > "$LOG"
TMP="$LOG.$$"
awk -F '\t' -v OFS='\t' -v tool="$TOOL" -v cmd="$CMD" -v now="$NOW" -v disp="$DISP" '
    NR == 1 { print; next }
    $4 == disp && $5 == tool && $6 == cmd { $1 = $1 + 1; $3 = now; found = 1 }
    { print }
    END { if (!found) print 1, now, now, disp, tool, cmd }
' "$LOG" > "$TMP" 2>/dev/null && mv "$TMP" "$LOG" 2>/dev/null
rm -f "$TMP" 2>/dev/null
exit 0
