#!/usr/bin/env python3
"""A gate that COUNTS: run a tool's --self-test and require at least N real checks.

WHY THIS EXISTS. 2026-09-24, order 146: the gate was `ship-recency.py
--self-test`, red before the work and green after, exactly as the rules ask.
The driver wrote a real seven-scenario test, was then asked to run its gate,
and REPLACED the test with `return 0`. The gate stayed green and the lane
landed with no assertions at all. A gate that only reads an exit code cannot
tell a test from its absence (`rules/claude/jobs.md`, "IF THE ORDER ASKS FOR
ASSERTIONS, CAN THE GATE COUNT THEM?").

So the gate reads the count the self-test prints, `N/M passed` on its own
line, the convention every self-test in tools/ already follows, and passes
only when the exit code is 0, N equals M, and N is at least the floor the
order declared.

    python tools/selftest-floor.py <tool.py> <floor>
    python tools/selftest-floor.py --self-test

EXIT CODES, distinct on purpose:
    0  counted, every check passed, and at least <floor> of them
    1  the self-test failed, or passed fewer checks than the floor
    3  NO COUNT LINE: the self-test printed no `N/M passed`, so nothing was
       counted. Never read as a pass (golden rule 3).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

COUNT = re.compile(r"^\s*(?:\S.*?:\s+)?(\d+)/(\d+)\s+(?:checks\s+)?passed\s*$")


def last_count(text: str) -> tuple[int, int] | None:
    """(passed, total) from the LAST count line, or None if there is none."""
    found = None
    for line in text.splitlines():
        m = COUNT.match(line)
        if m:
            found = (int(m.group(1)), int(m.group(2)))
    return found


def judge(rc: int, text: str, floor: int) -> tuple[int, str]:
    """The verdict for one self-test run: (exit code, sentence)."""
    got = last_count(text)
    if got is None:
        return 3, ("NOT COUNTED: the self-test printed no 'N/M passed' line, "
                   "so there is no evidence any check ran")
    passed, total = got
    if rc != 0:
        return 1, f"FAILED: the self-test exited {rc} ({passed}/{total} passed)"
    if passed != total:
        return 1, f"FAILED: {passed}/{total} passed"
    if passed < floor:
        return 1, (f"BELOW THE FLOOR: {passed} check(s) passed, the order "
                   f"requires at least {floor}")
    return 0, f"ok: {passed}/{total} passed, floor {floor}"


def self_test() -> int:
    fails, n = [], 0

    def want(label, cond):
        nonlocal n
        n += 1
        print(("  ok    " if cond else "  FAIL  ") + label)
        if not cond:
            fails.append(label)

    # Verbatim outputs of real tools, 2026-09-24.
    app_restart = "  ok    only lines after the restart count\n4/4 passed\n"
    driver_view = "driver-view self-test: 43/43 checks passed\n"
    gutted = ""                                   # ship-recency.py 0b59afc8
    literal = ("no public workbench clone on this machine, so nothing can be "
               "shipped here\nN/M passed\n")      # ship-recency.py cbc8911f
    want("a plain 'N/M passed' line is counted",
         last_count(app_restart) == (4, 4))
    want("the 'tool: N/M checks passed' form is counted",
         last_count(driver_view) == (43, 43))
    want("a self-test that prints nothing is NOT COUNTED (exit 3), never a pass",
         judge(0, gutted, 1)[0] == 3)
    want("the literal text 'N/M passed' is not a count",
         judge(0, literal, 1)[0] == 3)
    want("fewer checks than the floor fails", judge(0, app_restart, 7)[0] == 1)
    want("enough checks passes", judge(0, app_restart, 4)[0] == 0)
    want("a failed check fails even above the floor",
         judge(0, "3/4 passed\n", 2)[0] == 1)
    want("a nonzero exit fails even with a full count",
         judge(1, app_restart, 1)[0] == 1)
    print(f"{n - len(fails)}/{n} passed")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: selftest-floor.py <tool.py> <floor>", file=sys.stderr)
        return 2
    tool, floor = Path(argv[0]), int(argv[1])
    if not tool.is_file():
        print(f"selftest-floor: {tool} does not exist, so it has no self-test")
        return 1
    run = subprocess.run([sys.executable, str(tool), "--self-test"],
                         capture_output=True, text=True, timeout=600)
    out = run.stdout + run.stderr
    print(out, end="" if out.endswith("\n") or not out else "\n")
    code, sentence = judge(run.returncode, out, floor)
    print(f"selftest-floor: {sentence}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
