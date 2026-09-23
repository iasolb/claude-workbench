#!/usr/bin/env python3
"""promote.py: turn a start request from anywhere into a launchable order.

    python tools/promote.py --request <job_id>   # ask for a start: one event
    python tools/promote.py --apply              # apply every unapplied request
    python tools/promote.py --self-test

WHY (Ian, 2026-09-23): the phone and cloud sessions fire drivers by sending a
command, and the database is the truth for what is launchable. A request is an
append-only `promote_requested` event (db/015_promote_requests.sql), never an
edit to jobs.status, because tools/jobsync.py rewrites that column from the
order file on every launcher pass, and the launcher itself demotes an order to
draft after two failed runs. A row-wins sync would re-promote those and spend
the allowance again; an explicit request cannot be confused with a demotion.

This tool applies each request exactly ONCE, on the machine holding the order
files: draft or blocked becomes pending, in the working tree and NOT committed,
the same convention go.py's claim and release follow (another session may be
mid-edit in this checkout). It then writes `promote_applied` or
`promote_refused` pointing back at the request id, so nothing is applied twice
and a refusal is visible rather than silent. The ordinary sync then carries
pending into the row, and db/014's job_ready fires.

    reads : events, jobs, queue/orders/<stem>.md
    writes: the `status:` line of queue/orders/<stem>.md, and events rows

Exit codes: 0 done, 1 a request was refused or the store refused a statement,
2 usage, 3 the store could not be reached (nothing was written).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import memdb
import workbench

HERE = Path(__file__).resolve().parent

# Only these become pending. running/landed/failed/superseded are refused: a
# request can start work, never resurrect or double-launch it.
PROMOTABLE = ("draft", "blocked")

# A draft carrying either marker was held deliberately and must not launch
# from a phone tap: the first is the untouched new-order.py template, the
# second is the heading intake uses for questions still unanswered.
REFUSE_MARKERS = (
    ("PLACEHOLDER - THE SPEC IS NOT WRITTEN YET",
     "its spec was never written (the new-order placeholder is still in it)"),
    ("## OPEN QUESTIONS",
     "it is held on open questions written into the order itself"),
)


def _machine() -> str:
    try:
        spec = importlib.util.spec_from_file_location(
            "machinelib", HERE / "machinelib.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod.machine_branch()
    except Exception:                                   # noqa: BLE001
        return "unknown"


def frontmatter_status(text: str) -> str | None:
    """The `status:` value of an order's frontmatter, or None if it has none."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    for line in text[:end].splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    return None


def promoted_text(text: str) -> tuple[str | None, str]:
    """(the order text with status pending, "") or (None, why it is refused)."""
    status = frontmatter_status(text)
    if status is None:
        return None, "the order has no frontmatter status to change"
    if status == "pending":
        return None, "it is already pending"
    if status not in PROMOTABLE:
        return None, f"its status is {status}, and only draft or blocked can be started"
    for marker, why in REFUSE_MARKERS:
        if marker in text:
            return None, why
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    lines = ["status: pending" if l.startswith("status:") else l
             for l in head.splitlines()]
    return "\n".join(lines) + rest, ""


def _record(ask, kind: str, job_id: str, request_id, reason: str,
            machine: str) -> None:
    ask("INSERT INTO events (kind, machine, job_id, detail) "
        "VALUES (%s, %s, %s, %s::jsonb)",
        (kind, machine, job_id,
         json.dumps({"request_id": str(request_id), "reason": reason})))


def apply(repo: Path, ask, unapplied, machine: str) -> list[dict]:
    """Apply every unapplied request. `ask(sql, params)` returns rows and
    `unapplied()` returns [request_id, job_id, at] rows, both injectable so the
    self-test never touches the real store or the real queue."""
    results = []
    for request_id, job_id, _at in unapplied():
        rows = ask("SELECT order_path FROM jobs WHERE id = %s", (job_id,))
        outcome, reason = "promote_refused", ""
        if not rows:
            reason = "no such job in the store"
        else:
            rel = rows[0][0]
            path = repo / rel
            if "/done/" in rel.replace("\\", "/"):
                reason = "the order is archived, so it already finished"
            elif not path.is_file():
                reason = f"no order file at {rel}"
            else:
                new, reason = promoted_text(path.read_text(encoding="utf-8"))
                if new is not None:
                    path.write_text(new, encoding="utf-8")
                    outcome, reason = "promote_applied", "now pending"
        _record(ask, outcome, job_id, request_id, reason, machine)
        results.append({"request_id": request_id, "job_id": job_id,
                        "outcome": outcome, "reason": reason})
    return results


def request(job_id: str, ask, machine: str, via: str = "cli") -> int:
    """Write one promote_requested event. 0 written, 1 no such job."""
    if not ask("SELECT 1 FROM jobs WHERE id = %s", (job_id,)):
        print(f"promote: no job {job_id!r} in the store, nothing requested",
              file=sys.stderr)
        return 1
    ask("INSERT INTO events (kind, machine, job_id, detail) "
        "VALUES ('promote_requested', %s, %s, %s::jsonb)",
        (machine, job_id, json.dumps({"via": via})))
    print(f"promote: start requested for {job_id}")
    return 0


