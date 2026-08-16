#!/usr/bin/env bash
# SessionStart hook: memory bloat check. Silent when everything is
# within budget (no tokens wasted on good news); prints [memory-health]
# warnings with numbers when something needs a consolidation or trim pass.
# This is the trigger for efficiency passes: act on it when it fires.
# macOS/Linux side; Windows runs the .ps1 counterpart. Always exits 0.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO" ]] || exit 0

# Budgets. Rationale: MEMORY.md and queue/current.md are printed or loaded
# into EVERY session, so their budgets are tight; individual memory files
# are loaded on demand, so only outliers matter. Tune these to your repo.
MEMORY_MD_MAX_LINES=45      # index: one line per memory + headers
CURRENT_MD_MAX_LINES=150    # arc state, not an archive
FILE_MAX_KB=10              # any single memory file
# Set the directory budget to something a real cleanup pass can actually
# reach. A budget nothing can meet stops being a signal, and the per-FILE
# limit above is the earlier and more useful warning anyway.
TOTAL_MAX_KB=185            # whole memory/ dir

warn=0
say() { [[ $warn -eq 0 ]] && echo "[memory-health] over budget, run a consolidation/trim pass:"; warn=1; echo "[memory-health]   $1"; }

MEM="$REPO/memory"
if [[ -f "$MEM/MEMORY.md" ]]; then
    n=$(wc -l < "$MEM/MEMORY.md" | tr -d ' ')
    [[ $n -gt $MEMORY_MD_MAX_LINES ]] && say "MEMORY.md is $n lines (budget $MEMORY_MD_MAX_LINES): merge or drop index entries"
fi

CUR="$REPO/queue/current.md"
if [[ -f "$CUR" ]]; then
    n=$(wc -l < "$CUR" | tr -d ' ')
    [[ $n -gt $CURRENT_MD_MAX_LINES ]] && say "queue/current.md is $n lines (budget $CURRENT_MD_MAX_LINES): archive finished-arc detail into memory/ and cut it back to live state"
fi

if [[ -d "$MEM" ]]; then
    # Byte-accurate sizes (du -k block-rounds small files up).
    total_kb=$(find "$MEM" -maxdepth 1 -name '*.md' -print0 | xargs -0 wc -c | tail -1 | awk '{print int($1/1024)}')
    [[ $total_kb -gt $TOTAL_MAX_KB ]] && say "memory/ totals ${total_kb}KB (budget ${TOTAL_MAX_KB}KB)"
    while IFS= read -r f; do
        kb=$(( $(wc -c < "$f") / 1024 ))
        [[ $kb -gt $FILE_MAX_KB ]] && say "$(basename "$f") is ${kb}KB (budget ${FILE_MAX_KB}KB): split or condense"
    done < <(find "$MEM" -maxdepth 1 -name '*.md' ! -name 'MEMORY.md')
fi

exit 0
