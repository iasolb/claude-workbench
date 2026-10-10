#!/usr/bin/env python3
"""
Prunes remote branches that have already been merged into the default branch.

Behavior:
- Default (dry run): prints lines of the form
  would delete <repo>: <branch>
- If --apply is provided, actually deletes those branches on the remote via
  `git push origin --delete <branch>` and prints
  deleted <repo>: <branch>
- Uses origin/<default> as the baseline, and protects common branches:
  main, master, windows, mac, testing, and the default branch itself.
- --self-test runs an internal self-contained test fixture and exits with
  an appropriate summary line: "self-test: N/N checks passed".
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple


def default_branch(repo: Path) -> str:
    """The name origin/HEAD points at, or main when it points nowhere."""
    rc, out, _ = git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo)
    if rc != 0:
        return "main"
    ref = out.strip()
    if ref.startswith("ref: "):
        ref = ref[len("ref: ") :].strip()
    return ref.split("/")[-1]


def unmerged_report(repo: Path) -> Tuple[bool, List[str]]:
    """Every remote branch NOT merged into the default branch, with how many of
    its commits have no patch-equivalent there (git cherry '+' lines). Deletes
    nothing: a branch with unique work is a person's call."""
    rc, _, err = git(["fetch", "--prune", "origin"], repo)
    if rc != 0:
        return False, [f"git fetch failed: {err.strip()}"]
    default = default_branch(repo)
    rc, out, err = git(["branch", "-r", "--no-merged", f"origin/{default}"], repo)
    if rc != 0:
        return False, [f"git branch query failed: {err.strip()}"]
    names = [b for b in normalized_lines(out) if "->" not in b and b != "origin/HEAD"]
    if not names:
        return True, [f"{repo}: no unmerged branches"]
    messages: List[str] = []
    for b in sorted(names):
        name = b.split("origin/", 1)[-1]
        rc, out, err = git(["cherry", f"origin/{default}", b], repo)
        if rc != 0:
            return False, [f"git cherry {b} failed: {err.strip()}"]
        unique = sum(1 for line in out.splitlines() if line.startswith("+"))
        if unique:
            messages.append(f"{repo}: {name} has {unique} unique commit(s)")
        else:
            messages.append(f"{repo}: {name} has nothing unique")
    return True, messages


def git(cmd: List[str], repo: Path) -> Tuple[int, str, str]:
    """Run a git command in the given repository path. Returns (rc, stdout, stderr)."""
    full = ["git", "-C", str(repo)] + cmd
    proc = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def normalized_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def prune_repo(repo: Path, apply: bool) -> Tuple[bool, List[str]]:
    """Prune a single repo. Returns (succeeded, messages)."""
    messages: List[str] = []
    ok = True

    # 1) fetch --prune origin
    rc, out, err = git(["fetch", "--prune", "origin"], repo)
    if rc != 0:
        messages.append(f"git fetch failed: {err.strip()}")
        return False, messages

    # 2) determine default branch
    rc, out, err = git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo)
    default = "main"
    if rc == 0:
        ref = out.strip()
        if ref.startswith("ref: "):
            ref = ref[len("ref: ") :].strip()
        # ref is like refs/remotes/origin/main
        default = ref.split("/")[-1]
    # If the command failed, keep default as main

    # 3) candidates = git branch -r --merged origin/<default>
    rc, out, err = git(["branch", "-r", "--merged", f"origin/{default}"], repo)
    if rc != 0:
        messages.append(f"git branch/merge query failed: {err.strip()}")
        return False, messages
    branches = normalized_lines(out)

    # 4) filter: drop origin/HEAD, lines with '->', default, and protected names
    # A MACHINE BRANCH IS EVEN WITH MAIN MOST OF THE TIME, so "merged" says
    # nothing about whether it is dead. `phone` is the cloud sessions' branch
    # (memory-bank-branch-workflow.md); the first dry run, 2026-10-09, would have
    # deleted it. `archive/` branches are kept on purpose.
    protected = {"main", "master", "windows", "mac", "phone", "testing"}
    to_drop = {f"origin/{default}"}
    remaining: List[str] = []
    for b in branches:
        if b == "origin/HEAD":
            continue
        if "->" in b:
            continue
        # b is like origin/<name>; drop if equals default or protected
        if b in to_drop:
            continue
        name = b.split("origin/", 1)[-1]
        if name in protected or name.startswith("archive/"):
            continue
        remaining.append(name)

    if not remaining:
        messages.append(f"{repo}: nothing merged to prune")
        return True, messages

    remaining.sort()
    # 5) print would delete lines
    for name in remaining:
        messages.append(f"would delete {repo}: {name}")

    if not apply:
        return True, messages

    # Apply: delete each remote branch
    for name in remaining:
        rc, out, err = git(["push", "origin", "--delete", name], repo)
        if rc != 0:
            messages.append(f"git push --delete {name} failed: {err.strip()}")
            ok = False
        else:
            messages.append(f"deleted {repo}: {name}")
    return ok, messages


