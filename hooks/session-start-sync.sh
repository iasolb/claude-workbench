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

# "Always exits 0" above is only true if every call in here RETURNS. Git talking
# to a remote is the one thing in this hook that can wait forever.
. "$(dirname "${BASH_SOURCE[0]}")/portable.sh"

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

if [[ -z "$REPO" || ! -e "$REPO/.git" ]]; then
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
    # Report what git actually said. "offline?" was a guess, and it read as
    # harmless on a machine whose real problem was an expired credential.
    if ! git_remote -C "$REPO" fetch origin --quiet; then
        echo "[workbench] FETCH FROM ORIGIN FAILED, so this session is working from local state that may be stale. Nothing merged, nothing pushed. git said: ${GIT_NOHANG_ERR:-(no output)}"
        exit 0
    fi
fi

# 3. Leftovers from a previous session (the SessionEnd hook stages, never
# commits). timelog/sessions.csv alone is machine-generated churn (the
# time-log hook appends rows every session), so it gets auto-committed and
# pushed instead of nagging; anything else is a real leftover to report.
DIRTY="$(git -C "$REPO" status --porcelain)"
DIRTY_REAL="$(printf '%s\n' "$DIRTY" | grep -v 'timelog/sessions\.csv' | grep -v '^$' || true)"
if [[ -n "$DIRTY" && -z "$DIRTY_REAL" ]]; then
    if git -C "$REPO" add timelog/sessions.csv >/dev/null 2>&1 &&
       git -C "$REPO" commit --quiet -m 'timelog: session rows (auto-commit, session-start-sync)' >/dev/null 2>&1; then
        echo '[workbench] auto-committed timelog session rows.'
        [[ "$HAS_ORIGIN" -eq 1 ]] && git_remote -C "$REPO" push --quiet
        DIRTY="$(git -C "$REPO" status --porcelain)"
    fi
fi
if [[ -n "$DIRTY" ]]; then
    echo "[workbench] Uncommitted changes in $REPO left from a previous session. Commit them now (draft the message from the diff), then push."
fi

# 4. Converge: merge each sync branch from the other machines. Conflicts are
# deliberately left in the worktree for Claude to resolve immediately.
#
# A dirty tree used to SKIP the merge entirely, so a rule or settings fix
# pushed from another environment could sit unmerged for sessions while this
# machine kept running the old config. Convergence is not optional: stash,
# merge, restore.
#
# A stash pop that conflicts leaves BOTH the markers in the worktree AND the
# stash on the list. Stashing that state again next session compounds it:
# observed 2026-08-07, three orphan stashes and one card's frontmatter
# corrupted twice. So never stash a tree that still carries markers.
#
# DETECTION IS A CONTENT GREP, NOT `diff --check`, and that is the whole point
# (fixed 2026-08-15 after the guard failed and 17 stashes had accumulated).
# `git diff --check` compares the WORKTREE TO THE INDEX, so it is blind to
# exactly the two states this guard exists to catch: a failed `stash pop`
# STAGES its conflicted result, and four separate passes have COMMITTED
# markers outright. In both cases worktree and index agree, `diff --check`
# says nothing, and the hook cheerfully re-stashed a corrupted tree. `git
# grep` reads file CONTENT and finds a marker wherever it sits. Verified on
# a real repo the day it was written: zero false positives, and prose that
# merely discusses markers does not match because the pattern is anchored.
MARKERS=0
if [[ "$HAS_ORIGIN" -eq 1 && -n "$SYNC_BRANCHES" ]]; then
    if git -C "$REPO" grep -l -I -E '^<<<<<<< |^>>>>>>> ' >/dev/null 2>&1; then
        MARKERS=1
    fi
fi

