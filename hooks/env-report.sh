#!/usr/bin/env bash
# SessionStart hook, macOS side (env-report.ps1 is the Windows counterpart,
# same file format). Writes this environment's actual state to
# state/<environment>.md so every other angle (a phone, a cloud session, the
# other machine) can see what config this machine is really running.
#
# Exists because hooks exit 0 on failure by design, so a dead hook and a quiet
# system look identical, and nothing otherwise reports which settings a machine
# actually loaded. Reports OBSERVED state only, and says so explicitly where a
# fact cannot be established, because an omission reads as "fine".
#
# Runs LAST in SessionStart, after session-start-sync, so the git facts are
# post-merge. Reads only; the single write is its own state file, which it
# commits and pushes. Always exits 0.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

INPUT="$(cat)"
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO/.git" ]] || exit 0
ENVN="$(sed -n 's/^MACHINE_BRANCH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$ENVN" ]] || ENVN="$(uname | tr '[:upper:]' '[:lower:]')"
SYNCB="$(sed -n 's/^SYNC_BRANCHES=//p' "$CONF" | tr -d '\r' | head -n 1)"

field() { printf '%s' "$INPUT" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1; }
SID="$(field session_id)"
PMODE="$(field permission_mode)"
PMODE_LINE="$PMODE"
[[ -n "$PMODE_LINE" ]] || PMODE_LINE="BLANK, the SessionStart payload did not carry permission_mode. A fact about the payload, not about the mode."
CWD="$(field cwd)"; [[ -n "$CWD" ]] || CWD="$PWD"

BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
HEAD_LINE="$(git -C "$REPO" log -1 --format='%h %s' 2>/dev/null)"
DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
UNPUSHED="$(git -C "$REPO" rev-list --count '@{u}..HEAD' 2>/dev/null)"
[[ -n "$UNPUSHED" ]] || UNPUSHED="no upstream"

BEHIND=""
for B in ${SYNCB//,/ }; do
    if ! git -C "$REPO" rev-parse --verify --quiet "origin/$B" >/dev/null 2>&1; then
        BEHIND="$BEHIND- behind origin/$B: not fetched"$'\n'
        continue
    fi
    N="$(git -C "$REPO" rev-list --count "HEAD..origin/$B" 2>/dev/null)"
    if [[ "$N" -gt 0 ]]; then
        BEHIND="$BEHIND- behind origin/$B: $N NOT MERGED, session-start-sync did not converge"$'\n'
    else
        BEHIND="$BEHIND- behind origin/$B: 0 merged"$'\n'
    fi
done

USER_SETTINGS="$HOME/.claude/settings.json"
if [[ -L "$USER_SETTINGS" ]]; then
    SETTINGS_STATE="symlink -> $(readlink "$USER_SETTINGS")"
elif [[ -f "$USER_SETTINGS" ]]; then
    SETTINGS_STATE="REAL FILE, not the repo copy: edits to the repo do not reach this machine"
else
    SETTINGS_STATE="absent"
fi
FINGERPRINT="absent"
[[ -f "$USER_SETTINGS" ]] && FINGERPRINT="$(shasum -a 256 "$USER_SETTINGS" 2>/dev/null | cut -c1-12)"

# Parsed with python3 when present; "unknown" beats a guessed number.
DEFAULT_MODE="unknown"; ALLOW_COUNT="unknown"; DEAD_RULES="unknown"
if command -v python3 >/dev/null 2>&1 && [[ -f "$USER_SETTINGS" ]]; then
    read -r DEFAULT_MODE ALLOW_COUNT DEAD_RULES <<<"$(python3 - "$USER_SETTINGS" <<'PY'
import json, re, sys
try:
    p = json.load(open(sys.argv[1], encoding='utf-8')).get('permissions', {})
except Exception:
    print('unknown unknown unknown'); raise SystemExit
allow = p.get('allow', [])
dead = [r for r in allow if re.match(r'^(Write|NotebookEdit|MultiEdit|Glob)\(', r)
        or re.match(r'^(Edit|Read)\([A-Za-z]:', r) or re.match(r'^(Edit|Read)\(.*\\\\', r)]
print(p.get('defaultMode', 'not set'), len(allow), len(dead))
PY
)"
fi

