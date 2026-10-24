#!/usr/bin/env python3
"""orphan-check.py: directories under the worktrees root that no repo owns.

    python tools/orphan-check.py                  # read-only, print the table
    python tools/orphan-check.py --apply         # remove only `clean` orphans
    python tools/orphan-check.py --self-test

WHY (order 45). `git worktree prune` drops registrations whose DIRECTORY is
gone. What accumulates on the executor is the opposite shape: a DIRECTORY
whose registration is gone, left by a land that deregistered the lane and
failed to remove the folder, or by a `git worktree remove` that stopped on a
locked file. prune cannot see those, nothing else cleans them up, and the
count only grows: measured 2026-09-03, roughly twenty directories under the
worktrees root belong to no repository.

A directory nobody owns is an ORPHAN CANDIDATE, never an orphan. It is
classified before anything may be removed, and the classification decides
whether --apply may touch it:

    clean      every commit it holds is reachable from a remote branch
    dirty      it has uncommitted changes
    unpushed   it holds commits on no remote branch
    unknown    it is not a git checkout at all

Only `clean` is removable, and only with --apply. dirty, unpushed and unknown
are NEVER removed automatically: a lane that passed its gate and was never
landed is exactly the unpushed shape (queue/current.md records five such
lanes, one sitting eleven days), so refusing is the whole point of the tool.

The default is the memory bank checkout. Additional repositories can be
declared explicitly with `--repos`. Every repo answers `git worktree list
--porcelain`, and the porcelain paths are the only ownership evidence.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import machinelib

STATE_CLEAN = "clean"
STATE_DIRTY = "dirty"
STATE_UNPUSHED = "unpushed"
STATE_UNKNOWN = "unknown"

REASONS = {
    STATE_CLEAN: "every commit reachable from a remote branch",
    STATE_DIRTY: "uncommitted changes present",
    STATE_UNPUSHED: "commits not reachable from any remote branch",
    STATE_UNKNOWN: "not a git checkout",
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command. Decode as utf-8 with replacement so one odd byte can
    never crash a scan (lint.py's _git documents the same Windows failure)."""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(cwd)] + list(args))


def owned_dirs(repos: list[str]) -> set[pathlib.Path]:
    """Every worktree path registered to the known repos, from PORCELAIN.

    The main checkout appears in the listing too and is harmless to include:
    it resolves to the repo itself, never to a directory under the root. A
    repo that is not on this machine fails its `git -C` and is skipped; its
    worktrees cannot exist here either.
    """
    owned = set()
    for repo in repos:
        res = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
        if res.returncode != 0:
            continue
        for line in res.stdout.splitlines():
            if line.startswith("worktree "):
                raw = line[len("worktree "):].strip()
                if raw:
                    owned.add(pathlib.Path(raw).resolve())
    return owned


def classify(path: pathlib.Path) -> str:
    """Classify one orphan candidate. Conservative at every failure.

    dirty and unpushed are never removable; unknown is "I could not even see
    a git checkout", which must not be confused with "nothing here" (the same
    rule applies to every unknown state).
    """
    res = _git(path, "rev-parse", "--is-inside-work-tree")
    if res.returncode != 0 or res.stdout.strip() != "true":
        return STATE_UNKNOWN
    res = _git(path, "status", "--porcelain")
    if res.returncode != 0:
        return STATE_UNKNOWN
    if res.stdout.strip():
        return STATE_DIRTY
    # A commit is on a remote when a remote-tracking branch contains it.
    # `branch -r --contains HEAD` lists the remote branches whose tip has HEAD
    # in its history, which is exactly "these commits are recoverable from a
    # remote". Empty output, or an unborn HEAD that git refuses to resolve,
    # both answer "not on any remote", and both must refuse removal.
    res = _git(path, "branch", "-r", "--contains", "HEAD")
    if res.returncode != 0 or not res.stdout.strip():
        return STATE_UNPUSHED
    return STATE_CLEAN


def scan(root: pathlib.Path, repos: list[str]):
    """(candidates, total_dirs, owned_here) for one worktrees root.

    A candidate is a directory under the root whose resolved path no repo
    registered. The registration is the whole test: a directory with a live
    driver is registered and therefore owned, so it can never classify as an
    orphan no matter how old or new its files look.
    """
    owned = owned_dirs(repos)
    candidates = []
    total = 0
    owned_here = 0
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        total += 1
        if entry.resolve() in owned:
            owned_here += 1
            continue
        candidates.append((entry, classify(entry)))
    return candidates, total, owned_here


