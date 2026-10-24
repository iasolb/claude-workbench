#!/usr/bin/env python3
"""agents-in-flight.py: is any configured GitHub agent still working, or awaiting review?

    OPENCODE_AGENT_REPOS=owner/repository python tools/agents-in-flight.py
    # exit 0 nothing in flight, 3 work in flight, 4 could not ask
    python tools/agents-in-flight.py --self-test

Set OPENCODE_AGENT_REPOS to a comma-separated list of owner/repository slugs
before use. The list has no default because each user must select the repositories
they want checked. An unset or empty list is reported as COULD NOT ASK.

The tool asks GitHub every time; nothing is cached. A repository GitHub cannot
answer for is reported as COULD NOT ASK (exit 4), never as quiet.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPOS = tuple(repo.strip() for repo in
              os.environ.get("OPENCODE_AGENT_REPOS", "").split(",")
              if repo.strip())
AGENT_BRANCH = "opencode/"


def gh_json(args: list[str]):
    """Run gh and parse its JSON, or raise RuntimeError naming why it failed."""
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1:] or ["gh failed"])
    return json.loads(p.stdout or "[]")


def running(runs: list[dict]) -> list[str]:
    return [r["url"] for r in runs if r.get("status") in ("queued", "in_progress", "waiting", "pending")]


def agent_prs(prs: list[dict]) -> list[str]:
    return [p["url"] for p in prs if str(p.get("headRefName", "")).startswith(AGENT_BRANCH)]


def check(repos=REPOS, ask=gh_json) -> tuple[list[str], list[str]]:
    """(lines describing work in flight, repos that could not be asked)."""
    if not repos:
        return [], ["OPENCODE_AGENT_REPOS is not configured"]
    lines, unasked = [], []
    for repo in repos:
        try:
            runs = ask(["run", "list", "--repo", repo, "--workflow", "opencode.yml",
                        "--limit", "10", "--json", "status,url"])
            prs = ask(["pr", "list", "--repo", repo, "--state", "open",
                       "--json", "url,headRefName"])
        except (RuntimeError, json.JSONDecodeError, FileNotFoundError) as exc:
            unasked.append(f"{repo}: {exc}")
            continue
        lines += [f"AGENT RUNNING   {u}  (gh run watch <id> --repo {repo})" for u in running(runs)]
        lines += [f"PR TO REVIEW    {u}  (review the diff and its checks, then merge or send it back with /oc)"
                  for u in agent_prs(prs)]
    return lines, unasked


def self_test() -> int:
    bad = 0

    def want(cond: bool, what: str) -> None:
        nonlocal bad
        bad += not cond
        print(("PASS  " if cond else "FAIL  ") + what)

    # Representative `gh ... --json` output shapes.
    runs = [{"status": "completed", "url": "u/1"}, {"status": "in_progress", "url": "u/2"},
            {"status": "queued", "url": "u/3"}]
    prs = [{"url": "p/126", "headRefName": "opencode/issue125-20261023"},
           {"url": "p/132", "headRefName": "dependabot/pip/pytest-cov"}]
    want(running(runs) == ["u/2", "u/3"], "a run in progress or queued is in flight, a completed one is not")
    want(agent_prs(prs) == ["p/126"], "only an opencode/ branch is an agent PR; dependabot is not")

    def fake(args):
        return runs if args[0] == "run" else prs
    lines, unasked = check(("a/b",), fake)
    want(len(lines) == 3 and not unasked, "check reports two runs and one PR for one repo")

    def quiet(args):
        return []
    want(check(("a/b",), quiet) == ([], []), "nothing in flight is an empty answer, not an error")
    want(check((), quiet) == ([], ["OPENCODE_AGENT_REPOS is not configured"]),
         "an unconfigured repository list is COULD NOT ASK, never quiet")

    def broken(args):
        raise RuntimeError("HTTP 401")
    lines, unasked = check(("a/b",), broken)
    want(lines == [] and unasked and "a/b" in unasked[0],
         "a repo gh cannot answer for is COULD NOT ASK, never silence")
    print(f"\n{'PASS' if not bad else 'FAIL'}: {bad} failing")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    if ap.parse_args().self_test:
        return self_test()
    lines, unasked = check()
    for line in lines:
        print(line)
    for u in unasked:
        print(f"COULD NOT ASK   {u}")
    if unasked:
        return 4
    return 3 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