def _store_ask(sql: str, params: tuple = ()):
    return memdb._query(sql, params, write=True)


def _store_unapplied():
    return memdb.stored_query("unapplied_promotions")


def self_test() -> int:
    fails, n = [], 0

    def want(cond, label, got=None):
        nonlocal n
        n += 1
        print(("  ok    " if cond else "  FAIL  ") + label)
        if not cond:
            fails.append(f"{label} [got {got!r}]")

    draft = "---\norder: 9\nstatus: draft\nslug: x\n---\n\n# body\n"
    new, why = promoted_text(draft)
    want(new is not None and frontmatter_status(new) == "pending",
         "a draft becomes pending", why)
    want(new is not None and new.count("status:") == 1 and "slug: x" in new
         and new.endswith("# body\n"),
         "and only the status line changes, nothing else in the order", new)
    want(promoted_text(draft.replace("draft", "blocked"))[0] is not None,
         "a blocked order can be started too")
    for status in ("running", "landed", "failed", "superseded"):
        got, why = promoted_text(draft.replace("draft", status))
        want(got is None and status in why,
             f"a {status} order is REFUSED, never resurrected", why)
    got, why = promoted_text(draft.replace("draft", "pending"))
    want(got is None and "already pending" in why,
         "an already-pending order is refused as a no-op, not rewritten", why)
    got, why = promoted_text(draft + "\n**PLACEHOLDER - THE SPEC IS NOT WRITTEN YET.**\n")
    want(got is None and "spec" in why,
         "a draft whose spec was never written is refused", why)
    got, why = promoted_text(draft + "\n## OPEN QUESTIONS, and this stays a draft\n")
    want(got is None and "open questions" in why,
         "a draft held on open questions is refused", why)
    want(promoted_text("no frontmatter at all")[0] is None,
         "an order with no frontmatter is refused, not guessed at")

    # apply() end to end against a fake store and a temp queue.
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "queue" / "orders").mkdir(parents=True)
        (repo / "queue" / "orders" / "9-x.md").write_text(draft, encoding="utf-8")
        (repo / "queue" / "orders" / "8-y.md").write_text(
            draft.replace("draft", "running"), encoding="utf-8")
        paths = {"9-x": "queue/orders/9-x.md", "8-y": "queue/orders/8-y.md",
                 "7-z": "queue/orders/done/7-z.md"}
        events = []

        def ask(sql, params=()):
            if sql.startswith("SELECT order_path"):
                return [[paths[params[0]]]] if params[0] in paths else []
            if sql.startswith("INSERT INTO events"):
                events.append(params)
                return []
            raise AssertionError(sql)

        reqs = [[101, "9-x", None], [102, "8-y", None], [103, "7-z", None],
                [104, "nope", None]]
        out = apply(repo, ask, lambda: reqs, "testbox")
        by = {r["job_id"]: r for r in out}
        want(by["9-x"]["outcome"] == "promote_applied"
             and frontmatter_status((repo / "queue/orders/9-x.md")
                                    .read_text(encoding="utf-8")) == "pending",
             "a requested draft is applied and its FILE now reads pending", by)
        want(by["8-y"]["outcome"] == "promote_refused"
             and frontmatter_status((repo / "queue/orders/8-y.md")
                                    .read_text(encoding="utf-8")) == "running",
             "a running order is refused and its file is untouched", by)
        want(by["7-z"]["outcome"] == "promote_refused"
             and "archived" in by["7-z"]["reason"],
             "an archived order is refused as finished", by)
        want(by["nope"]["outcome"] == "promote_refused"
             and "no such job" in by["nope"]["reason"],
             "an unknown job is refused by name", by)
        want(len(events) == 4
             and {json.loads(e[3])["request_id"] for e in events}
             == {"101", "102", "103", "104"},
             "EVERY request gets exactly one answering event carrying its id, "
             "so the catch-up query never offers it again", events)
        want(all(e[1] == "testbox" for e in events),
             "and each answer names the machine that gave it", events)

    for f in fails:
        print(f"FAIL {f}")
    print(f"\npromote self-test: {n - len(fails)}/{n} checks passed")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promote")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--request", metavar="JOB_ID")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--self-test", action="store_true")
    g.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    try:
        if a.status:
            rows = memdb.stored_query("recent_start_requests")
            if not rows:
                print("promote: no start requests recorded yet")
            for rid, at, job, outcome, reason, status in rows:
                print(f"{rid}  {at:%Y-%m-%d %H:%M}  {job}  "
                      f"{outcome.replace('promote_', '')}  "
                      f"{reason or ''}  (job is {status})")
            return 0
        if a.request:
            return request(a.request, _store_ask, _machine())
        results = apply(workbench.repo_root(), _store_ask, _store_unapplied,
                        _machine())
    except memdb.CouldNotAsk as exc:
        print(f"promote: COULD NOT ASK the store: {exc}", file=sys.stderr)
        return 3
    if not results:
        print("promote: no unapplied start requests")
    for r in results:
        print(f"promote: {r['job_id']}: {r['outcome'].split('_')[1]}, {r['reason']}")
    return 1 if any(r["outcome"] == "promote_refused" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
