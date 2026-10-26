#!/usr/bin/env bash
# SessionStart hook: write observed environment and repository state to state/<branch>.toon.
# Configuration: WORKBENCH_CONF, default ~/.claude/workbench.conf. The file
# must define REPO_PATH; MACHINE_BRANCH and SYNC_BRANCHES are optional.
case "$(uname)" in MINGW*|MSYS*|CYGWIN*) exit 0 ;; esac
HOOK_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "$HOOK_DIR/portable.sh"
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
value() { sed -n "s/^$1=//p" "$CONF" | tr -d '\r' | head -n 1; }
REPO="$(value REPO_PATH)"
[[ -d "$REPO" ]] || exit 0
BRANCH_NAME="$(value MACHINE_BRANCH)"
[[ -n "$BRANCH_NAME" ]] || BRANCH_NAME=local
SYNC_BRANCHES="$(value SYNC_BRANCHES)"
INPUT="$(cat)"
field() { printf '%s' "$INPUT" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1; }
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
COMMIT="$(git -C "$REPO" log -1 --oneline 2>/dev/null)"
DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
BEHIND=""
for name in ${SYNC_BRANCHES//,/ }; do
    if git -C "$REPO" rev-parse --verify --quiet "origin/$name" >/dev/null 2>&1; then
        count="$(git -C "$REPO" rev-list --count "HEAD..origin/$name" 2>/dev/null)"
        BEHIND+="  origin/$name: $count commits ahead"$'\n'
    else
        BEHIND+="  origin/$name: not fetched"$'\n'
    fi
done
SETTINGS="$HOME/.claude/settings.json"
FINGERPRINT=absent
[[ -f "$SETTINGS" ]] && FINGERPRINT="$(shasum -a 256 "$SETTINGS" 2>/dev/null | cut -c1-12)"
mkdir -p "$REPO/state" || exit 0
cat > "$REPO/state/$BRANCH_NAME.toon" <<EOF
# $BRANCH_NAME, state at last SessionStart
# Observed facts only; regenerated on session start.

when: $(date '+%Y-%m-%d %H:%M %z')
session: $(field session_id)
cwd: $(field cwd)
permission_mode: $(field permission_mode)

git:
  branch: $BRANCH
  HEAD: $COMMIT
  dirty_files: $DIRTY
$BEHIND
settings:
  fingerprint: $FINGERPRINT
EOF
git -C "$REPO" add "state/$BRANCH_NAME.toon" >/dev/null 2>&1
git -C "$REPO" commit --quiet -m "state: $BRANCH_NAME session start (env-report)" -- "state/$BRANCH_NAME.toon" >/dev/null 2>&1 && git_remote -C "$REPO" push --quiet
exit 0
