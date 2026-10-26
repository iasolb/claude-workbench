#!/usr/bin/env bash
# SessionStart hook: warn when configured memory and status files exceed budgets.
# Defaults: MEMORY_DIR=memory, MEMORY_INDEX=memory/MEMORY.md,
# CURRENT_STATUS=queue/current.md. MANDATED_READS is a colon-separated list
# of repository-relative paths; set it to monitor additional always-read files.
case "$(uname)" in MINGW*|MSYS*|CYGWIN*) exit 0 ;; esac
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -d "$REPO" ]] || exit 0
MEMORY_DIR="${MEMORY_DIR:-memory}"
MEMORY_INDEX="${MEMORY_INDEX:-$MEMORY_DIR/MEMORY.md}"
CURRENT_STATUS="${CURRENT_STATUS:-queue/current.md}"
INDEX_MAX="${MEMORY_INDEX_MAX_LINES:-45}"
CURRENT_MAX="${CURRENT_STATUS_MAX_LINES:-150}"
FILE_MAX="${MEMORY_FILE_MAX_KB:-10}"
TOTAL_MAX="${MEMORY_TOTAL_MAX_KB:-185}"
READ_MAX="${MANDATED_READ_MAX_KB:-65}"
warned=0
say() { [[ $warned -eq 0 ]] && printf '%s\n' '[memory-health] over budget, review and consolidate:'; warned=1; printf '[memory-health]   %s\n' "$1"; }
if [[ -f "$REPO/$MEMORY_INDEX" ]]; then
    n="$(wc -l < "$REPO/$MEMORY_INDEX" | tr -d ' ')"
    [[ $n -gt $INDEX_MAX ]] && say "index has $n lines (budget $INDEX_MAX)"
fi
if [[ -f "$REPO/$CURRENT_STATUS" ]]; then
    n="$(wc -l < "$REPO/$CURRENT_STATUS" | tr -d ' ')"
    [[ $n -gt $CURRENT_MAX ]] && say "current status has $n lines (budget $CURRENT_MAX)"
fi
MEM="$REPO/$MEMORY_DIR"
if [[ -d "$MEM" ]]; then
    total="$(find "$MEM" -maxdepth 1 -name '*.md' -print0 | xargs -0 wc -c | tail -1 | awk '{print int($1/1024)}')"
    [[ $total -gt $TOTAL_MAX ]] && say "memory directory totals ${total}KB (budget ${TOTAL_MAX}KB)"
    while IFS= read -r file; do
        [[ "$file" == "$REPO/$MEMORY_INDEX" ]] && continue
        kb=$(( $(wc -c < "$file") / 1024 ))
        [[ $kb -gt $FILE_MAX ]] && say "$(basename "$file") is ${kb}KB (budget ${FILE_MAX}KB)"
    done < <(find "$MEM" -maxdepth 1 -name '*.md' ! -path "$REPO/$MEMORY_INDEX")
fi
IFS=: read -r -a reads <<< "${MANDATED_READS:-}"
for relative in "${reads[@]}"; do
    file="$REPO/$relative"
    [[ -f "$file" ]] || continue
    kb=$(( $(wc -c < "$file") / 1024 ))
    [[ $kb -gt $READ_MAX ]] && say "$relative exceeds the mandated-read budget of ${READ_MAX}KB"
done
exit 0