def tree_size(path: pathlib.Path) -> int:
    """On-disk bytes under a candidate, counting only regular files."""
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def human(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def reclaimable(candidates) -> int:
    """The size that cleaning would free: only clean candidates count."""
    return sum(tree_size(p) for p, state in candidates if state == STATE_CLEAN)


def apply_clean(candidates) -> tuple[list[str], list[tuple[str, str]]]:
    """Remove the clean candidates. Returns (removed names, failures).

    A removal that fails (a lingering process holds the folder, WinError 32)
    is REPORTED, never swallowed: a clean-looking candidate that did not come
    off is the same shape as the leak this tool exists to fix.
    """
    removed = []
    failures = []
    for path, state in candidates:
        if state != STATE_CLEAN:
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            failures.append((path.name, str(exc)))
        else:
            removed.append(path.name)
    return removed, failures


def known_repos() -> list[str]:
    """The memory bank checkout, as an absolute path."""
    return [str(pathlib.Path(machinelib.memory_bank_root()).resolve())]


def _init_repo(path: pathlib.Path) -> None:
    run(["git", "init", "-b", "main", str(path)])
    run(["git", "-C", str(path), "config", "user.email",
         "self-test"])
    run(["git", "-C", str(path), "config", "user.name", "Self Test"])


def _commit(repo: pathlib.Path, rel: str, content: str, msg: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run(["git", "-C", str(repo), "add", "-A"])
    res = run(["git", "-C", str(repo), "commit", "-m", msg])
    if res.returncode != 0:
        raise RuntimeError(f"fixture commit failed: "
                           f"{res.stderr.strip()[:200]}")


def self_test() -> int:
    """Prove the classification and the --apply refusal on a temp world.

    Every fixture lives under one temporary directory; the real worktrees
    root is never read here. The `unpushed` fixture is the one that matters
    most: it holds a commit that is NOT on any remote branch, which is exactly
    the unlanded-lane shape --apply must refuse.
    """
    fails = []
    checks = 0

    def want(label: str, cond: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}")
        if not cond:
            fails.append(label)

    with tempfile.TemporaryDirectory(prefix="orphan-selftest-",
                                     ignore_cleanup_errors=True) as td:
        tmp = pathlib.Path(td)
        root = tmp / "worktrees"
        root.mkdir()

        # KNOWN REPO A. Its worktree under the root is registered, so it is
        # owned and must never be reported.
        repo_a = tmp / "repoA"
        _init_repo(repo_a)
        _commit(repo_a, "base.txt", "base\n", "base")
        owned_a = root / "ownedA"
        run(["git", "-C", str(repo_a), "worktree", "add", "-b", "ownedA",
             str(owned_a)])

        # REPO B OWNS THE ORPHANS AND IS NEVER ASKED. It has a bare remote so
        # the `clean` fixture has somewhere real to push to and the `unpushed`
        # fixture can prove a commit stays local even though a remote exists.
        repo_b = tmp / "repoB"
        _init_repo(repo_b)
        _commit(repo_b, "base.txt", "base\n", "base")
        bare = tmp / "repoB-bare.git"
        run(["git", "-C", str(repo_b), "init", "--bare", str(bare)])
        run(["git", "-C", str(repo_b), "remote", "add", "origin", str(bare)])
        run(["git", "-C", str(repo_b), "push", "-u", "origin", "main"])

        orphan_b = root / "orphanB"   # dirty: an uncommitted change
        run(["git", "-C", str(repo_b), "worktree", "add", "-b", "orphanB",
             str(orphan_b)])
        (orphan_b / "new.txt").write_text("uncommitted\n", encoding="utf-8")

        orphan_c = root / "orphanC"   # unpushed: a commit on no remote branch
        run(["git", "-C", str(repo_b), "worktree", "add", "-b", "orphanC",
             str(orphan_c)])
        _commit(orphan_c, "work.txt", "unpushed work\n", "unpushed work")

        orphan_d = root / "orphanD"   # clean: a commit reachable from a remote
        run(["git", "-C", str(repo_b), "worktree", "add", "-b", "orphanD",
             str(orphan_d)])
        _commit(orphan_d, "work.txt", "x" * 2048, "pushed work")
        res = run(["git", "-C", str(orphan_d), "push", "-u", "origin",
                   "orphanD"])
        if res.returncode != 0:
            raise RuntimeError(f"fixture push failed: "
                               f"{res.stderr.strip()[:200]}")

        notgit = root / "notgit"      # unknown: not a git checkout at all
        notgit.mkdir()
        (notgit / "loose.txt").write_text("loose\n", encoding="utf-8")

        repos = [str(repo_a)]
        candidates, total, owned_here = scan(root, repos)
        names = {p.name: state for p, state in candidates}

        want("a directory registered to a repo is NOT reported",
             "ownedA" not in names)
        want("an unregistered directory IS reported",
             all(n in names for n in
                 ("orphanB", "orphanC", "orphanD", "notgit")))
        want("a non-git directory is reported too", "notgit" in names)
        want("scan counted the owned worktree", owned_here == 1 and total == 5)

        want("uncommitted changes classify dirty",
             names.get("orphanB") == STATE_DIRTY)
        want("a commit on no remote branch classifies unpushed",
             names.get("orphanC") == STATE_UNPUSHED)
        want("commits all on a remote classify clean",
             names.get("orphanD") == STATE_CLEAN)
        want("a non-git directory classifies unknown",
             names.get("notgit") == STATE_UNKNOWN)

        # WITHOUT --apply NOTHING IS DELETED: the classification pass is the
        # whole read-only surface of the tool.
        for name in ("orphanB", "orphanC", "orphanD", "notgit", "ownedA"):
            want(f"read-only run leaves {name} in place",
                 (root / name).is_dir())

        want("reclaimable size counts only clean candidates",
             reclaimable(candidates) == tree_size(orphan_d))
        want("the clean fixture contributes size", tree_size(orphan_d) > 0)

        # --apply removes exactly the clean candidate and refuses the rest.
        code = main(["--root", str(root), "--repos", str(repo_a), "--apply"])
        want("--apply exits 0", code == 0)
        want("--apply removed the clean orphan",
             not (root / "orphanD").is_dir())
        want("--apply refused the dirty orphan", (root / "orphanB").is_dir())
        want("--apply refused the unpushed orphan",
             (root / "orphanC").is_dir())
        want("--apply refused the unknown orphan", (root / "notgit").is_dir())
        want("--apply left the owned worktree alone",
             (root / "ownedA").is_dir())

        # A second scan agrees the clean one is gone and the refused ones are
        # still candidates.
        again, _t, _o = scan(root, repos)
        again_names = {p.name: s for p, s in again}
        want("after apply the clean candidate is no longer scanned",
             "orphanD" not in again_names)
        want("after apply the refused candidates are still reported",
             all(n in again_names for n in ("orphanB", "orphanC", "notgit")))

    if fails:
        print(f"\norphan-check self-test FAIL: "
              f"{len(fails)} failing assertion(s)")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"\norphan-check self-test PASS: {checks} assertions")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="orphan-check",
        description="Report directories under the worktrees root that no "
                    "known repo owns; --apply removes only `clean` ones.")
    ap.add_argument("--root",
                    help="worktrees root (default: the memory bank's)")
    ap.add_argument("--repos",
                    help="comma-separated repo paths to ask (default: "
                         "the memory bank checkout)")
    ap.add_argument("--apply", action="store_true",
                    help="remove `clean` orphan candidates")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()

    root = (pathlib.Path(a.root)
            if a.root else machinelib.memory_bank_root().parent / "worktrees")
    if a.repos:
        repos = [p.strip() for p in a.repos.split(",") if p.strip()]
    else:
        repos = known_repos()

    try:
        candidates, total, owned_here = scan(root, repos)
    except FileNotFoundError:
        print(f"orphan-check: {root} does not exist", file=sys.stderr)
        return 1

    clean_count = sum(1 for _p, s in candidates if s == STATE_CLEAN)
    print(f"orphan-check: scanning {root}")
    print(f"  {total} directories, {owned_here} owned by a repo, "
          f"{len(candidates)} orphan candidate(s)")
    for path, state in candidates:
        print(f"  {state:9} {path.name:<30} {REASONS[state]}")

    if a.apply:
        removed, failures = apply_clean(candidates)
        for name in removed:
            print(f"  removed  {name}")
        for name, err in failures:
            print(f"  FAILED   {name}: {err}")
        print(f"\nremoved {len(removed)} clean candidate(s), "
              f"{len(failures)} removal(s) failed; "
              f"{len(candidates) - clean_count} non-clean refused")
        return 1 if failures else 0

    size = reclaimable(candidates)
    print(f"\nreclaimable if cleaned: {human(size)} across {clean_count} "
          f"clean candidate(s); nothing removed (read-only, pass --apply to "
          f"remove)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
