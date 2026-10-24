#!/usr/bin/env python3
"""Run a gate and REFUSE it unless its passing count reached a floor.

    python tools/gatefloor.py --min 19 -- python tools/log-to-store.py --self-test

WHY THIS EXISTS, measured 2026-09-20 and it cost a real assertion.

An order asked a driver for four new assertions in `tools/log-to-store.py` and
stated a floor in its prose: "after both edits it must print at least 19". The
driver added the four assertions AND deleted an existing one, so the count went
15 -> 18. The gate was `--self-test`, which exits 0 whenever every assertion it
runs passes, so **the gate went green while a guard was destroyed**. The floor
was the only thing that would have caught it, and the floor was a sentence in a
file nothing executes.

A FLOOR THAT LIVES IN PROSE IS NOT A FLOOR. This makes it an exit code.

`store:facts/lane/an_anchor_at_the_end_of_an_after_block_gets_consumed` has the
incident; `store:facts/lane/a_gate_cannot_detect_an_assertions_only_deliverable`
is the older, more general statement of the same hole.

EXIT CODES, and the third one is the point:

    0  the gate passed AND the count reached the floor
    N  the gate itself exited N, unchanged and passed straight through
    3  the gate passed but NO COUNT COULD BE READ from its output
    4  the gate passed and the count is BELOW the floor

**3 IS NOT 0.** A parser that finds nothing must say so distinctly from a world
where there is nothing (`rules/shared/memory-integrity.md` rule 2). A tool
whose summary line this cannot read is a tool that must not be gated by a floor
until its summary is fixed, and saying that out loud is the whole value.

THIS IS A PROSE SCRAPE AND IT IS DECLARED AS ONE. Nothing else can read a
count out of an arbitrary gate. The mitigation the rule demands is here: every
pattern below carries a fixture that is the callee's REAL output, captured on
the date named, and the self-test asserts against those bytes.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# THE LAST MATCH WINS, NOT THE LARGEST. `kpi.py` takes max() over its matches
# and `store:facts/framework/the_kpi_blind_list_is_a_parser_shape_not_a_missing_test`
# records what that costs: a line reading "(reported, not counted)" can be read
# as a count and INFLATE the number. A summary line is the LAST thing a tool
# prints, so position is the honest selector and a stray earlier number cannot
# win.
# "self-test: 31 check(s), 0 failure(s)"   tools/after-lane.py, 2026-10-17, and
# about a dozen more tools print this shape. Passing is checks minus failures.
# Unreadable here until that day, so a green 31-check run was refused as "no
# passing count" and its order went red for the gate's own blind spot.
RAN_FAILED = re.compile(r"(\d+)\s+check\(s\),\s*(\d+)\s+failure\(s\)")

COUNT_PATTERNS = (
    # "self-test: 26/26 checks passed"            tools/kpi.py, 2026-09-20
    # "log-to-store self-test: 19 assertions passed"          same day
    re.compile(r"(\d+)\s*(?:/\s*\d+)?\s*(?:assertion|check)s?\s+passed"),
    # "36 passed in 5.26s"     pytest on otter/tests/test_pond.py, 2026-09-20
    re.compile(r"(\d+)\s+passed\b"),
)


def read_count(text: str) -> int | None:
    """The passing count in `text`, or None when no pattern matched.

    None is a REAL answer and callers must not coerce it to zero.
    """
    ran_failed = RAN_FAILED.findall(text)
    if ran_failed:
        ran, failed = ran_failed[-1]
        return int(ran) - int(failed)
    for pattern in COUNT_PATTERNS:
        found = pattern.findall(text)
        if found:
            return int(found[-1])
    return None


def run(minimum: int, command: list[str]) -> int:
    proc = subprocess.run(command, capture_output=True, text=True,
                          errors="replace")
    text = (proc.stdout or "") + (proc.stderr or "")
    sys.stdout.write(text)
    if proc.returncode != 0:
        print("\ngatefloor: the gate itself exited %d. The floor was never "
              "reached because the gate did not pass." % proc.returncode)
        return proc.returncode
    count = read_count(text)
    if count is None:
        print("\ngatefloor: the gate PASSED but no passing count could be read "
              "from its output, so the floor of %d could not be checked. This "
              "is NOT a pass: fix the gate's summary line to say "
              "'<n> assertions passed', '<n> checks passed', '<n> passed' or "
              "'<n> check(s), <m> failure(s)' before gating it on a floor."
              % minimum)
        return 3
    if count < minimum:
        print("\ngatefloor: FLOOR NOT REACHED. The gate passed with %d, and "
              "this order requires at least %d. A green gate with a short "
              "count means work the gate cannot see was skipped, or a guard "
              "that already existed was removed." % (count, minimum))
        return 4
    print("\ngatefloor: %d passing, floor %d reached." % (count, minimum))
    return 0


def self_test() -> int:
    checks = [0, 0]

    def want(label: str, condition: bool) -> None:
        checks[0] += 1
        if condition:
            checks[1] += 1
            print("  ok    %s" % label)
        else:
            print("  FAIL  %s" % label)

    # ---- real callee output, captured 2026-09-20, pasted verbatim ----------
    KPI = "self-test: 26/26 checks passed\n"
    LOG = (
        "  ok    a heading with no body is refused, not stored as an empty "
        "finding\n"
        "  ok    a file with no level-2 headings produces no rows and no "
        "crash\n"
        "\nlog-to-store self-test: 19 assertions passed\n"
    )
    PYTEST = (
        "....................................                        [100%]\n"
        "\n36 passed in 5.26s\n"
    )

    want("reads the kpi self-test summary, taking the passing count",
         read_count(KPI) == 26)
    want("reads the log-to-store summary past its per-assertion ok lines",
         read_count(LOG) == 19)
    want("reads a real pytest summary line", read_count(PYTEST) == 36)

    # ---- real callee output, captured 2026-10-17, pasted verbatim ----------
    AFTER_LANE = (
        "  ok    the self-test runs at least 31 checks, so assertions an order "
        "asked for cannot be dropped without this going red (currently 31)\n"
        "\nself-test: 31 check(s), 0 failure(s)\n"
    )
    want("reads the 'N check(s), M failure(s)' summary many tools print",
         read_count(AFTER_LANE) == 31)
    want("and counts only the PASSING ones when some failed",
         read_count("self-test: 31 check(s), 2 failure(s)\n") == 29)

    # THE LAST MATCH WINS. Red-proven by flipping findall()[-1] to [0]: this
    # assertion goes red and the next one does not, so it is the one that
    # pins the behaviour rather than merely agreeing with it.
    want("a later summary beats an earlier number, so a progress line cannot "
         "be mistaken for the count",
         read_count("3 passed in 0.1s\n\n41 passed in 9.9s\n") == 41)
    want("and the largest number does NOT win, which is how an inflated "
         "count gets in", read_count("99 collected\n7 passed in 0.1s\n") == 7)

    # NONE IS A REAL ANSWER, DISTINCT FROM ZERO. This is the assertion the
    # whole tool exists for.
    want("output with no recognisable count returns None, never 0",
         read_count("everything is fine\n") is None)
    want("and zero passing is read as 0, which is NOT the same as unreadable",
         read_count("0 passed in 0.1s\n") == 0)

    # ---- the exit codes, against a real subprocess -------------------------
    ok = [sys.executable, "-c", "print('self-test: 26/26 checks passed')"]
    want("a gate that passes and clears the floor exits 0", run(20, ok) == 0)
    want("a gate that passes BELOW the floor exits 4, not 0", run(27, ok) == 4)
    want("a gate that passes with no readable count exits 3, which is "
         "distinct from both pass and floor-miss",
         run(1, [sys.executable, "-c", "print('done')"]) == 3)
    want("a gate that FAILS passes its own exit code through untouched, so "
         "the floor never masks a red gate",
         run(1, [sys.executable, "-c",
                 "import sys; print('nope'); sys.exit(7)"]) == 7)

    print("\ngatefloor self-test: %d/%d checks passed" % (checks[1], checks[0]))
    return 0 if checks[1] == checks[0] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gatefloor")
    ap.add_argument("--min", type=int, dest="minimum",
                    help="the passing count the gate must reach")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- then the gate command to run")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    command = [a for a in args.command if a != "--"]
    if args.minimum is None or not command:
        ap.error("both --min and a command after -- are required, as in: "
                 "gatefloor.py --min 19 -- python tools/lint.py --self-test")
    return run(args.minimum, command)


if __name__ == "__main__":
    raise SystemExit(main())