def self_test() -> int:
    """Run a self-contained test fixture entirely in a temp directory.
    Returns 0 on success, non-zero on failure.
    The test creates a bare origin.git, clones it, creates a main branch,
    creates and merges a merged-work branch, and creates unmerged branches
    (open-work, windows). It then verifies that the dry-run reports only
    merged-work for pruning, and that applying deletes merged-work on the
    origin bare repo actually removes it while leaving the others.
    """
    checks = 0
    total = 9
    temp = tempfile.TemporaryDirectory()
    base = Path(temp.name)

    try:
        origin_bare = base / "origin.git"
        # 1. create bare origin
        rc, _, err = git(["init", "--bare", str(origin_bare.name)], base)
        if rc != 0:
            print(f"self-test: 0/{total} checks passed")
            return 1
        # However above only creates a bare repo at origin.git path; ensure path exists
        # Create actual bare repo at origin_bare path using proper path
        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "--bare"], cwd=str(base / origin_bare.name))

        clone_dir = base / "clone"
        rc, _, _ = git(["clone", str(origin_bare)], clone_dir.parent)
        if rc != 0:
            print(f"self-test: 0/{total} checks passed")
            return 1
        # Clone path should exist now
        clone_path = clone_dir
        # Ensure clone exists by re-cloning properly
        subprocess = __import__("subprocess")
        subprocess.run(["git", "clone", str(origin_bare), str(clone_path)], cwd=str(base))
        # configure user in clone
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(clone_path))
        # Not an address: the public-mirror scanner refuses anything shaped like one.
        subprocess.run(["git", "config", "user.email", "prune-merged-self-test"], cwd=str(clone_path))

        # 2. create initial commit on main and push to origin
        subprocess.run(["git", "checkout", "-b", "main"], cwd=str(clone_path))
        p = Path(clone_path) / "README.md"
        p.write_text("initial\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(clone_path))
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(clone_path))
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(clone_path))

        # 3. merged-work: commit on it, merge into main, push both
        subprocess.run(["git", "checkout", "-b", "merged-work"], cwd=str(clone_path))
        (clone_path / "merged.txt").write_text("merged")
        subprocess.run(["git", "add", "merged.txt"], cwd=str(clone_path))
        subprocess.run(["git", "commit", "-m", "merge base"], cwd=str(clone_path))
        subprocess.run(["git", "push", "origin", "merged-work"], cwd=str(clone_path))
        subprocess.run(["git", "checkout", "main"], cwd=str(clone_path))
        subprocess.run(["git", "merge", "merged-work"], cwd=str(clone_path))
        subprocess.run(["git", "push", "origin", "main"], cwd=str(clone_path))

        # 4. open-work: unmerged branch
        subprocess.run(["git", "checkout", "-b", "open-work"], cwd=str(clone_path))
        (clone_path / "open.txt").write_text("open")
        subprocess.run(["git", "add", "open.txt"], cwd=str(clone_path))
        subprocess.run(["git", "commit", "-m", "open work"], cwd=str(clone_path))
        subprocess.run(["git", "push", "origin", "open-work"], cwd=str(clone_path))

        # 5. windows branch from main
        subprocess.run(["git", "checkout", "main"], cwd=str(clone_path))
        subprocess.run(["git", "checkout", "-b", "windows"], cwd=str(clone_path))
        (clone_path / "windows.txt").write_text("win")
        subprocess.run(["git", "add", "windows.txt"], cwd=str(clone_path))
        subprocess.run(["git", "commit", "-m", "windows"], cwd=str(clone_path))
        subprocess.run(["git", "push", "origin", "windows"], cwd=str(clone_path))

        # 6. phone and archive/old sit EVEN with main, so they read as merged;
        # both must survive --apply.
        subprocess.run(["git", "checkout", "main"], cwd=str(clone_path))
        for kept in ("phone", "archive/old"):
            subprocess.run(["git", "branch", kept, "main"], cwd=str(clone_path))
            subprocess.run(["git", "push", "origin", kept], cwd=str(clone_path))

        # set HEAD of origin to main in clone as per instructions
        subprocess.run(["git", "remote", "set-head", "origin", "main"], cwd=str(clone_path))

        # Run dry-run on the clone
        process = subprocess.run([sys.executable, os.path.abspath(__file__), "--dry-run"], cwd=str(clone_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # The --dry-run flag is not defined; instead, reuse the --self-test path by invoking the script with no args
        # We simulate the dry-run by invoking our own binary's pruning on the clone using the same script
        dry_run = subprocess.run([sys.executable, __file__, str(clone_path)], cwd=str(base), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Simpler: directly call the prune on the clone path using a one-off function would be nicer, but keep self-contained
        # We'll interpret dry_run lines from dry_run.stdout
        if dry_run.returncode != 0:
            print("self-test: 0/5 checks passed")
            return 1
        # Expect a line mentioning merged-work in the output
        if any("merged-work" in line for line in dry_run.stdout.splitlines()):
            checks += 1
        else:
            print("self-test: 1/5 checks passed")
            return 1

        # The unmerged report: open-work has one commit main lacks.
        report = subprocess.run([sys.executable, __file__, str(clone_path), "--unmerged"],
                                cwd=str(base), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = report.stdout.splitlines()
        if any("open-work has 1 unique commit(s)" in line for line in lines):
            checks += 1
        if not any("merged-work" in line for line in lines):
            checks += 1

        # Run apply on the clone to delete merged-work from origin.git
        apply_run = subprocess.run([sys.executable, __file__, str(clone_path), "--apply"], cwd=str(base), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if apply_run.returncode != 0:
            print("self-test: 2/5 checks passed")
            return 1
        # Verify origin.git no longer has merged-work
        ver = subprocess.run(["git", "-C", str(origin_bare.parent), "ls-remote", "--heads", str(origin_bare)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # More robust: list refs/heads on origin.git
        list_heads = subprocess.run(["git", "--git-dir", str(origin_bare), "for-each-ref", "--format=%(refname:short)", "refs/heads/"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        heads = [l.strip() for l in list_heads.stdout.splitlines() if l.strip()]
        if "merged-work" not in heads:
            checks += 1
        else:
            print("self-test: 3/5 checks passed")
            return 1

        # Ensure other branches remain
        for b in ["main", "open-work", "windows", "phone", "archive/old"]:
            if b in heads:
                checks += 1
        if checks < total:
            print(f"self-test: {checks}/{total} checks passed")
            return 1
        print(f"self-test: {checks}/{total} checks passed")
        return 0
    finally:
        temp.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Prune merged remote branches")
    parser.add_argument("repos", nargs="*", help="Paths to repositories to prune")
    parser.add_argument("--apply", action="store_true", help="Apply deletions on remotes")
    parser.add_argument("--unmerged", action="store_true",
                        help="Report unmerged branches and their unique commits; deletes nothing")
    parser.add_argument("--self-test", action="store_true", dest="self_test", help="Run self-test fixture")
    args = parser.parse_args()

    if args.self_test:
        code = self_test()
        sys.exit(code)

    if not args.repos:
        print("No repositories provided.")
        sys.exit(1)

    exit_code = 0
    for repo_path in args.repos:
        repo = Path(repo_path)
        if not repo.exists():
            print(f"{repo}: path does not exist")
            exit_code = 1
            continue
        ok, msgs = unmerged_report(repo) if args.unmerged else prune_repo(repo, args.apply)
        for m in msgs:
            print(m)
        if not ok:
            exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
