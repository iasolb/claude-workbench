#!/usr/bin/env bash
# Test for the convergence lock in session-start-sync.sh, against a throwaway
# repo built fresh in a temp directory. Never touches the real repo or queue.
#
# Every assertion is paired with its DISCRIMINATION case: the same check run
# against a world where the thing is not happening, so a check that can only
# ever pass gets caught. The first draft of this file had two such assertions
# and reported the lock broken while it was working correctly
# (`rules/shared/memory-integrity.md` rule 2).
#
# Run it:
#   bash <repo>/hooks/session-start-sync.test.sh
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/session-start-sync.sh"
T=$(mktemp -d)
SHIM_COUNT="$T/shim-n"
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); echo "PASS  $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL  $1"; }

echo "=== 0. syntax ==="
if bash -n "$HOOK"; then ok "hook parses"; else bad "hook parses"; exit 1; fi

# --- fixture: a bare origin with a 'mac' branch, plus a clone on it ---
git init -q --bare "$T/origin.git"
git clone -q "$T/origin.git" "$T/repo" 2>/dev/null
git -C "$T/repo" config user.email t@t
git -C "$T/repo" config user.name t
echo seed > "$T/repo/seed.txt"
git -C "$T/repo" add -A >/dev/null
git -C "$T/repo" commit -qm seed
git -C "$T/repo" branch -M mac
git -C "$T/repo" push -q -u origin mac
LOCK="$T/repo/.git/session-start-sync.lock"

cat > "$T/conf" <<EOF
REPO_PATH=$T/repo
MACHINE_BRANCH=mac
SYNC_BRANCHES=mac
EOF

# Drop the DOCTOR noise: it reports on the real machine's memory symlink and
# has nothing to do with what is under test.
run() { WORKBENCH_CONF="$T/conf" bash "$HOOK" 2>&1 | grep -v 'DOCTOR\|left from a previous session'; }

echo "=== 1. baseline: a dirty tree converges and the stash comes back ==="
echo local-edit >> "$T/repo/seed.txt"
OUT=$(run)
if grep -q 'stashed local changes' <<<"$OUT"; then ok "stashed"; else bad "stashed: $OUT"; fi
if grep -q 'local-edit' "$T/repo/seed.txt"; then ok "edit restored"; else bad "edit restored"; fi
if [[ -z "$(git -C "$T/repo" stash list)" ]]; then ok "no stash left"; else bad "stash left"; fi
if grep -q 'STILL on the list' <<<"$OUT"; then bad "false orphan warning"; else ok "no false orphan warning"; fi
# DISCRIMINATION for section 2: with no lock held it must NOT back off.
if grep -q 'another session is converging' <<<"$OUT"; then
    bad "backed off with no lock held (check cannot discriminate)"
else
    ok "does not back off when nothing holds the lock"
fi

echo "=== 2. THE GUARD: a held lock makes the hook back off and touch nothing ==="
mkdir -p "$LOCK"
BEFORE=$(git -C "$T/repo" status --porcelain)
OUT=$(run)
if grep -q 'another session is converging' <<<"$OUT"; then ok "backed off"; else bad "did NOT back off, lock ignored: $OUT"; fi
if grep -q 'stashed local changes' <<<"$OUT"; then bad "stashed anyway while locked"; else ok "did not stash while locked"; fi
if [[ "$(git -C "$T/repo" status --porcelain)" == "$BEFORE" ]]; then ok "worktree untouched while locked"; else bad "worktree changed while locked"; fi
if [[ -d "$LOCK" ]]; then ok "did not steal a fresh lock"; else bad "STOLE a lock that was not stale"; fi

echo "=== 3. a STALE lock is broken rather than blocking forever ==="
touch -t "$(date -v-5M +%Y%m%d%H%M 2>/dev/null || date -d '5 minutes ago' +%Y%m%d%H%M)" "$LOCK"
OUT=$(run)
if grep -q 'another session is converging' <<<"$OUT"; then bad "still blocked by a 5-minute-old lock"; else ok "broke the stale lock"; fi
if [[ -d "$LOCK" ]]; then bad "lock left behind after the run"; else ok "lock released after the run"; fi

