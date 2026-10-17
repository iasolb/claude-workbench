#!/usr/bin/env python3
"""Release one of the published packages to PyPI, the way it is done.

    python tools/release-package.py <checkout> <version> [--why "<text>"]
    python tools/release-package.py <checkout> <version> --apply
    python tools/release-package.py --self-test

WHY (A3, third occurrence, 2026-10-17). otter, fred-loader and census-loader
are released by pushing a `v<version>` tag: each repo's tests workflow
publishes to PyPI over a trusted publisher, gated on that tag. The flow was
hand-run for 0.1.0, again for 0.1.1 and 0.1.2, and a third round was due. By
hand it is four git calls per repo with two ways to go wrong: a tag whose
pyproject.toml still names the old version (PyPI refuses the upload and the
publish goes red), or a tag cut from a checkout that is behind GitHub.

A RELEASE IS IAN'S CALL (rules/claude/git-github.md). This tool never decides
to release; it carries out one he approved, and --apply is that approval made
explicit. Without --apply it says what it would do and changes nothing.

REFUSED, each before anything is written:
  * a checkout not on main, not clean, or not level with origin/main
  * a version that is not greater than the one in pyproject.toml
  * a tag that already exists
  * a pyproject.toml without exactly one `version = "..."` line

DECLARED SCOPE: writes `pyproject.toml` in the named checkout, one commit on
its main, one push of main, one tag and its push. Nothing else.

Exit: 0 done or dry run, 1 a git step failed, 2 refused with nothing changed.
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


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def refusal(repo: Path, version: str) -> str:
    """Why this release must not happen, or "" when it may."""
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        return "%s has no pyproject.toml" % repo
    found = VERSION_LINE.findall(pyproject.read_text(encoding="utf-8"))
    if len(found) != 1:
        return "pyproject.toml has %d version lines, not exactly one" % len(found)
    try:
        if parse_version(version) <= parse_version(found[0]):
            return "%s is not greater than the current %s" % (version, found[0])
    except ValueError:
        return "versions must be dotted integers, got %s and %s" % (version, found[0])
    if _git(repo, "branch", "--show-current").stdout.strip() != "main":
        return "the checkout is not on main"
    if _git(repo, "status", "--porcelain").stdout.strip():
        return "the checkout has uncommitted changes"
    fetch = _git(repo, "fetch", "origin", "main", "--tags")
    if fetch.returncode != 0:
        return "could not fetch origin: %s" % fetch.stderr.strip()[:120]
    if (_git(repo, "rev-parse", "HEAD").stdout.strip()
            != _git(repo, "rev-parse", "origin/main").stdout.strip()):
        return "main is not level with origin/main"
    if _git(repo, "rev-parse", "-q", "--verify", "refs/tags/v" + version).returncode == 0:
        return "tag v%s already exists" % version
    return ""


def release(repo: Path, version: str, why: str, apply: bool) -> int:
    problem = refusal(repo, version)
    if problem:
        print("release-package: REFUSED, nothing changed: " + problem)
        return 2
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    name = (NAME_LINE.findall(text) or [repo.name])[0]
    message = "Release %s" % version + (": " + why if why else "")
    if not apply:
        print("release-package: would release %s %s from %s (commit %r, tag v%s). "
              "Re-run with --apply." % (name, version, repo, message, version))
        return 0
    (repo / "pyproject.toml").write_text(
        VERSION_LINE.sub('version = "%s"' % version, text, count=1),
        encoding="utf-8", newline="\n")
    for step in (("commit", "-q", "-m", message, "--", "pyproject.toml"),
                 ("push", "-q", "origin", "main"),
                 ("tag", "v" + version),
                 ("push", "-q", "origin", "v" + version)):
        res = _git(repo, *step)
        if res.returncode != 0:
            print("release-package: FAILED at git %s: %s"
                  % (step[0], (res.stderr or res.stdout).strip()[:200]))
            return 1
    print("release-package: tagged %s v%s and pushed. GitHub publishes it; check "
          "https://pypi.org/simple/%s/" % (name, version, name))
    return 0


def self_test() -> int:
    fails, checks = [], 0

    def want(label: str, cond: bool) -> None:
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        origin, clone = base / "origin.git", base / "clone"
        ident = ["-c", "user.name=t", "-c", "user.email=t"]
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)],
                       capture_output=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)],
                       capture_output=True)
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", "main"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(clone), "config", "user.name", "t"])
        subprocess.run(["git", "-C", str(clone), "config", "user.email", "t"])
        (clone / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "0.1.3"\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(clone), "add", "pyproject.toml"])
        subprocess.run(["git", *ident, "-C", str(clone), "commit", "-q", "-m", "init"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "main"],
                       capture_output=True)

        want("a version not greater than the current one is refused",
             "not greater" in refusal(clone, "0.1.3"))
        want("and a non-numeric version is refused, never guessed",
             "dotted integers" in refusal(clone, "0.1.4rc1"))
        (clone / "stray.txt").write_text("x")
        want("a dirty checkout is refused", "uncommitted" in refusal(clone, "0.1.4"))
        (clone / "stray.txt").unlink()
        want("a clean, level checkout with a greater version may release",
             refusal(clone, "0.1.4") == "")

        rc = release(clone, "0.1.4", "page fixes", apply=False)
        want("a dry run changes nothing", rc == 0 and '"0.1.3"' in
             (clone / "pyproject.toml").read_text(encoding="utf-8"))

        rc = release(clone, "0.1.4", "page fixes", apply=True)
        want("--apply bumps pyproject.toml to the new version", rc == 0 and
             'version = "0.1.4"' in (clone / "pyproject.toml").read_text(encoding="utf-8"))
        tags = subprocess.run(["git", "--git-dir", str(origin), "tag", "--list"],
                              capture_output=True, text=True).stdout.split()
        want("and the tag reaches origin, which is what fires the publish",
             "v0.1.4" in tags)
        log = subprocess.run(["git", "--git-dir", str(origin), "log", "-1",
                              "--format=%s", "main"], capture_output=True,
                             text=True).stdout.strip()
        want("and origin's main carries the release commit", log == "Release 0.1.4: page fixes")
        subprocess.run(["git", "-C", str(clone), "tag", "v0.1.9"])
        want("a version whose tag already exists is refused",
             "already exists" in refusal(clone, "0.1.9"))

        subprocess.run(["git", "-C", str(clone), "reset", "-q", "--hard", "HEAD~1"])
        want("a checkout behind origin is refused",
             "not level" in refusal(clone, "0.1.5"))

    print("\nself-test: %d check(s), %d failure(s)" % (checks, len(fails)))
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release-package")
    ap.add_argument("checkout", nargs="?")
    ap.add_argument("version", nargs="?")
    ap.add_argument("--why", default="", help="one line for the release commit")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.checkout or not args.version:
        ap.error("a checkout and a version are required")
    return release(Path(args.checkout), args.version, args.why, args.apply)


if __name__ == "__main__":
    sys.exit(main())
