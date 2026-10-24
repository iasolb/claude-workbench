#!/usr/bin/env python3
"""gate-selftest.py: a self-test gate that a VACUOUS self-test cannot pass.

WHY THIS EXISTS. On 2026-09-13 two orders landed on main with `python tools/<x>.py
--self-test` as their gate, and both tools were broken:

  - `branch-litter.py` reported 0 of 40 branches, and its whole self-test was
    `try: main(); return 0; except: return 1`. It asserted NOTHING. It passed by
    not crashing.
  - `pypi-types-check.py` ignored the flag entirely and made live network calls,
    so `--self-test` ran the production path and exited 0.

Both gates were green. Both tools were useless. **An unimplemented or empty
self-test is indistinguishable from a passing one when the only signal is the
exit code**, which is `rules/shared/memory-integrity.md` rule 2 in its purest
form: silence and success looking alike.

So the gate stops being "exit code 0" and becomes "exit 0 AND the run reported
at least N assertions AND zero failures". A tool that asserts nothing now FAILS
its gate, loudly, naming what it was missing.

USE IT AS THE GATE, not the bare tool:

    python tools/gate-selftest.py tools/branch-litter.py --min-checks 4

DECLARED SCOPE, enumerated, never inferred:
  reads   : runs the named tool as a subprocess with --self-test and reads its
            stdout and stderr
  writes  : NOTHING
  decides : exit 0 only when the self-test exits 0, reports at least
            --min-checks assertions, and reports no failures.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# The two shapes every self-test in this repo already prints. Counting BOTH is
# deliberate: the point is to notice a test that ran nothing, not to force one
# house format on tools that predate this.
OK_LINE = re.compile(r"^\s*(ok|PASS)\b", re.M | re.I)
FAIL_LINE = re.compile(r"^\s*(FAIL|failed)\b", re.M | re.I)
SUMMARY = re.compile(r"(\d+)\s+check\(s\).*?(\d+)\s+failure\(s\)", re.I | re.S)


def measure(text: str) -> tuple[int, int]:
    """(checks, failures) from the self-test's output, taking the WORSE of the
    two readings.

    A SUMMARY MUST NEVER BE ABLE TO HIDE A VISIBLE FAILURE, and the first
    version of this let one through within an hour of shipping. Order 113's
    driver printed:

        FAIL    a wheel carrying the Typing :: Typed classifier reads as typed
        ok      one WITHOUT the classifier reads as NOT typed
        FAIL    a payload with no wheel is reported, not crashed on
        ok      a simulated fetch failure reads UNREACHABLE
        self-test: 4 check(s), 0 failure(s)

    Two assertions RED and a summary claiming zero. The old code preferred the
    summary "because it is the tool's own count and cannot disagree with
    itself", which is exactly the assumption a broken tool violates, and the
    gate reported PASS. That is the failure this gate was written to catch,
    committed by the gate.

    So: count both ways and believe the pessimistic one. A summary can raise the
    failure count and can never lower it below the FAIL lines actually printed
    (golden rule 2, a logger can never lie).
    """
    seen_ok = len(OK_LINE.findall(text))
    seen_fail = len(FAIL_LINE.findall(text))
    checks, failures = seen_ok + seen_fail, seen_fail
    m = SUMMARY.search(text)
    if m:
        checks = max(checks, int(m.group(1)))
        failures = max(failures, int(m.group(2)))
    return checks, failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gate-selftest")
    ap.add_argument("tool", nargs="?",
                    help="path to the tool, relative to the repo root")
    ap.add_argument("--min-checks", type=int, default=3,
                    help="fewest assertions that count as a real self-test")
    ap.add_argument("--self-test", action="store_true",
                    help="test THIS tool's own measuring")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()

    target = (REPO / a.tool).resolve()
    if not target.is_file():
        print(f"gate-selftest: {a.tool} does not exist, so its gate cannot pass.",
              file=sys.stderr)
        return 1

    proc = subprocess.run([sys.executable, str(target), "--self-test"],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out, end="" if out.endswith("\n") else "\n")

    checks, failures = measure(out)

    if proc.returncode != 0:
        print(f"gate-selftest: FAILED, the self-test exited {proc.returncode}.",
              file=sys.stderr)
        return 1
    if failures:
        print(f"gate-selftest: FAILED, {failures} assertion(s) went red.",
              file=sys.stderr)
        return 1
    if checks < a.min_checks:
        # SAY WHAT SHAPE IS WANTED, not just that the count was short. Measured
        # 2026-09-13: a driver read `--min-checks 4` as "print four lines" and
        # emitted four FIXTURE lines, which is a reasonable reading of a message
        # that never said otherwise. A gate that refuses without naming the
        # accepted form makes the next attempt another guess.
        print(f"gate-selftest: FAILED, the self-test reported {checks} "
              f"assertion(s) and at least {a.min_checks} were required. "
              f"An exit code of 0 from a test that asserts nothing is not a "
              f"pass: it is the failure mode this gate exists to catch.\n"
              f"WHAT COUNTS AS AN ASSERTION, and printing prose is not it. "
              f"Either one line per check beginning 'ok' or 'PASS' (and 'FAIL' "
              f"for a red one), like:\n"
              f"    ok    a wheel with the Typing :: Typed classifier reads as typed\n"
              f"    ok    one without it reads as NOT typed\n"
              f"or a final summary line of the form:\n"
              f"    self-test: 4 check(s), 0 failure(s)\n"
              f"Each line must be a CHECK that could go red, not a description "
              f"of what the tool does.",
              file=sys.stderr)
        return 1

    print(f"gate-selftest: {checks} assertion(s), 0 failure(s)")
    return 0


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    want("a summary line is read as the tool's own count",
         measure("self-test: 12 check(s), 0 failure(s)") == (12, 0))
    want("and its failure count is read too",
         measure("self-test: 12 check(s), 3 failure(s)") == (12, 3))
    # THE ONE THAT MATTERS, and the one the first version got wrong. Verbatim
    # from order 113's driver on 2026-09-13: two red lines under a summary
    # claiming none. Red-proven by restoring `if m: return ...` at the top of
    # measure(), which makes this the only assertion that fails.
    want("VISIBLE FAIL LINES BEAT A SUMMARY CLAIMING ZERO",
         measure("FAIL  a wheel with the classifier reads as typed\n"
                 "ok    one without it reads as NOT typed\n"
                 "FAIL  a payload with no wheel is reported\n"
                 "ok    a fetch failure reads UNREACHABLE\n"
                 "self-test: 4 check(s), 0 failure(s)\n") == (4, 2))
    want("and a summary may still RAISE a count the lines understate",
         measure("ok  one\nself-test: 9 check(s), 5 failure(s)") == (9, 5))
    want("per-assertion lines are counted when there is no summary",
         measure("  ok  one\n  ok  two\n  ok  three\n") == (3, 0))
    want("PASS lines count as well, for tools using that spelling",
         measure("PASS one\nPASS two\n") == (2, 0))
    # A RED LINE IS STILL A CHECK. The old expectation here was (1, 1), which
    # counted only the `ok` line as a check and quietly dropped the failing one
    # from the total. Two assertions ran; one of them was red.
    want("a red line counts as both a check and a failure",
         measure("  ok  one\n  FAIL  two\n") == (2, 1))
    # THE WHOLE POINT, and the red proof: output from a test that asserted
    # nothing must measure ZERO, so the caller can refuse it. Break `measure`
    # to return (99, 0) and this is the assertion that goes red.
    want("a self-test that printed no assertions measures ZERO checks",
         measure("Building plan...\nDone.\n") == (0, 0))
    want("and an empty run measures zero too", measure("") == (0, 0))

    print("self-test: %d check(s), %d failure(s)" % (checks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