echo "=== 4. THE IDENTITY GUARD: refuse to pop a stash this hook did not push ==="
# A git shim earlier in PATH answers `rev-parse -q --verify stash@{0}` with a
# DIFFERENT value each call, so the SHA recorded at push time cannot match the
# one read back before the pop. That is exactly the state two concurrent hooks
# produce, and the real window is far too small to hit on purpose.
mkdir -p "$T/bin"
cat > "$T/bin/git" <<SHIM
#!/usr/bin/env bash
if [[ "\$*" == *"rev-parse -q --verify stash@{0}"* ]]; then
    N=\$(cat "$SHIM_COUNT" 2>/dev/null || echo 0)
    echo \$(( N + 1 )) > "$SHIM_COUNT"
    printf 'deadbeef%032d\n' "\$(( N + 1 ))"
    exit 0
fi
exec /usr/bin/git "\$@"
SHIM
chmod +x "$T/bin/git"
rm -f "$SHIM_COUNT"
echo ours >> "$T/repo/seed.txt"
OUT=$(PATH="$T/bin:$PATH" WORKBENCH_CONF="$T/conf" bash "$HOOK" 2>&1 | grep -v 'DOCTOR\|left from a previous session')
if grep -q 'NOT popping' <<<"$OUT"; then ok "refused to pop a stash it did not push"; else bad "did NOT refuse (guard unproven): $OUT"; fi
# DISCRIMINATION: the same scenario with real git must NOT refuse.
git -C "$T/repo" stash pop >/dev/null 2>&1 || true
echo ours2 >> "$T/repo/seed.txt"
OUT=$(run)
if grep -q 'NOT popping' <<<"$OUT"; then bad "refuses even when the stash IS its own"; else ok "pops normally when the stash is its own"; fi
if grep -q 'ours2' "$T/repo/seed.txt"; then ok "edit restored in the normal path"; else bad "edit lost in the normal path"; fi

echo "=== 5. THE SERVICE PROBE must be able to report DOWN, not just UP ==="
# This is the check whose absence cost a whole session: the automation service
# was down for an unknown number of days and the only instruction to check it
# was served BY that service. A health check that cannot say "unhealthy" is
# not a health check, so both directions are asserted here.
cat > "$T/conf-dead" <<EOF
REPO_PATH=$T/repo
MACHINE_BRANCH=mac
SYNC_BRANCHES=mac
N8N_URL=http://127.0.0.1:1
EOF
OUT=$(WORKBENCH_CONF="$T/conf-dead" bash "$HOOK" 2>&1)
if grep -q 'AUTOMATION SERVICE NOT ANSWERING' <<<"$OUT"; then
    ok "a dead service is reported as not answering"
else
    bad "a dead service was NOT reported: $OUT"
fi
if grep -q 'pc.py docker' <<<"$OUT"; then
    ok "and the message carries the command that fixes it"
else
    bad "no fix command in the warning"
fi
# DISCRIMINATION: a LIVE service must report the opposite. Uses the real one,
# and is skipped rather than failed when it genuinely is not running, because
# this test must not go red for a reason outside the code under test.
LIVE="$(sed -n 's/^N8N_URL=//p' "$HOME/.claude/workbench.conf" 2>/dev/null | head -n 1)"
LIVE="${LIVE:-http://127.0.0.1:5678}"
if [[ "$(curl -s -o /dev/null -m 4 -w '%{http_code}' "$LIVE/webhook/session-checklist" 2>/dev/null)" == "200" ]]; then
    cat > "$T/conf-live" <<EOF
REPO_PATH=$T/repo
MACHINE_BRANCH=mac
SYNC_BRANCHES=mac
N8N_URL=$LIVE
EOF
    OUT=$(WORKBENCH_CONF="$T/conf-live" bash "$HOOK" 2>&1)
    if grep -q 'automation service answering' <<<"$OUT"; then
        ok "a live service is reported as answering"
    else
        bad "a live service was reported as down: $OUT"
    fi
    if grep -q 'NOT ANSWERING' <<<"$OUT"; then
        bad "reported a live service as down at the same time"
    else
        ok "and does not also claim it is down"
    fi
