#!/usr/bin/env python3
"""ship.py: gate, branch, commit, push, open the pull request, merge, report.

    python tools/ship.py --repo <path> --branch <name> --title "<one line>"
                         --body-file <path> --gate "<command>" -- <path> [<path>...]
    python tools/ship.py ... --dry-run     # print the plan, touch nothing
    python tools/ship.py --self-test

WHY THIS EXISTS (golden rule A2, stated 2026-08-30: "everything that can be is a n8n
workflow or command you can run and register").

On 2026-08-30 this exact sequence was hand-run TEN times in one session, about
nine tool calls each: fetch, branch off origin/main, add the untracked files,
commit by pathspec, push -u, write a body file, `gh pr create`, `gh pr merge`,
then report the URL. None of it needed judgement, and doing it by hand cost two
mistakes that reached main: one branch cut off a feature branch instead of main
so it could not merge, and one merge made with a red guard because only two test
files had been run instead of the suite.

Both of those are prevented here rather than remembered:

  * the branch is ALWAYS cut from `origin/main` after a fetch, never from HEAD
  * the gate RUNS, and a non-zero exit stops everything before a branch exists

Host-side, standard library only (this repo has no .venv).

Exit codes, so a caller can tell shipped from stopped without reading prose:
    0  merged, and the pull-request URL is on stdout
    2  bad arguments
    3  red gate: stopped before anything was created
    4  a git step failed
    5  the pull request could not be opened
    6  opened but could not be merged; the URL is still printed, nothing is lost
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

STEP = "ship:"


def run(cmd: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def say(msg: str) -> None:
    print(f"{STEP} {msg}", flush=True)


def pr_url(text: str) -> str:
    """The pull-request URL out of `gh pr create` output, which prints it alone
    on a line but may print notices around it."""
    m = re.search(r"https://github\.com/\S+/pull/\d+", text)
    return m.group(0) if m else ""


def ship(repo: pathlib.Path, branch: str, title: str, body_file: pathlib.Path,
         gate: str, paths: list[str], base: str = "main",
         dry_run: bool = False) -> int:
    if not paths:
        say("REFUSED: no paths given. Committing by pathspec is not optional; a "
            "bare commit takes whatever a parallel session staged.")
        return 2
    if not body_file.exists():
        say(f"REFUSED: body file {body_file} does not exist")
        return 2

    # THE GATE FIRST, ALWAYS, and before a branch exists. A gate run after the
    # branch is a gate you are tempted to argue with.
    say(f"gate: {gate}")
    if dry_run:
        say("dry-run: gate not executed")
    else:
        g = subprocess.run(gate, cwd=repo, shell=True, text=True)
        if g.returncode != 0:
            say(f"RED GATE (exit {g.returncode}). Nothing was created.")
            return 3

    steps: list[list[str]] = [
        ["git", "-C", str(repo), "fetch", "origin"],
        # From origin/<base>, never from HEAD. A branch cut off the previous
        # feature branch carries an already-squashed commit and cannot merge.
        ["git", "-C", str(repo), "checkout", "-b", branch, f"origin/{base}"],
    ]
    # `commit -- <path>` SILENTLY SKIPS UNTRACKED FILES, so add first. This is the
    # one that bites on a pass that creates a test file.
    steps.append(["git", "-C", str(repo), "add", *paths])
    steps.append(["git", "-C", str(repo), "commit", "-m", title, "--", *paths])
    steps.append(["git", "-C", str(repo), "push", "-u", "origin", branch])

    for cmd in steps:
        say(" ".join(cmd[3:] if cmd[:2] == ["git", "-C"] else cmd))
        if dry_run:
            continue
        r = run(cmd)
        if r.returncode != 0:
            say(f"FAILED: {r.stderr.strip() or r.stdout.strip()}")
            return 4

    create = ["gh", "pr", "create", "--head", branch, "--base", base,
              "--title", title, "--body-file", str(body_file)]
    say(" ".join(create))
    if dry_run:
        say("dry-run: stopping before the pull request")
        return 0
    c = run(create, cwd=repo)
    url = pr_url(c.stdout + c.stderr)
    if c.returncode != 0 or not url:
        say(f"FAILED to open: {c.stderr.strip() or c.stdout.strip()}")
        return 5

    m = run(["gh", "pr", "merge", url, "--squash", "--delete-branch"], cwd=repo)
    if m.returncode != 0:
        say(f"OPENED but not merged: {url}")
        say(f"  {m.stderr.strip() or m.stdout.strip()}")
        return 6

    say(f"MERGED {url}")
    print(url)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--base", default="main")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)
    return ship(pathlib.Path(args.repo), args.branch, args.title,
                pathlib.Path(args.body_file), args.gate, args.paths,
                args.base, args.dry_run)


# --------------------------------------------------------------------------- #

_checks: list[tuple[bool, str, object]] = []


def want(ok: bool, label: str, detail: object = "") -> None:
    _checks.append((bool(ok), label, detail))


def self_test() -> int:
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ship-"))
    body = tmp / "body.md"
    body.write_text("a body", encoding="utf-8")

    # No paths is a refusal, not a plain commit. The whole reason pathspec
    # commits exist here is that sessions share one working tree.
    want(ship(tmp, "b", "t", body, "exit 0", [], dry_run=True) == 2,
         "refuses with no paths, rather than committing the whole index")

    # A missing body file is caught before anything is created.
    want(ship(tmp, "b", "t", tmp / "nope.md", "exit 0", ["x"], dry_run=True) == 2,
         "refuses when the body file does not exist")

    # THE GATE IS NOT SKIPPABLE and a red one stops before any branch exists.
    # Asserted with a command that really fails, not a stubbed return value.
    want(ship(tmp, "b", "t", body, "exit 7", ["x"]) == 3,
         "a red gate stops the whole thing, and nothing was created")

    # The URL parser, against gh's real shape: it prints the URL on its own line,
    # sometimes after a notice line.
    real = ("Warning: 2 uncommitted changes\n"
            "https://github.com/example-owner/example-repo/pull/87\n")
    want(pr_url(real) == "https://github.com/example-owner/example-repo/pull/87",
         "reads the pull-request URL out of gh's real output", pr_url(real))
    want(pr_url("no url here") == "",
         "and returns empty rather than a wrong URL when there is none")

    # THE REAL COMMAND SEQUENCE, recorded from a live run with `run` replaced.
    # dry_run is deliberately OFF here: with it on, no command is ever built, so
    # asserting against a list rebuilt by the test would only prove the test
    # agrees with itself. An earlier version of these two checks did exactly
    # that, including a literal `want(True, ...)`, which is the fault this whole
    # file's provenance is about (rules/shared/memory-integrity.md rule 2).
    plan: list[str] = []
    real_run = globals()["run"]

    def record(cmd, cwd=None):
        plan.append(" ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "https://github.com/o/r/pull/1", "")

    globals()["run"] = record
    try:
        ship(tmp, "feature", "t", body, "exit 0", ["a.py"])
    finally:
        globals()["run"] = real_run

    joined = " | ".join(plan)
    want(any("checkout -b feature origin/main" in c for c in plan),
         "the branch is cut from origin/<base>, never from HEAD", plan)
    want(any(c.endswith("add a.py") for c in plan),
         "untracked paths are ADDED, because commit by pathspec skips them", plan)
    add_at = next(i for i, c in enumerate(plan) if c.endswith("add a.py"))
    commit_at = next(i for i, c in enumerate(plan) if " commit " in c)
    want(add_at < commit_at, "and the add happens BEFORE the commit",
         (add_at, commit_at))
    want(any("commit -m t -- a.py" in c for c in plan),
         "the commit is by pathspec, never a bare commit", plan)
    want("fetch origin" in joined and
         joined.index("fetch origin") < joined.index("checkout -b"),
         "and it fetches before branching, so origin/<base> is current", plan)

    bad = [(l, x) for ok, l, x in _checks if not ok]
    for ok, label, detail in _checks:
        print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"  [{detail!r}]"))
    print(f"\n{len(_checks) - len(bad)}/{len(_checks)} checks passed")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        print(f"SELF-TEST FAILED: {len(bad)} check(s)")
        return 1
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main(sys.argv[1:]))
