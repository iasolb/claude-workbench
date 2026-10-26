#!/usr/bin/env python3
"""Report or delete remote branches whose work is already on the default branch.

By default this is a dry run. Squash-merged branches are recognized by patch
equivalence, unique commits are preserved, and archive/ branches are retained.
Additional protected branch names can be set with PRUNE_PROTECTED_BRANCHES or
one or more --protect-branch options.

    python tools/prune-merged.py ~/dev/project
    python tools/prune-merged.py ~/dev/project --unmerged
    python tools/prune-merged.py ~/dev/project --apply
    python tools/prune-merged.py --self-test
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def git(args: list[str], repo: Path) -> tuple[int, str, str]:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode, result.stdout, result.stderr


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def default_branch(repo: Path) -> str:
    code, output, _error = git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo)
    if code != 0:
        return "main"
    ref = output.strip().removeprefix("ref: ")
    return ref.split("/")[-1]


def unmerged_report(repo: Path) -> tuple[bool, list[str]]:
    code, _output, error = git(["fetch", "--prune", "origin"], repo)
    if code:
        return False, [f"git fetch failed: {error.strip()}"]
    default = default_branch(repo)
    code, output, error = git(["branch", "-r", "--no-merged", f"origin/{default}"], repo)
    if code:
        return False, [f"git branch query failed: {error.strip()}"]
    branches = [branch for branch in lines(output)
                if "->" not in branch and branch != "origin/HEAD" and branch.startswith("origin/")]
    if not branches:
        return True, [f"{repo}: no unmerged branches"]
    messages = []
    for branch in sorted(branches):
        name = branch.removeprefix("origin/")
        code, output, error = git(["cherry", f"origin/{default}", branch], repo)
        if code:
            return False, [f"git cherry {name} failed: {error.strip()}"]
        unique = sum(line.startswith("+") for line in output.splitlines())
        if unique:
            messages.append(f"{repo}: {name} has {unique} unique commit(s)")
        else:
            messages.append(f"{repo}: {name} has nothing unique")
    return True, messages


def prune_repo(repo: Path, apply: bool, extra_protected: set[str] | None = None) -> tuple[bool, list[str]]:
    messages = []
    code, _output, error = git(["fetch", "--prune", "origin"], repo)
    if code:
        return False, [f"git fetch failed: {error.strip()}"]
    default = default_branch(repo)
    code, merged, error = git(["branch", "-r", "--merged", f"origin/{default}"], repo)
    if code:
        return False, [f"git branch/merge query failed: {error.strip()}"]
    candidates = set(lines(merged))

    # A squash merge is not an ancestor, but git cherry identifies its equivalent patch.
    code, unmerged, error = git(["branch", "-r", "--no-merged", f"origin/{default}"], repo)
    if code:
        return False, [f"git branch/no-merged query failed: {error.strip()}"]
    for branch in lines(unmerged):
        if "->" in branch or branch == "origin/HEAD":
            continue
        code, cherry, error = git(["cherry", f"origin/{default}", branch], repo)
        if code:
            return False, [f"git cherry {branch} failed: {error.strip()}"]
        marks = [line[0] for line in cherry.splitlines() if line.strip()]
        if marks and "+" not in marks:
            candidates.add(branch)

    protected = {"main", "master", default, *(extra_protected or set())}
    names = set()
    for branch in candidates:
        if not branch.startswith("origin/") or branch == "origin/HEAD" or "->" in branch:
            continue
        name = branch.removeprefix("origin/")
        if name in protected or name.startswith("archive/"):
            continue
        names.add(name)
    if not names:
        return True, [f"{repo}: nothing merged to prune"]
    for name in sorted(names):
        messages.append(f"would delete {repo}: {name}")
    if not apply:
        return True, messages
    ok = True
    for name in sorted(names):
        code, _output, error = git(["push", "origin", "--delete", name], repo)
        if code:
            messages.append(f"git push --delete {name} failed: {error.strip()}")
            ok = False
        else:
            messages.append(f"deleted {repo}: {name}")
    return ok, messages


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)


def self_test() -> int:
    checks = failures = 0

    def want(condition: bool, label: str) -> None:
        nonlocal checks, failures
        checks += 1
        if not condition:
            failures += 1
            print(f"FAIL {label}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        origin, clone = root / "origin.git", root / "clone"
        want(_run(["git", "init", "-q", "--bare", "-b", "main", str(origin)]).returncode == 0,
             "a bare remote initializes")
        want(_run(["git", "clone", "-q", str(origin), str(clone)]).returncode == 0,
             "a working clone is created")
        for key, value in (("user.name", "Test User"), ("user.email", "test-user")):
            _run(["git", "config", key, value], clone)
        (clone / "base.txt").write_text("base\n", encoding="utf-8")
        _run(["git", "add", "base.txt"], clone)
        want(_run(["git", "commit", "-q", "-m", "base"], clone).returncode == 0,
             "the default branch has a base commit")
        _run(["git", "push", "-q", "-u", "origin", "main"], clone)
        _run(["git", "remote", "set-head", "origin", "main"], clone)

        _run(["git", "checkout", "-q", "-b", "merged-work"], clone)
        (clone / "merged.txt").write_text("merged\n", encoding="utf-8")
        _run(["git", "add", "merged.txt"], clone)
        _run(["git", "commit", "-q", "-m", "merged work"], clone)
        _run(["git", "push", "-q", "-u", "origin", "merged-work"], clone)
        _run(["git", "checkout", "-q", "main"], clone)
        _run(["git", "merge", "-q", "--no-ff", "merged-work", "-m", "merge"], clone)
        _run(["git", "push", "-q", "origin", "main"], clone)

        _run(["git", "checkout", "-q", "-b", "squashed-work"], clone)
        (clone / "squashed.txt").write_text("squashed\n", encoding="utf-8")
        _run(["git", "add", "squashed.txt"], clone)
        _run(["git", "commit", "-q", "-m", "squashed work"], clone)
        _run(["git", "push", "-q", "-u", "origin", "squashed-work"], clone)
        _run(["git", "checkout", "-q", "main"], clone)
        _run(["git", "merge", "-q", "--squash", "squashed-work"], clone)
        _run(["git", "commit", "-q", "-m", "squash"], clone)
        _run(["git", "push", "-q", "origin", "main"], clone)

        _run(["git", "checkout", "-q", "-b", "open-work"], clone)
        (clone / "open.txt").write_text("open\n", encoding="utf-8")
        _run(["git", "add", "open.txt"], clone)
        _run(["git", "commit", "-q", "-m", "open"], clone)
        _run(["git", "push", "-q", "-u", "origin", "open-work"], clone)
        _run(["git", "checkout", "-q", "main"], clone)
        for branch in ("maintenance", "archive/old"):
            _run(["git", "branch", branch, "main"], clone)
            _run(["git", "push", "-q", "origin", branch], clone)
        _run(["git", "remote", "set-head", "origin", "main"], clone)

        ok, report = prune_repo(clone, False, {"maintenance"})
        text = "\n".join(report)
        want(ok and "merged-work" in text and "squashed-work" in text,
             "dry run finds ancestor and squash-merged branches")
        want("open-work" not in text and "maintenance" not in text and "archive/old" not in text,
             "unique, configured protected, and archive branches are retained")
        ok, report = unmerged_report(clone)
        want(ok and any("open-work has 1 unique commit(s)" in line for line in report),
             "the unmerged report counts unique commits")
        ok, report = prune_repo(clone, True, {"maintenance"})
        heads = _run(["git", "--git-dir", str(origin), "for-each-ref",
                      "--format=%(refname:short)", "refs/heads/"]).stdout.splitlines()
        want(ok and "merged-work" not in heads and "squashed-work" not in heads,
             "apply removes the two merged branches")
        want(all(branch in heads for branch in ("main", "open-work", "maintenance", "archive/old")),
             "apply leaves unmerged and protected branches intact")
    print(f"prune-merged self-test: {checks - failures}/{checks} checks passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prune merged remote branches")
    parser.add_argument("repos", nargs="*")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--unmerged", action="store_true")
    parser.add_argument("--protect-branch", action="append", default=[])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.repos:
        parser.error("give one or more repository paths")
    protected = {name.strip() for name in os.environ.get("PRUNE_PROTECTED_BRANCHES", "").split(",") if name.strip()}
    protected.update(args.protect_branch)
    result = 0
    for name in args.repos:
        repo = Path(name).expanduser()
        if not repo.exists():
            print(f"{repo}: path does not exist")
            result = 1
            continue
        ok, messages = unmerged_report(repo) if args.unmerged else prune_repo(repo, args.apply, protected)
        for message in messages:
            print(message)
        if not ok:
            result = 1
    return result


if __name__ == "__main__":
    sys.exit(main())
