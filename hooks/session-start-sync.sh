#!/usr/bin/env bash
# SessionStart hook: workbench doctor checks + optional cross-machine branch
# sync. macOS/Linux side; Windows runs the .ps1 counterpart (settings.json
# wires up both and each self-selects by platform). Reads
# ~/.claude/workbench.conf, written by the installer. Prints [workbench]
# lines into session context and always exits 0: a broken check must never
# block a session, visibility is the point.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

# WORKBENCH_CONF overrides the conf location (mainly for testing).
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
if [[ ! -f "$CONF" ]]; then
    echo "[workbench] DOCTOR: no config at $CONF. Run install/mac.sh from your workbench repo to create it."
    exit 0
fi

conf_get() {
    sed -n "s/^$1=//p" "$CONF" | tr -d '\r' | head -n 1
}

REPO="$(conf_get REPO_PATH)"
MACHINE_BRANCH="$(conf_get MACHINE_BRANCH)"
SYNC_BRANCHES="$(conf_get SYNC_BRANCHES)"

if [[ -z "$REPO" || ! -d "$REPO/.git" ]]; then
    echo "[workbench] DOCTOR: repo not found at '$REPO' (from $CONF). If the repo moved, re-run install/mac.sh from its new location."
    exit 0
fi

# 1. The project-key memory symlink must resolve into this repo, or memory
# writes land somewhere git never sees.
PROJECT_KEY="$(pwd | sed 's|[:/]|-|g')"
MEMORY_LINK="$HOME/.claude/projects/$PROJECT_KEY/memory"
EXPECTED="$REPO/memory"
if [[ ! -e "$MEMORY_LINK" ]]; then
    echo "[workbench] DOCTOR: no memory link at $MEMORY_LINK, memory writes are landing nowhere. Recreate it: ln -s \"$EXPECTED\" \"$MEMORY_LINK\""
elif [[ ! -L "$MEMORY_LINK" ]]; then
    echo "[workbench] DOCTOR: $MEMORY_LINK is a real directory, not a symlink into the repo, so memories are not syncing. Merge its contents into $EXPECTED and replace it with a symlink."
else
    TARGET="$(readlink "$MEMORY_LINK")"
    if [[ "$TARGET" != "$EXPECTED" ]]; then
        echo "[workbench] DOCTOR: memory link points at $TARGET, expected $EXPECTED. Re-point it."
    fi
fi

# 2. This machine should stay on the branch recorded at install time.
if [[ -n "$MACHINE_BRANCH" ]]; then
    BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
    if [[ "$BRANCH" != "$MACHINE_BRANCH" ]]; then
        echo "[workbench] DOCTOR: checkout is on '$BRANCH', expected '$MACHINE_BRANCH'. Switch back (git -C \"$REPO\" switch $MACHINE_BRANCH) or re-run the installer if the change is intentional."
        exit 0
    fi
fi

# No remote means nothing to fetch, merge, or push; doctor checks plus the
# dirty-tree report below are still worth having.
HAS_ORIGIN=0
if git -C "$REPO" remote get-url origin >/dev/null 2>&1; then
    HAS_ORIGIN=1
    if ! git -C "$REPO" fetch origin --quiet 2>/dev/null; then
        echo '[workbench] fetch failed (offline?), working from local state, possibly stale.'
        exit 0
    fi
fi

# 3. Leftovers from a previous session (the SessionEnd hook stages, never
# commits).
DIRTY="$(git -C "$REPO" status --porcelain)"
if [[ -n "$DIRTY" ]]; then
    echo '[workbench] Uncommitted changes left from a previous session. Commit them now (draft the message from the diff), then push.'
fi

# 4. Converge: merge each sync branch from the other machines. Conflicts are
# deliberately left in the worktree for Claude to resolve immediately.
if [[ "$HAS_ORIGIN" -eq 1 && -n "$SYNC_BRANCHES" ]]; then
    for B in ${SYNC_BRANCHES//,/ }; do
        OTHER="origin/$B"
        git -C "$REPO" rev-parse --verify --quiet "$OTHER" >/dev/null 2>&1 || continue
        BEHIND="$(git -C "$REPO" rev-list --count "HEAD..$OTHER")"
        [[ "$BEHIND" -gt 0 ]] || continue
        if [[ -n "$DIRTY" ]]; then
            echo "[workbench] $OTHER has $BEHIND commit(s) not merged here. After committing the local changes above, run: git -C \"$REPO\" merge --no-edit $OTHER"
            continue
        fi
        if git -C "$REPO" merge --no-edit "$OTHER" >/dev/null 2>&1; then
            echo "[workbench] merged $BEHIND commit(s) from $OTHER."
        else
            echo "[workbench] MERGE CONFLICT with $OTHER. Resolve it in $REPO before any other work: keep both sides' facts, dedupe MEMORY.md, commit, push."
        fi
    done
fi

# 5. Anything unpushed, including merges just made.
if [[ "$HAS_ORIGIN" -eq 1 && -n "$MACHINE_BRANCH" ]]; then
    if git -C "$REPO" rev-parse --verify --quiet "origin/$MACHINE_BRANCH" >/dev/null 2>&1; then
        AHEAD="$(git -C "$REPO" rev-list --count "origin/$MACHINE_BRANCH..HEAD")"
        if [[ "$AHEAD" -gt 0 ]]; then
            echo "[workbench] $AHEAD unpushed commit(s) on $MACHINE_BRANCH. Push: git -C \"$REPO\" push"
        fi
    else
        echo "[workbench] branch $MACHINE_BRANCH has no upstream yet. Push: git -C \"$REPO\" push -u origin $MACHINE_BRANCH"
    fi
fi

exit 0
