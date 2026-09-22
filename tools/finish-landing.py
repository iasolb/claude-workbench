#!/usr/bin/env python3
"""finish-landing.py: the three steps that follow a landing, in order.

`land-lane.py` leaves a landing half-finished: the order file is still in
queue/orders/, the store has not been told, and the lane litter is still on
disk. Three tools fix that and the ORDER MATTERS, because after-lane cannot
see an order that has not been archived and synced yet.

    archive  ->  apply-triage.py --archive
    sync     ->  jobsync.py
    record   ->  after-lane.py --apply

DECIDES ON EXIT CODES, NEVER ON OUTPUT TEXT. Each step's own stdout is printed
verbatim for a person to read; nothing here parses it (golden rule: one tool
must never scrape another tool's prose).

    python3 tools/finish-landing.py
    python3 tools/finish-landing.py --dry-run
    python3 tools/finish-landing.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

TOOLS = pathlib.Path(__file__).resolve().parent

# The fixed order. Changing it breaks the landing, so it lives in ONE place.
STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("archive the finished order out of the live queue",
     ("apply-triage.py", "--archive")),
    ("tell the store about it", ("jobsync.py",)),
    ("record the landing and clear the lane litter",
     ("after-lane.py", "--apply")),
)


def run_step(script: str, args: tuple[str, ...],
             runner=None) -> tuple[int, str]:
    """(exit_code, output). The code decides; the output is for a person."""
    argv = [sys.executable, str(TOOLS / script), *args]
    if runner is not None:
        return runner(argv)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"could not run {script}: {exc}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def finish(dry_run: bool = False, runner=None) -> int:
    """Run every step in order, STOPPING at the first nonzero exit."""
    for n, (why, (script, *args)) in enumerate(STEPS, start=1):
        label = f"[{n}/{len(STEPS)}] {script} {' '.join(args)}".rstrip()
        if dry_run:
            print(f"WOULD RUN {label}  ({why})")
            continue
        print(f"{label}  ({why})")
        code, out = run_step(script, tuple(args), runner=runner)
        if out.strip():
            print(out.rstrip())
        if code != 0:
            print(f"finish-landing: STOPPED at {script}, exit {code}. The "
                  f"remaining steps were NOT run, because they depend on this "
                  f"one.", file=sys.stderr)
            return code
    if not dry_run:
        print("finish-landing: landing finished, all three steps exited 0")
    return 0


def self_test() -> int:
    fails: list[str] = []
    n = 0

    def want(cond, label, got=None):
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{label}  [got {got!r}]")

    want([s for _, s in STEPS][0][0] == "apply-triage.py",
         "archiving comes FIRST, or after-lane cannot see the order")
    want([s for _, s in STEPS][1][0] == "jobsync.py",
         "syncing the store comes second")
    want([s for _, s in STEPS][2][0] == "after-lane.py",
         "recording comes last")

    calls: list[list[str]] = []

    def ok(argv):
        calls.append(argv)
        return 0, "fine"

    want(finish(runner=ok) == 0, "a clean run exits 0")
    want(len(calls) == 3, "and really ran all three steps", len(calls))

    # STOPPING IS THE POINT. A failed archive must not be followed by a sync
    # that records a landing nobody archived.
    calls.clear()

    def fail_first(argv):
        calls.append(argv)
        return 4, "no"

    want(finish(runner=fail_first) == 4,
         "a failing step's exit code is returned, not swallowed")
    want(len(calls) == 1,
         "and NOTHING after it ran, because the steps depend on each other",
         len(calls))

    calls.clear()
    want(finish(dry_run=True, runner=ok) == 0, "a dry run exits 0")
    want(len(calls) == 0, "and runs nothing at all", len(calls))

    for f in fails:
        print(f"FAIL {f}")
    print(f"finish-landing self-test: {n - len(fails)}/{n} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="finish-landing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    return finish(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