# CONVERGENCE TAKES A LOCK, because several sessions start at once and every
# one of them runs this hook against the SAME repo. Measured 2026-09-02: three
# SessionStart rows landed inside one second on this machine, and one pre-merge
# stash was left on the list with its content already back in the worktree,
# which is exactly what an interleaved push/pop pair looks like. `git stash
# pop` with no argument takes stash@{0}, so when two hooks stash at once one of
# them pops the OTHER one's entry: the tree ends up correct, and an entry is
# orphaned with nothing to show it happened. That is also the shape of the
# seventeen stashes that had piled up by 2026-08-15, and the reason a person
# had to inspect and drop one by hand this morning (golden rule A3: a guard
# whose failure mode needs a person is not automation).
#
# mkdir is the lock because it is atomic, and it lives under .git so it can
# never dirty the worktree, never be committed, and never be stashed.
# THE LOCK LIVES IN THE RESOLVED GIT DIR, NEVER IN "$REPO/.git".
#
# `$REPO/.git` is a DIRECTORY only in a standalone clone. In a SUBMODULE it is
# a FILE holding `gitdir: ../../.git/modules/<name>`, and in a linked worktree
# it is a file too. ai-memory-bank is a submodule of the -kit tree on the Mac,
# so `mkdir "$REPO/.git/session-start-sync.lock"` failed with ENOTDIR on every
# single attempt, forever. `git rev-parse --absolute-git-dir` resolves all
# three layouts and is the only correct way to ask.
GITDIR="$(git -C "$REPO" rev-parse --absolute-git-dir 2>/dev/null)"
LOCK=""
[[ -n "$GITDIR" && -d "$GITDIR" ]] && LOCK="$GITDIR/session-start-sync.lock"
lock_mtime() {
    file_mtime "$LOCK"
}
release_lock() {
    [[ -n "$LOCK" ]] && rmdir "$LOCK" 2>/dev/null
    return 0
}
# THIS LOOP MUST TERMINATE ON ITS ITERATION COUNT ALONE, whatever any probe
# inside it reports. It did not, and that took the Mac desktop app down
# completely on 2026-09-05: the app sat on "Starting Claude Code..." forever
# with three copies of this hook spinning at once.
#
# The old shape was `while ! mkdir "$LOCK"`, with a staleness branch ending in
# `continue`. On the submodule layout above, mkdir could never succeed and
# `stat` could never read the lock, so file_mtime returned its 0 fallback,
# `now - 0` was always greater than 120, and every iteration took the
# `continue` path, which SKIPPED BOTH `sleep 1` AND the counter. An unbounded
# CPU spin, from two guards that each swallowed their own error: mkdir's rc
# discarded by the `while !`, stat's by file_mtime's `|| echo 0`.
#
# So: every iteration sleeps and increments, mkdir failing for a STRUCTURAL
# reason is reported loudly and immediately instead of being waited out, and an
# unreadable mtime is treated as "cannot tell" rather than as "ancient".
acquire_lock() {
    if [[ -z "$LOCK" ]]; then
        echo "[workbench] NO LOCK LOCATION: the git dir for $REPO did not resolve to a directory, so convergence was SKIPPED rather than raced unlocked. Nothing merged this session. Check: git -C \"$REPO\" rev-parse --absolute-git-dir"
        return 1
    fi
    local waited=0 now m
    while (( waited < 20 )); do
        mkdir "$LOCK" 2>/dev/null && return 0
        # mkdir failed and the lock is not there: the parent is missing or is
        # not a directory. Waiting cannot fix that, and reporting it as
        # contention would be a lie.
        if [[ ! -d "$LOCK" ]]; then
            echo "[workbench] CANNOT CREATE THE CONVERGENCE LOCK at $LOCK, and no such lock exists, so mkdir is failing structurally rather than losing a race. Convergence SKIPPED, nothing merged. Reproduce: mkdir \"$LOCK\""
            return 1
        fi
        # Nothing under this lock should ever take a minute, so a lock older
        # than that belonged to a session that died. Break it rather than
        # skipping convergence on this machine forever. mtime 0 means stat
        # could not read it, which is not evidence of age: keep waiting.
        m="$(lock_mtime)"
        now="$(date +%s)"
        if [[ "$m" != "0" ]] && (( now - m > 120 )); then
            rmdir "$LOCK" 2>/dev/null || true
        fi
        sleep 1
        waited=$(( waited + 1 ))
    done
    return 1
}