else
    echo "SKIP  live-service direction: nothing answering at $LIVE right now"
fi

echo "=== 6. A SUBMODULE LAYOUT, where \$REPO/.git is a FILE, must still RETURN ==="
# THE BUG THIS FILE EXISTED FOR AND DID NOT CATCH. Every fixture above is a
# `git clone`, so .git is a real DIRECTORY and the lock's mkdir succeeds. On
# the Mac, ai-memory-bank is a SUBMODULE: .git is a FILE holding a `gitdir:`
# pointer, mkdir "$REPO/.git/<lock>" fails with ENOTDIR forever, stat cannot
# read the lock so file_mtime returns its 0 fallback, and the staleness branch
# `continue`d past both the sleep and the counter. Unbounded CPU spin. The
# Claude desktop app sat on "Starting Claude Code..." forever, 2026-09-05.
#
# So the assertion is WALL CLOCK, not output: the hook must come back at all.
git init -q --bare "$T/sub-origin.git"
git clone -q "$T/sub-origin.git" "$T/super/sub" 2>/dev/null
git -C "$T/super/sub" config user.email t@t
git -C "$T/super/sub" config user.name t
echo seed > "$T/super/sub/seed.txt"
git -C "$T/super/sub" add -A >/dev/null
git -C "$T/super/sub" commit -qm seed
git -C "$T/super/sub" branch -M mac
git -C "$T/super/sub" push -q -u origin mac
# Convert it to the submodule shape by hand: move the git dir out and leave the
# `gitdir:` FILE behind, which is exactly what `git submodule` produces.
mkdir -p "$T/super/.git/modules"
mv "$T/super/sub/.git" "$T/super/.git/modules/sub"
printf 'gitdir: %s\n' "$T/super/.git/modules/sub" > "$T/super/sub/.git"
git -C "$T/super/sub" config core.worktree "$T/super/sub"
cat > "$T/conf-sub" <<EOF
REPO_PATH=$T/super/sub
MACHINE_BRANCH=mac
SYNC_BRANCHES=mac
N8N_URL=http://127.0.0.1:1
EOF
if [[ -f "$T/super/sub/.git" && ! -d "$T/super/sub/.git" ]]; then
    ok "fixture really is a submodule layout (.git is a file)"
else
    bad "fixture is NOT a submodule layout, section 6 proves nothing"
fi
echo local-edit >> "$T/super/sub/seed.txt"
START=$(date +%s)
( WORKBENCH_CONF="$T/conf-sub" bash "$HOOK" >"$T/sub.out" 2>&1 & p=$!
  ( sleep 45; kill -9 $p 2>/dev/null ) & w=$!
  wait $p 2>/dev/null; kill $w 2>/dev/null )
ELAPSED=$(( $(date +%s) - START ))
if (( ELAPSED < 40 )); then
    ok "hook RETURNED on a submodule layout (${ELAPSED}s)"
else
    bad "hook HUNG on a submodule layout (${ELAPSED}s): this is the 2026-09-05 desktop-app hang"
fi
# And it must have really converged, not merely survived by skipping the work.
if grep -q 'stashed local changes' "$T/sub.out"; then
    ok "and it took the lock and converged"
else
    bad "returned but never converged: $(cat "$T/sub.out")"
fi
if grep -q 'CANNOT CREATE THE CONVERGENCE LOCK\|NO LOCK LOCATION' "$T/sub.out"; then
    bad "could not place the lock on a submodule layout"
else
    ok "placed the lock in the resolved git dir"
fi

