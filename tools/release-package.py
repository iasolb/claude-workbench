#!/usr/bin/env python3
"""Prepare and publish an approved Python package release.

    python tools/release-package.py <checkout> <version> [--why "<text>"]
    python tools/release-package.py <checkout> <version> --apply
    python tools/release-package.py --self-test

The default is a dry run. The owner decides whether to release, and --apply is
the explicit instruction to update the project version, commit, push, tag, and
push that tag. The checkout must be clean, on main, and level with its remote.

(2026-10-17: repeated package releases motivated this tool.)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.M)
NAME_LINE = re.compile(r'^name\s*=\s*"([^"]+)"\s*$', re.M)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def refusal(repo: Path, version: str) -> str:
    project = repo / "pyproject.toml"
    if not project.is_file():
        return f"{repo} has no pyproject.toml"
    current_versions = VERSION_LINE.findall(project.read_text(encoding="utf-8"))
    if len(current_versions) != 1:
        return f"pyproject.toml has {len(current_versions)} version lines, not exactly one"
    try:
        if parse_version(version) <= parse_version(current_versions[0]):
            return f"{version} is not greater than the current {current_versions[0]}"
    except ValueError:
        return f"versions must be dotted integers, got {version} and {current_versions[0]}"
    if git(repo, "branch", "--show-current").stdout.strip() != "main":
        return "the checkout is not on main"
    if git(repo, "status", "--porcelain").stdout.strip():
        return "the checkout has uncommitted changes"
    fetched = git(repo, "fetch", "origin", "main", "--tags")
    if fetched.returncode:
        return f"could not fetch origin: {fetched.stderr.strip()[:120]}"
    if git(repo, "rev-parse", "HEAD").stdout.strip() != git(repo, "rev-parse", "origin/main").stdout.strip():
        return "main is not level with origin/main"
    if git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{version}").returncode == 0:
        return f"tag v{version} already exists"
    return ""


def release(repo: Path, version: str, why: str, apply: bool) -> int:
    problem = refusal(repo, version)
    if problem:
        print(f"release-package: REFUSED, nothing changed: {problem}")
        return 2
    project = repo / "pyproject.toml"
    text = project.read_text(encoding="utf-8")
    name = (NAME_LINE.findall(text) or [repo.name])[0]
    message = f"Release {version}" + (f": {why}" if why else "")
    if not apply:
        print(f"release-package: would release {name} {version} from {repo} (commit {message!r}, tag v{version}). Re-run with --apply.")
        return 0
    project.write_text(VERSION_LINE.sub(f'version = "{version}"', text, count=1),
                       encoding="utf-8", newline="\n")
    steps = (("commit", "-q", "-m", message, "--", "pyproject.toml"),
             ("push", "-q", "origin", "main"),
             ("tag", f"v{version}"),
             ("push", "-q", "origin", f"v{version}"))
    for step in steps:
        result = git(repo, *step)
        if result.returncode:
            print(f"release-package: FAILED at git {step[0]}: {(result.stderr or result.stdout).strip()[:200]}")
            return 1
    print(f"release-package: tagged {name} v{version} and pushed; check its package index entry")
    return 0


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
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        for key, value in (("user.name", "Test User"), ("user.email", "test-user")):
            git(clone, "config", key, value)
        (clone / "pyproject.toml").write_text('[project]\nname = "sample-package"\nversion = "0.1.3"\n', encoding="utf-8")
        git(clone, "add", "pyproject.toml")
        git(clone, "commit", "-q", "-m", "initial", "--", "pyproject.toml")
        git(clone, "push", "-q", "-u", "origin", "main")
        want("a non-increasing version is refused", "not greater" in refusal(clone, "0.1.3"))
        want("a non-numeric version is refused", "dotted integers" in refusal(clone, "0.1.4rc1"))
        (clone / "stray.txt").write_text("x", encoding="utf-8")
        want("a dirty checkout is refused", "uncommitted" in refusal(clone, "0.1.4"))
        (clone / "stray.txt").unlink()
        want("a clean, current checkout accepts a higher version", refusal(clone, "0.1.4") == "")
        want("dry run does not change the version", release(clone, "0.1.4", "maintenance", False) == 0 and
             'version = "0.1.3"' in (clone / "pyproject.toml").read_text(encoding="utf-8"))
        want("apply updates, commits, and pushes the version tag", release(clone, "0.1.4", "maintenance", True) == 0 and
             "v0.1.4" in subprocess.run(["git", "--git-dir", str(origin), "tag", "--list"], capture_output=True, text=True).stdout)
        want("an existing tag is refused", "already exists" in refusal(clone, "0.1.9"))
    print(f"release-package self-test: {checks - failures}/{checks} checks passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="release-package")
    parser.add_argument("checkout", nargs="?")
    parser.add_argument("version", nargs="?")
    parser.add_argument("--why", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.checkout or not args.version:
        parser.error("a checkout and version are required")
    return release(Path(args.checkout).expanduser(), args.version, args.why, args.apply)


if __name__ == "__main__":
    sys.exit(main())
