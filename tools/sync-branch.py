#!/usr/bin/env python3
"""Reconcile a local branch with its origin counterpart, local commits on top.

Two landing paths reach the same branch and target different refs:
`tools/lane.ps1 done` merges a lane into the LOCAL branch, while a pull
request merged with `gh` lands on origin. Both are correct and both are
used, often minutes apart, so the two refs diverge every time one of each
happens, and `git pull --ff-only` then refuses.

Measured 2026-08-30: the five-step recovery below was hand-run FIVE times in
one session, and checkin.py printed the same divergence error twice more
without recovering at all. Golden rule A2: the second occurrence means a
command should already exist, and it is golden rule A3 every time it was a
manual intervention inside the most normal routine there is, landing work.

    git stash push -u
    git rebase origin/main
    git push
    git stash pop

This is that recovery as a command. It classifies the state with git
(`rev-list --left-right --count`), never by trying an operation, because
"diverged" and "behind" look alike and need opposite handling: rebasing a
branch a plain fast-forward would fix rewrites commit hashes for nothing,
and fast-forwarding a diverged branch is impossible.

  equal      fast, silent of drama, exit 0
  behind     fast-forward, no rebase, no stash
  ahead      push, no rebase
  diverged   stash anything dirty, rebase, push, restore the stash

Safety, non-negotiable: never force-push, never --hard, never drop a stash
this run did not create. A rebase conflict STOPS the command, leaves the
rebase in progress for a session to resolve, prints the exact files, and
exits non-zero; it must NOT abort, because aborting silently discards the
choice to resolve. A stash that cannot be restored is reported loudly by
name, never silently lost.

--dry-run prints the classification and the steps it WOULD run and changes
nothing; the self-test proves the branch tip is identical before and after.

Wiring this into `tools/checkin.py` is a SEPARATE change and out of scope
here. This order ships the command itself.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

STASH_MESSAGE = "sync-branch: auto-stashed before reconciling"


class SyncError(Exception):
    pass


def run(cmd, timeout=600):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def git(repo, *args, timeout=600):
    return run(["git", "-C", str(repo), *map(str, args)], timeout=timeout)


def current_branch(repo):
    r = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    return r.stdout.strip() or None


def upstream_ref(repo, branch):
    r = git(repo, "rev-parse", "--abbrev-ref", "@{u}")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return "origin/" + branch


def remote_of(upstream):
    if "/" in upstream:
        return upstream.split("/", 1)[0]
    return "origin"


def push_refspec(branch, upstream):
    if "/" in upstream:
        return branch + ":" + upstream.split("/", 1)[1]
    return branch


def ref_exists(repo, ref):
    return git(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def classify(repo, upstream):
    """(behind, ahead) counts against upstream. The whole tool hangs on this.

    `rev-list --left-right --count` is the one honest instrument: behind and
    diverged look alike and a wrong guess here means rewriting history that a
    fast-forward would have preserved, or attempting an impossible merge.
    """
    r = git(repo, "rev-list", "--left-right", "--count", upstream + "...HEAD")
    if r.returncode != 0:
        raise SyncError("cannot compare HEAD against %s: %s" % (
            upstream, (r.stderr or r.stdout).strip()[:240]))
    parts = (r.stdout or "").split()
    if len(parts) != 2:
        raise SyncError("unexpected rev-list output: %r" % (r.stdout or ""))
    return int(parts[0]), int(parts[1])


def state_of(behind, ahead):
    if behind == 0 and ahead == 0:
        return "equal"
    if ahead > 0 and behind == 0:
        return "ahead"
    if behind > 0 and ahead == 0:
        return "behind"
    return "diverged"


def is_dirty(repo):
    r = git(repo, "status", "--porcelain")
    return bool((r.stdout or "").strip())


def unmerged_files(repo):
    r = git(repo, "diff", "--name-only", "--diff-filter=U")
    return [ln for ln in (r.stdout or "").splitlines() if ln.strip()]


def rebase_in_progress(repo):
    r = git(repo, "rev-parse", "--git-dir")
    if r.returncode != 0:
        return False
    gd = pathlib.Path(r.stdout.strip())
    if not gd.is_absolute():
        gd = pathlib.Path(repo) / gd
    return (gd / "rebase-merge").is_dir() or (gd / "rebase-apply").is_dir()


def stash_push(repo):
    r = git(repo, "stash", "push", "-u", "-m", STASH_MESSAGE)
    if r.returncode != 0:
        raise SyncError("could not stash local changes: %s" % (
            (r.stderr or r.stdout).strip()[:240]))
    rr = git(repo, "rev-parse", "--short", "stash@{0}")
    return rr.stdout.strip() if rr.returncode == 0 else "stash@{0}"


def plan_lines(state, dirty, upstream):
    lines = []
    if state == "behind":
        lines.append("fast-forward local onto " + upstream)
    elif state == "ahead":
        lines.append("push local commits to " + remote_of(upstream))
    elif state == "diverged":
        if dirty:
            lines.append("stash the dirty working tree (stash push -u)")
        lines.append("rebase local commits onto " + upstream)
        lines.append("push the rebased branch")
        if dirty:
            lines.append("restore the stashed working tree (stash pop)")
    return lines


def fast_forward(repo, upstream, log):
    r = git(repo, "merge", "--ff-only", upstream)
    if r.returncode != 0:
        log("fast-forward failed: %s" % (r.stderr or r.stdout).strip()[:240])
        return 1
    log("fast-forwarded onto " + upstream)
    return 0


def push(repo, branch, upstream, log):
    r = git(repo, "push", remote_of(upstream), push_refspec(branch, upstream))
    if r.returncode != 0:
        log("push failed: %s" % (r.stderr or r.stdout).strip()[:240])
        return 1
    log("pushed " + branch + " to " + upstream)
    return 0


def restore_stash(repo, stash_ref, log):
    r = git(repo, "stash", "pop")
    if r.returncode != 0:
        log("ERROR: the stash could NOT be restored.")
        log("It is preserved at %s; apply it by hand and drop it only once "
            "the working tree is complete." % stash_ref)
        return 1
    log("restored the stashed working tree")
    return 0


def reconcile_diverged(repo, branch, upstream, dirty, log):
    stash_ref = None
    if dirty:
        stash_ref = stash_push(repo)
        log("stashed working tree as " + stash_ref)

    r = git(repo, "rebase", upstream)
    if r.returncode != 0:
        files = unmerged_files(repo)
        log("rebase CONFLICT: the rebase is left IN PROGRESS, not aborted")
        if files:
            log("conflicted files:")
            for name in files:
                log("  " + name)
        else:
            log("rebase failed: " + (r.stderr or r.stdout).strip()[:240])
        if stash_ref:
            log("the stashed working tree is parked at %s, it is NOT lost"
                % stash_ref)
        return 1
    log("rebased onto " + upstream)

    rc = push(repo, branch, upstream, log)
    if dirty:
        if restore_stash(repo, stash_ref, log) != 0:
            return 1
    return rc


def sync(repo, dry_run=False, log=print) -> int:
    repo = pathlib.Path(repo)
    branch = current_branch(repo)
    if branch is None:
        log("detached HEAD: nothing to reconcile")
        return 1
    r = git(repo, "fetch")
    if r.returncode != 0:
        log("fetch failed: %s" % (r.stderr or r.stdout).strip()[:240])
        return 1
    upstream = upstream_ref(repo, branch)
    if not ref_exists(repo, upstream):
        log("no remote branch %s: this branch has never reached origin"
            % upstream)
        return 1
    behind, ahead = classify(repo, upstream)
    state = state_of(behind, ahead)
    dirty = is_dirty(repo)
    log("branch %s: %s (behind %d, ahead %d)%s" % (
        branch, state, behind, ahead,
        ", working tree dirty" if dirty else ", working tree clean"))

    if state == "equal":
        log("already in sync: nothing to do")
        return 0

    if dry_run:
        for line in plan_lines(state, dirty, upstream):
            log("  would " + line)
        return 0

    if state == "behind":
        return fast_forward(repo, upstream, log)
    if state == "ahead":
        return push(repo, branch, upstream, log)
    return reconcile_diverged(repo, branch, upstream, dirty, log)


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    checks = []

    def want(ok, label, detail=""):
        checks.append((bool(ok), label, detail))

    base = pathlib.Path(tempfile.mkdtemp(prefix="sync-branch-"))
    collect = []

    def log(line):
        collect.append(str(line))

    def ident(repo):
        assert git(repo, "config", "user.name", "sync-branch-selftest").returncode == 0
        assert git(repo, "config", "user.email", "sync-branch-selftest").returncode == 0

    def commit_file(repo, rel, content, msg):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        assert git(repo, "add", "--", rel).returncode == 0
        assert git(repo, "commit", "-m", msg, "--", rel).returncode == 0

    def resync(repo):
        """Bring the remote-side clone up to date before it adds a commit.

        The two clones see origin through their own tracking refs, which only
        move when THEY fetch or push. `other` starts synced once and then falls
        behind every time `local` pushes, so a later push from `other` is a
        non-fast-forward and git rejects it. Re-sync before it speaks.
        """
        assert git(repo, "fetch", "origin").returncode == 0
        assert git(repo, "reset", "--hard", "origin/main").returncode == 0

    def rev(repo, ref="HEAD"):
        r = git(repo, "rev-parse", ref)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    @contextlib.contextmanager
    def in_dir(path):
        prev = os.getcwd()
        os.chdir(str(path))
        try:
            yield
        finally:
            os.chdir(prev)

    def run_cli_rc(argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_cli(argv)
        return rc, out.getvalue(), err.getvalue()

    try:
        remote = base / "remote"
        assert git(base, "init", "--bare", remote.name).returncode == 0
        local = base / "local"
        assert git(base, "init", "-b", "main", local.name).returncode == 0
        ident(local)
        assert git(local, "remote", "add", "origin", str(remote)).returncode == 0
        commit_file(local, "base.txt", "one\n", "base")
        assert git(local, "push", "-u", "origin", "main").returncode == 0

        other = base / "other"
        assert git(base, "init", "-b", "main", other.name).returncode == 0
        ident(other)
        assert git(other, "remote", "add", "origin", str(remote)).returncode == 0
        assert git(other, "fetch", "origin").returncode == 0
        assert git(other, "reset", "--hard", "origin/main").returncode == 0

        # --- equal ---------------------------------------------------------
        behind, ahead = classify(local, "origin/main")
        want((behind, ahead) == (0, 0) and state_of(behind, ahead) == "equal",
             "equal: classified equal, not anything noisier", (behind, ahead))
        want(sync(local, log=log) == 0, "equal: exits 0 doing nothing")
        want(rev(local) == rev(local, "origin/main"),
             "equal: branch tip untouched")
        collect.clear()

        # --- behind (remote-only commits) ----------------------------------
        resync(other)
        commit_file(other, "remote.txt", "remote\n", "remote-only")
        assert git(other, "push", "origin", "main").returncode == 0
        assert git(local, "fetch", "origin").returncode == 0
        behind, ahead = classify(local, "origin/main")
        want((behind, ahead) == (1, 0) and state_of(behind, ahead) == "behind",
             "behind: classified BEHIND, not diverged", (behind, ahead))
        want(sync(local, log=log) == 0, "behind: exits 0")
        want(rev(local) == rev(other),
             "behind: fast-forwarded to the origin tip, no rewrite")
        want(rev(local) == rev(local, "origin/main"),
             "behind: local equals origin after sync")
        collect.clear()

        # --- ahead (local-only commits) ------------------------------------
        commit_file(local, "local.txt", "local\n", "local-only")
        behind, ahead = classify(local, "origin/main")
        want((behind, ahead) == (0, 1) and state_of(behind, ahead) == "ahead",
             "ahead: classified AHEAD, no rebase needed", (behind, ahead))
        local_sha = rev(local)
        want(sync(local, log=log) == 0, "ahead: exits 0")
        want(rev(local) == local_sha, "ahead: local commits left in place")
        want(rev(local, "origin/main") == local_sha,
             "ahead: pushed, origin now at the local tip")
        collect.clear()

        # --- diverged, clean ------------------------------------------------
        commit_file(local, "local2.txt", "l2\n", "local-2")
        resync(other)
        commit_file(other, "remote2.txt", "r2\n", "remote-2")
        assert git(other, "push", "origin", "main").returncode == 0
        assert git(local, "fetch", "origin").returncode == 0
        behind, ahead = classify(local, "origin/main")
        want(behind > 0 and ahead > 0 and state_of(behind, ahead) == "diverged",
             "diverged: classified DIVERGED, not behind", (behind, ahead))
        want(sync(local, log=log) == 0, "diverged: exits 0")
        want(rev(local) == rev(local, "origin/main"),
             "diverged: local tip now equals the origin tip")
        want(git(local, "rev-list", "--count", "origin/main..HEAD")
             .stdout.strip() == "0", "diverged: nothing left unpushed")
        want("local-2" in git(local, "log", "-1", "--oneline").stdout,
             "diverged: the local commit sits ON TOP after the rebase")
        want("remote-2" in git(local, "log", "--oneline").stdout,
             "diverged: the origin commit is in history, not discarded")
        collect.clear()

        # --- diverged with a dirty working tree ------------------------------
        # The assertion that matters most: the uncommitted file must survive
        # the stash round trip byte for byte. A pre-existing stash must also
        # survive: this run may only ever drop the stash it created.
        commit_file(local, "local3.txt", "l3\n", "local-3")
        resync(other)
        commit_file(other, "remote3.txt", "r3\n", "remote-3")
        assert git(other, "push", "origin", "main").returncode == 0
        assert git(local, "fetch", "origin").returncode == 0
        (local / "pre.txt").write_text("pre-existing stash\n", encoding="utf-8")
        assert git(local, "stash", "push", "-u", "-m", "pre-existing").returncode == 0
        dirty_content = "dirty \u00e9 \u2603\nsecond line\n"
        (local / "work.txt").write_text(dirty_content, encoding="utf-8")
        want(sync(local, log=log) == 0,
             "diverged with dirty tree: exits 0")
        got = (local / "work.txt").read_text(encoding="utf-8")
        want(got == dirty_content,
             "diverged with dirty tree: the uncommitted file survives the "
             "stash round trip BYTE FOR BYTE", repr(got))
        want(rev(local) == rev(local, "origin/main"),
             "diverged with dirty tree: branch reconciled to origin")
        stashes = git(local, "stash", "list").stdout
        want("pre-existing" in stashes,
             "a stash this run did not create survives untouched", stashes)
        want(STASH_MESSAGE not in stashes,
             "and the run's own stash was restored, not left behind", stashes)
        assert git(local, "stash", "drop").returncode == 0
        collect.clear()

        # --- a caller that names no repo is REFUSED, never mis-answered ------
        # The measured defect: with no --repo the default was the caller's cwd,
        # so from a checkout in detached HEAD it answered "detached HEAD:
        # nothing to reconcile" about a repo it never meant to inspect. An
        # unresolved repo is now an ERROR, not a value: the refusal names the
        # flag and shares no exit code or message with "nothing to do".
        headless = base / "headless"
        assert git(base, "init", "-b", "main", headless.name).returncode == 0
        ident(headless)
        commit_file(headless, "h.txt", "h\n", "h")
        assert git(headless, "checkout", "--detach").returncode == 0
        want(current_branch(headless) is None,
             "CLI: the fixture cwd is in detached HEAD, like the measured run")

        with in_dir(headless):
            rc, out, err = run_cli_rc([])
        want(rc != 0,
             "CLI: no --repo is REFUSED, never reconciled by the caller's cwd",
             (rc, out, err))
        want(rc != 1,
             "CLI: the refusal is not the detached-HEAD exit code either",
             (rc, out, err))
        want("--repo" in err,
             "CLI: the refusal names the flag", err)
        want("nothing to reconcile" not in out + err,
             "CLI: a repo it never inspected is never reported as "
             "'nothing to reconcile'", (rc, out, err))
        want("nothing to do" not in out + err,
             "CLI: the refusal is not an exit-0 'nothing to do'", (rc, out, err))

        # and the flag still drives a real reconcile end to end: the refusal is
        # about the MISSING flag, never about the cwd being detached.
        commit_file(local, "local5.txt", "l5\n", "local-5")
        resync(other)
        commit_file(other, "remote5.txt", "r5\n", "remote-5")
        assert git(other, "push", "origin", "main").returncode == 0
        (local / "work3.txt").write_text("dirty via cli\n", encoding="utf-8")
        with in_dir(headless):
            rc, out, err = run_cli_rc(["--repo", str(local)])
        want(rc == 0,
             "CLI: an explicit --repo reconciles an ahead-and-behind dirty repo "
             "even from a detached-HEAD cwd", (rc, out, err))
        want(rev(local) == rev(local, "origin/main"),
             "CLI: the named repo reached the origin tip")
        want((local / "work3.txt").read_text(encoding="utf-8") == "dirty via cli\n",
             "CLI: the named repo's dirty file survived the round trip")
        want("nothing to reconcile" not in out,
             "CLI: the reconciled output is not the detached-HEAD lie", out)
        (local / "work3.txt").unlink(missing_ok=True)
        collect.clear()

        # --- dry-run changes nothing ----------------------------------------
        commit_file(local, "local4.txt", "l4\n", "local-4")
        resync(other)
        commit_file(other, "remote4.txt", "r4\n", "remote-4")
        assert git(other, "push", "origin", "main").returncode == 0
        (local / "work2.txt").write_text("untouched\n", encoding="utf-8")
        sha_before = rev(local)
        want(sync(local, dry_run=True, log=log) == 0, "dry-run: exits 0")
        want(rev(local) == sha_before,
             "dry-run: branch tip IDENTICAL before and after")
        want((local / "work2.txt").read_text(encoding="utf-8") == "untouched\n",
             "dry-run: working tree untouched")
        want(git(local, "stash", "list").stdout.strip() == "",
             "dry-run: no stash created")
        want(not rebase_in_progress(local), "dry-run: no rebase started")
        want((local / "local4.txt").exists(), "dry-run: nothing was committed")
        joined = "\n".join(collect)
        want("diverged" in joined and "rebase" in joined and "stash" in joined
             and "push" in joined,
             "dry-run: the plan names the classification and every step",
             joined)
        collect.clear()

        # --- a rebase conflict stops, does NOT abort -------------------------
        (local / "work.txt").unlink(missing_ok=True)
        (local / "work2.txt").unlink(missing_ok=True)
        (local / "same.txt").write_text("local side\n", encoding="utf-8")
        assert git(local, "add", "--", "same.txt").returncode == 0
        assert git(local, "commit", "-m", "local same.txt edit",
                   "--", "same.txt").returncode == 0
        sha_before = rev(local)
        resync(other)
        (other / "same.txt").write_text("remote side\n", encoding="utf-8")
        assert git(other, "add", "--", "same.txt").returncode == 0
        assert git(other, "commit", "-m", "remote same.txt edit",
                   "--", "same.txt").returncode == 0
        assert git(other, "push", "origin", "main").returncode == 0
        want(sync(local, log=log) != 0, "conflict: exits non-zero")
        want(rebase_in_progress(local),
             "conflict: the rebase is left IN PROGRESS, not aborted")
        want(unmerged_files(local) == ["same.txt"],
             "conflict: the exact conflicted file is reported",
             unmerged_files(local))
        want(git(local, "rev-parse", "refs/heads/main").stdout.strip() == sha_before,
             "conflict: the branch ref still points where it started")
        joined = "\n".join(collect)
        want("same.txt" in joined, "conflict: the file name is in the output",
             joined)
        collect.clear()
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad = [(l, x) for ok_, l, x in checks if not ok_]
    for ok_, label, detail in checks:
        print(("PASS  " if ok_ else "FAIL  ") + label +
              ("" if ok_ else "  [%r]" % (detail,)))
    print("\n%d/%d checks passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


def run_cli(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo",
                    help="repository to reconcile (REQUIRED: the tool refuses "
                         "to guess one from the caller's directory)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the classification and the steps that WOULD "
                         "run; change nothing")
    ap.add_argument("--self-test", action="store_true",
                    help="assert every state against throwaway git repos")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.repo:
        print("error: --repo is required: refusing to guess which repository "
              "to reconcile", file=sys.stderr)
        return 2
    try:
        return sync(a.repo, dry_run=a.dry_run)
    except SyncError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


def main() -> int:
    return run_cli(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