echo "=== 7. run-hook.sh BOUNDS a hook that never returns ==="
# The wrapper is the structural half: even a hook that spins forever must not
# take the session with it, and it must SAY so rather than failing silently.
mkdir -p "$T/hooks"
cp "$HERE/run-hook.sh" "$T/hooks/"
printf '#!/usr/bin/env bash\nwhile :; do :; done\n' > "$T/hooks/spinner.sh"
printf '#!/usr/bin/env bash\necho fine\n'            > "$T/hooks/quick.sh"
chmod +x "$T/hooks"/*.sh
START=$(date +%s)
OUT=$(bash "$T/hooks/run-hook.sh" spinner --limit 5 </dev/null 2>&1)
RC=$?
ELAPSED=$(( $(date +%s) - START ))
if (( ELAPSED < 20 )); then ok "killed the spinner (${ELAPSED}s)"; else bad "spinner ran ${ELAPSED}s, wrapper did not bound it"; fi
if [[ "$RC" -eq 0 ]]; then ok "wrapper still exits 0, so it cannot block a session"; else bad "wrapper exited $RC"; fi
if grep -q 'WAS KILLED' <<<"$OUT"; then ok "and says out loud that it was killed"; else bad "killed SILENTLY: $OUT"; fi
# DISCRIMINATION: a hook that finishes must NOT be reported as killed.
OUT=$(bash "$T/hooks/run-hook.sh" quick --limit 5 </dev/null 2>&1)
if grep -q 'fine' <<<"$OUT"; then ok "passes a healthy hook's output through"; else bad "swallowed healthy output: $OUT"; fi
if grep -q 'WAS KILLED\|exited' <<<"$OUT"; then bad "reported a healthy hook as failed"; else ok "does not cry wolf on a healthy hook"; fi
# A hook whose stdin is never written must not block on `cat`.
printf '#!/usr/bin/env bash\nI="$(cat)"\necho "read ${#I} bytes"\n' > "$T/hooks/reader.sh"
chmod +x "$T/hooks/reader.sh"
START=$(date +%s)
OUT=$(bash "$T/hooks/run-hook.sh" reader --limit 20 </dev/null 2>&1)
ELAPSED=$(( $(date +%s) - START ))
if (( ELAPSED < 15 )); then ok "a stdin-reading hook is not left blocked (${ELAPSED}s)"; else bad "blocked on stdin for ${ELAPSED}s"; fi

echo "=== 8. machine-written row files are committed BY PATHSPEC ==="
# Windows twin: session-start-sync.test.py. A staged unrelated file must not
# ride along, and must still be reported as a leftover.
git init -q --bare "$T/rows-origin.git"
git clone -q "$T/rows-origin.git" "$T/rows" 2>/dev/null
git -C "$T/rows" config user.email t@t
git -C "$T/rows" config user.name t
mkdir -p "$T/rows/automation" "$T/rows/reports/go"
echo a > "$T/rows/automation/driver-errors.csv"
echo n > "$T/rows/notes.md"
git -C "$T/rows" add -A >/dev/null
git -C "$T/rows" commit -qm seed
git -C "$T/rows" branch -M mac
git -C "$T/rows" push -q -u origin mac
printf 'REPO_PATH=%s\nMACHINE_BRANCH=mac\nSYNC_BRANCHES=mac\n' "$T/rows" > "$T/conf-rows"
echo b >> "$T/rows/automation/driver-errors.csv"
echo new > "$T/rows/reports/go/new.md"
echo edit >> "$T/rows/notes.md"
git -C "$T/rows" add notes.md
BEFORE=$(git -C "$T/rows" rev-parse HEAD)
OUT=$(WORKBENCH_CONF="$T/conf-rows" bash "$HOOK" 2>&1)
CHANGED=$(git -C "$T/rows" show --name-only --pretty=format: HEAD)
if [[ "$(git -C "$T/rows" rev-parse HEAD)" != "$BEFORE" ]]; then ok "a row commit was made"; else bad "no row commit: $OUT"; fi
if grep -q 'reports/go/new.md' <<<"$CHANGED"; then ok "untracked go report committed"; else bad "go report missing: $CHANGED"; fi
if grep -q 'notes.md' <<<"$CHANGED"; then bad "staged notes.md rode along"; else ok "staged notes.md did not ride along"; fi
if grep -q 'left from a previous session' <<<"$OUT"; then ok "real leftover still reported"; else bad "real leftover not reported"; fi

echo
echo "assertions: $PASS passed, $FAIL failed"
rm -rf "$T"
[[ "$FAIL" -eq 0 ]]