# Assumes the lock is HELD. Split out so the lock/unlock pair stays readable.
converge() {
    # Re-read the tree under the lock: whoever held it may have merged,
    # committed, or restored a stash since $DIRTY was measured above.
    DIRTY="$(git -C "$REPO" status --porcelain)"
    STASHED=0
    STASH_SHA=''
    if [[ -n "$DIRTY" ]]; then
        if git -C "$REPO" stash push --include-untracked --quiet -m 'session-start-sync: pre-merge autostash' >/dev/null 2>&1; then
            STASHED=1
            STASH_SHA="$(git -C "$REPO" rev-parse -q --verify 'stash@{0}' 2>/dev/null)"
            echo "[workbench] stashed local changes to merge; restored below."
        fi
    fi
    for B in ${SYNC_BRANCHES//,/ }; do
        OTHER="origin/$B"
        git -C "$REPO" rev-parse --verify --quiet "$OTHER" >/dev/null 2>&1 || continue
        BEHIND="$(git -C "$REPO" rev-list --count "HEAD..$OTHER")"
        [[ "$BEHIND" -gt 0 ]] || continue
        if git -C "$REPO" merge --no-edit "$OTHER" >/dev/null 2>&1; then
            echo "[workbench] merged $BEHIND commit(s) from $OTHER."
        else
            echo "[workbench] MERGE CONFLICT with $OTHER. Resolve it in $REPO before any other work: keep both sides' facts, dedupe MEMORY.md, commit, push."
        fi
    done
    [[ "$STASHED" -eq 1 ]] || return 0

    # Pop OUR entry, never "whatever is on top". The lock should make those the
    # same thing; assert it instead of trusting it, because popping another
    # session's stash SUCCEEDS and leaves no trace, which is why this went
    # unnoticed for weeks.
    TOP_SHA="$(git -C "$REPO" rev-parse -q --verify 'stash@{0}' 2>/dev/null)"
    if [[ -n "$STASH_SHA" && "$TOP_SHA" != "$STASH_SHA" ]]; then
        echo "[workbench] NOT popping: the top stash is not the one this hook pushed, so something is stashing without the lock. Both were left alone. Inspect: git -C \"$REPO\" stash list"
        return 0
    fi
    # Do NOT infer the pop failed from its exit code. Observed 2026-08-15:
    # this fired with an EMPTY file list ("CONFLICT MARKERS in  ,") against
    # a clean tree and an empty stash list, because the pop had fully
    # applied and dropped. A false alarm that says the repo is corrupt
    # costs the same trust as a missed real one. Assert on CONTENT.
    git -C "$REPO" stash pop >/dev/null 2>&1 || true
    CONFLICTED="$(git -C "$REPO" diff --name-only --diff-filter=U | tr '\n' ' ' | sed 's/ *$//')"
    if [[ -n "$CONFLICTED" ]]; then
        echo "[workbench] STASH DID NOT REAPPLY. The worktree now has CONFLICT MARKERS in: $CONFLICTED. Fix those files by hand FIRST (do not pop again, it will re-conflict), then commit."
        return 0
    fi
    # A clean pop drops its own entry. One still sitting there means the pop
    # refused for a reason that produced no conflict, so the content may NOT
    # have landed: report it, never drop it blind.
    if [[ -n "$STASH_SHA" ]] && git -C "$REPO" rev-parse -q --verify 'stash@{0}' >/dev/null 2>&1; then
        if [[ "$(git -C "$REPO" rev-parse -q --verify 'stash@{0}')" == "$STASH_SHA" ]]; then
            echo "[workbench] the pre-merge stash is STILL on the list after a pop that reported no conflict, so its content may not have landed. Check it with git -C \"$REPO\" stash show -p and drop it only if it did."
        fi
    fi
}
if [[ "$MARKERS" -eq 1 ]]; then
    echo "[workbench] CONFLICT MARKERS in $REPO, staged or committed or both. Convergence SKIPPED this pass ON PURPOSE: re-stashing a marker-corrupted tree is how orphan stashes accumulate. Name the files with git -C \"$REPO\" grep -l -E '^<<<<<<< ', fix them, commit, then start a fresh session to merge."
elif [[ "$HAS_ORIGIN" -eq 1 && -n "$SYNC_BRANCHES" ]]; then
    if acquire_lock; then
        # Release on any exit path. A lock left behind would make every later
        # session skip convergence for two minutes, which is the same silent
        # staleness this whole section exists to prevent.
        trap release_lock EXIT
        converge
        release_lock
        trap - EXIT
    else
        echo "[workbench] another session is converging $REPO right now, so this pass skipped the merge on purpose rather than racing it. Nothing is lost: whoever holds the lock is doing the same merge."
    fi
fi

# 4b. Orphan stashes. A failed pop leaves its stash on the list and nothing
# has ever swept them: SEVENTEEN had piled up by 2026-08-15, each one a
# convergence that silently half-failed. The count is the only signal that
# ever existed, so print it.
if [[ "$HAS_ORIGIN" -eq 1 ]]; then
    STASH_COUNT="$(git -C "$REPO" stash list | wc -l | tr -d ' ')"
    if [[ "$STASH_COUNT" -gt 0 ]]; then
        echo "[workbench] $STASH_COUNT leftover stash(es) in $REPO from failed pops. Inspect the newest with git -C \"$REPO\" stash show -p and drop what already landed: git -C \"$REPO\" stash drop"
    fi
fi

# 4c. IS THE AUTOMATION SERVICE UP? This check exists because of a bootstrap
# loop that cost a whole session on 2026-09-02.
#
# Golden rule 1 tells every session to call the session-checklist endpoint
# rather than re-derive its opening order. STEP 1 of what that endpoint returns
# is "Start the automation service", and its stated reason is exactly right: "A
# session that finds it down only when it wants an endpoint has already routed
# that work somewhere more expensive."
#
# But the checklist is SERVED BY the service that step 1 is about. That morning
# the service was down (Docker Desktop was not running on the PC), so the
# endpoint refused, so the instruction to check the service could not be
# delivered. All fourteen workflows were dead, the driver-end ping and the
# queue tick with them, the farm app's staging containers too, and nothing said
# a word for an unknown number of days.
#
# So the check has to live HERE, in a hook that runs locally and depends on
# nothing. A probe, its answer, and the command that fixes it.
#
# N8N_URL in the conf overrides the default, and it is REQUIRED on a machine
# that is not the one hosting the service: 127.0.0.1 is correct on the host and
# wrong everywhere else, which is why the Mac read the endpoint as unreachable
# rather than as remote.
N8N_URL="$(conf_get N8N_URL)"
N8N_URL="${N8N_URL:-http://127.0.0.1:5678}"
if command -v curl >/dev/null 2>&1; then
    N8N_CODE="$(curl -s -o /dev/null -m 4 -w '%{http_code}' \
                "$N8N_URL/webhook/session-checklist" 2>/dev/null || echo 000)"
    case "$N8N_CODE" in
        200|204|404)
            echo "[workbench] automation service answering at $N8N_URL (HTTP $N8N_CODE)."
            ;;
        *)
            echo "[workbench] AUTOMATION SERVICE NOT ANSWERING at $N8N_URL (HTTP $N8N_CODE). Every workflow is dead while this is down, including the driver-end ping and the queue tick, and the cheap tier is unavailable so work will silently route to an expensive one. Fix: python tools/pc.py docker   then: python tools/pc.py status"
            ;;
    esac
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