LOCAL_LINES=""
# $HOME/.claude is the USER-level local file, and it belongs in this list: it is
# live for sessions started in $HOME and invisible to every other session, so a
# machine can accumulate a large stored-approval file that nothing ever reports.
SEEN_LP=""
for LP in "$HOME/.claude/settings.local.json" "$HOME/claude/.claude/settings.local.json" "$CWD/.claude/settings.local.json"; do
    case "$SEEN_LP" in *"|$LP|"*) continue;; esac
    SEEN_LP="$SEEN_LP|$LP|"
    if [[ ! -f "$LP" ]]; then
        LOCAL_LINES="$LOCAL_LINES- $LP : absent"$'\n'
        continue
    fi
    SUM="unknown entries"
    if command -v python3 >/dev/null 2>&1; then
        SUM="$(python3 - "$LP" <<'PY'
import json, sys
try:
    p = json.load(open(sys.argv[1], encoding='utf-8')).get('permissions', {})
except Exception:
    print('unparseable'); raise SystemExit
a = p.get('allow', [])
mode = p.get('defaultMode', 'not set')
flag = ' OVERRIDES the shared defaultMode' if mode != 'not set' else ''
# Two counts on purpose: the ':*)' prefix form is the documented wildcard, but
# a bare '*' anywhere (e.g. "Bash(git push *)") is the shape that actually
# accumulates from click-throughs, and counting only the first hid it.
print(f"{len(a)} allow entries, {sum(1 for r in a if r.endswith(':*)'))} prefix-wildcards, {sum(1 for r in a if '*' in r)} entries containing '*', defaultMode: {mode}{flag}")
PY
)"
    fi
    LOCAL_LINES="$LOCAL_LINES- $LP : $SUM"$'\n'
done

TIMELOG="$REPO/timelog/sessions.csv"
TIMELOG_HIT="no row for this session, time-log did not write"
if [[ -n "$SID" && -f "$TIMELOG" ]] && grep -q "$SID" "$TIMELOG" 2>/dev/null; then
    TIMELOG_HIT="row present"
fi
PLOG="$REPO/prompts/$ENVN.tsv"
if [[ -f "$PLOG" ]]; then
    PROMPT_STATE="$(( $(wc -l < "$PLOG") - 1 )) rows, last written $(date -r "$PLOG" '+%Y-%m-%d %H:%M' 2>/dev/null)"
else
    PROMPT_STATE="ABSENT, no permission prompt has ever been logged on this machine"
fi
URGENT_COUNT="$(find "$REPO/urgent" -maxdepth 1 -name '*.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')"

mkdir -p "$REPO/state" 2>/dev/null || exit 0
cat > "$REPO/state/$ENVN.md" <<EOF
# $ENVN, state at last SessionStart

Written by \`hooks/env-report.sh\`. Observed facts only. Regenerated every
session start, so a stale timestamp here means this machine has not started a
session since then.

- when: $(date '+%Y-%m-%d %H:%M %z')
- session: $SID
- cwd: $CWD
- permission_mode: $PMODE_LINE

## Repo git, after the sync hook ran

- branch: $BRANCH
- HEAD: $HEAD_LINE
- dirty files: $DIRTY
- unpushed commits: $UNPUSHED
$BEHIND
## Settings this machine actually loaded

- \`~/.claude/settings.json\`: $SETTINGS_STATE
- content fingerprint: $FINGERPRINT
- permissions.defaultMode: $DEFAULT_MODE
- allow entries: $ALLOW_COUNT
- rules the product ignores (Write/NotebookEdit/MultiEdit/Glob paths, or Windows backslash paths): $DEAD_RULES

### Local settings, which OUTRANK the shared file

$LOCAL_LINES
## Instruments

- timelog row for this session: $TIMELOG_HIT
- prompts/$ENVN.tsv: $PROMPT_STATE
- urgent/ items waiting: $URGENT_COUNT
- NOT verifiable from any file: queue-print, urgent-print, job-brief and
  time-print only write to session context. Their firing can only be
  confirmed by someone reading the session output.
EOF

# PATHSPEC form, and it matters: a bare `git commit` commits the whole INDEX,
# so this hook would file whatever a live session had staged under a message
# that says "state:". That is not hypothetical; it has happened here, sweeping
# six unrelated files into one automatic commit.
git -C "$REPO" add "state/$ENVN.md" >/dev/null 2>&1
git -C "$REPO" commit --quiet -m "state: $ENVN session start (auto, env-report)" -- "state/$ENVN.md" >/dev/null 2>&1 \
    && git -C "$REPO" push --quiet >/dev/null 2>&1
exit 0
