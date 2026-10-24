#!/usr/bin/env python3
"""issue-job.py: a job becomes a store row, then a GitHub issue, then an agent's work.

    python tools/issue-job.py open --repo <owner>/<repository> --slug bins-read-unit \
        --name "Bins read their unit" --tier nano|luna --body-file spec.md [--gate "<cmd>"]
    python tools/issue-job.py sync        # stamp landed/failed from each issue's state
    python tools/issue-job.py --self-test

WHY (2026-10-23: "keep writing jobs to the DB and then opening issues with
them in git and pointing agents at them"). GitHub agents are the executor
(store:facts/framework/github_agents_are_the_executor), and the jobs table is
still the one record of what work exists (golden rule A7), so every order is a
jobs row FIRST and an issue second. The row's order_path is the issue URL.

TIERS (docs/model-routing.md): nano posts `/oc` (opencode/gpt-5-nano, Zen) for
literal edits; luna posts `/oc-go` (opencode-go/gpt-6-luna, Go) for writing and
judgement. Default to nano.

STORE FIRST, OR NOTHING. A store that cannot be reached stops `open` before an
issue exists (exit 3), so no issue is ever opened that the store does not know.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TIERS = {
    "nano": ("/oc", "opencode/gpt-5-nano"),
    "luna": ("/oc-go", "opencode-go/gpt-6-luna"),
}
EXIT_STORE = 3
EXIT_GITHUB = 4


def assign_comment(tier: str) -> str:
    return f"{TIERS[tier][0]} do this issue exactly as specified and open one pull request."


def status_from_issue(state: str, reason: str | None) -> str:
    """The jobs status an issue's state means. OPEN is still running; a merged
    fix closes its issue as COMPLETED; anything else closed is failed."""
    if state.upper() == "OPEN":
        return "running"
    return "landed" if (reason or "").upper() == "COMPLETED" else "failed"


def gh(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout if p.returncode == 0 else (p.stderr or p.stdout)).strip()


def _jobsync():
    import jobsync  # the store's one writer of jobs rows; reused, never re-derived
    return jobsync


def cmd_open(a) -> int:
    js = _jobsync()
    body = Path(a.body_file).read_text(encoding="utf-8")
    sha = hashlib.sha256(body.encode()).hexdigest()[:12]
    model = TIERS[a.tier][1]
    rc = js._upsert_job(a.slug, a.name, "draft", None, [], a.gate, model,
                        "(issue not opened yet)", sha, repo=a.repo)
    if rc != 0:
        print("issue-job: the store did not take the job row, so no issue was opened", file=sys.stderr)
        return EXIT_STORE
    rc, url = gh(["issue", "create", "--repo", a.repo, "--title", a.name, "--body-file", a.body_file])
    if rc != 0:
        print(f"issue-job: gh could not open the issue (job {a.slug} stays draft): {url}", file=sys.stderr)
        return EXIT_GITHUB
    url = url.splitlines()[-1]
    js._upsert_job(a.slug, a.name, "running", None, [], a.gate, model, url, sha, repo=a.repo)
    rc, out = gh(["issue", "comment", url, "--body", assign_comment(a.tier)])
    if rc != 0:
        print(f"issue-job: opened {url} but could not assign it: {out}", file=sys.stderr)
        return EXIT_GITHUB
    print(f"{a.slug}: {url} assigned to {a.tier} ({model})")
    return 0


def cmd_sync() -> int:
    js = _jobsync()
    rc, rows, err = js._ask(
        "SELECT id, order_path FROM jobs WHERE status = 'running' "
        "AND order_path LIKE 'https://github.com/%/issues/%'")
    if rc != 0:
        return js._store_failure("the running-jobs query", rc, err)
    changed = 0
    for job_id, url in rows:
        rc, out = gh(["issue", "view", url, "--json", "state,stateReason"])
        if rc != 0:
            print(f"issue-job: COULD NOT ASK GitHub about {job_id} ({url}): {out}", file=sys.stderr)
            continue
        info = json.loads(out)
        status = status_from_issue(info.get("state", ""), info.get("stateReason"))
        if status != "running":
            js._ask("UPDATE jobs SET status = %s, updated_at = now() WHERE id = %s", (status, job_id))
            changed += 1
            print(f"{job_id}: {status} ({url})")
    print(f"issue-job sync: {len(rows)} running, {changed} stamped")
    return 0


def self_test() -> int:
    bad = 0

    def want(cond: bool, what: str) -> None:
        nonlocal bad
        bad += not cond
        print(("PASS  " if cond else "FAIL  ") + what)

    want(assign_comment("nano").startswith("/oc ") and "/oc-go" not in assign_comment("nano"),
         "nano posts /oc, which the workflow routes to gpt-5-nano")
    want(assign_comment("luna").startswith("/oc-go "), "luna posts /oc-go, which the workflow routes to Luna")
    want(status_from_issue("OPEN", None) == "running", "an open issue is still running")
    want(status_from_issue("CLOSED", "COMPLETED") == "landed", "an issue closed as completed is landed")
    want(status_from_issue("CLOSED", "NOT_PLANNED") == "failed", "an issue closed as not planned is failed")
    want(status_from_issue("closed", "") == "failed", "a closed issue with no reason is failed, never landed")
    print(f"\n{'PASS' if not bad else 'FAIL'}: {bad} failing")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    o = sub.add_parser("open")
    o.add_argument("--repo", required=True)
    o.add_argument("--slug", required=True)
    o.add_argument("--name", required=True)
    o.add_argument("--tier", choices=sorted(TIERS), default="nano")
    o.add_argument("--body-file", required=True)
    o.add_argument("--gate")
    sub.add_parser("sync")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.cmd == "open":
        return cmd_open(a)
    if a.cmd == "sync":
        return cmd_sync()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
