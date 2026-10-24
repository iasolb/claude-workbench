#!/usr/bin/env python3
"""check-tool-contracts.py: tools may not scrape each other's prose, and an
assertion may not be decoration.

    python tools/check-tool-contracts.py            # report, exit 1 on anything NEW
    python tools/check-tool-contracts.py --list     # every finding, baselined or not
    python tools/check-tool-contracts.py --bless    # accept today's findings as the baseline
    python tools/check-tool-contracts.py --self-test

WHY (2026-08-30: "maybe we should be tagging tools as machine / human", and
"seems like we should just fuckin ban want(True, ...)").

TWO FAULTS, both measured that day, both previously fixed one instance at a time
and both back within the week.

1. ONE TOOL SCRAPING ANOTHER'S HUMAN OUTPUT. `lane-block.py` searched
   `drive-lane.py --list` for a token that listing has never contained, so it
   could never see a running driver, silently, for its whole life. Fourteen sites
   do this; four of them ignore a machine-readable option that already exists.

2. AN ASSERTION THAT CANNOT FAIL. A `want(True, ...)` is a green line that proves
   nothing. This file's own author wrote two of them the same day, hours after
   fixing the historical case where `want()` returned `None` and 23 of 35
   assertions had never executed.

THE TAG, the idea that makes fault 1 checkable rather than
remembered. A tool that another tool is allowed to read declares it in its
docstring:

    CONTRACT: machine

Meaning it has a stable, parseable output (a `--json`/`--porcelain`/`--toon`
flag, or a state file it writes) that a sibling may depend on. Everything else is
CONTRACT: human by default, and reading its stdout from another tool is a
finding.

WHAT THIS DELIBERATELY DOES NOT DO. It does not ban parsing third-party output
(`git`, `gh`, `opencode`, `tasklist`): that is unavoidable. `rules/shared/
memory-integrity.md` rule 2 governs those, and requires a dated real-output
fixture in the parser's self-test. This tool only polices tools reading TOOLS.

RATCHET, NOT A WALL. Fourteen known sites cannot be fixed in one pass, so known
findings live in a baseline and only NEW ones fail, the same shape as
`lint-baseline.json`.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
BASELINE = TOOLS / "tool-contracts-baseline.json"

MACHINE_TAG = re.compile(r"^\s*CONTRACT:\s*machine\s*$", re.M | re.I)
MACHINE_FLAGS = ("--json", "--porcelain", "--toon", "--format")

# A sibling tool named inside a subprocess argument list, e.g. "drive-lane.py".
SIBLING = re.compile(r"['\"]([a-z0-9][a-z0-9_-]*)\.py['\"]")


def tool_names() -> set[str]:
    return {p.stem for p in TOOLS.glob("*.py")}


def is_machine(path: pathlib.Path) -> bool:
    """Declared machine-readable, or demonstrably so."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if MACHINE_TAG.search(text):
        return True
    return any(f'"{flag}"' in text or f"'{flag}'" in text for flag in MACHINE_FLAGS)


def scrapes(path: pathlib.Path, names: set[str]) -> list[str]:
    """Sibling tools this file runs as a subprocess AND then reads output from.

    This is a refined detector: only flag a sibling if its output is actually
    consumed by the tool (read into a variable, parsed, or otherwise used),
    not merely if the code mentions stdout or names the sibling.

    Heuristic: identify the subprocess call to the sibling and require that, in
    a window around that call, there is consumption of its output (e.g., a
    variable assigned from subprocess.run(...) or a call like .stdout/.communicate/
    check_output on the result).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    # Quick guard: if there is no reference to a sibling at all, there's nothing
    # to analyze for this file.
    if not any(rf"{name}.py" in text for name in names for rf in ("'", '"')):
        return []

    lines = text.splitlines()
    hits: list[str] = []
    # For each occurrence of a sibling's module name in a string literal, check for
    # consumption patterns within a small window around that line.
    for idx, line in enumerate(lines):
        for m in SIBLING.finditer(line):
            name = m.group(1)
            if name not in names or name == path.stem:
                continue
            # Skip if the line is a comment
            if line.lstrip().startswith("#"):
                continue
            # Look for consumption within a small window around this line
            window_start = max(0, idx - 6)
            window_end = min(len(lines), idx + 7)
            block = "\n".join(lines[window_start:window_end])
            # CONSUMPTION IS READING THE TEXT. `returncode` IS NOT CONSUMPTION,
            # and listing it here was the bug: it made "only checks an exit
            # code" indistinguishable from "parses the output", which is the
            # single distinction this detector exists to draw. A caller that
            # tests an exit code is CLEAN and common; tools/go.py does it
            # everywhere.
            #
            # Assigning the result is not consumption either. `res =
            # subprocess.run(...)` followed by `if res.returncode` is the clean
            # shape, so the assignment alone says nothing.
            consumed = bool(re.search(
                r"\.stdout|\.stderr|\.communicate\(|check_output\(", block))
            if consumed:
                hits.append(name)
                continue
            # Not consumed nearby: this caller runs the sibling and does not read
            # what it printed. Not a finding, and saying so is the point.
    return sorted(set(hits))


def fake_assertions(path: pathlib.Path) -> list[int]:
    """An assertion whose condition is a hard-coded True or False.

    ORDER-AGNOSTIC ON PURPOSE, and the first version was not, which is why it
    reported hundreds of false findings the first time it met the real toolbox.
    This repo has TWO assertion conventions, `want(ok, label)` and
    `check(label, ok)`, so assuming the condition is argument zero flagged every
    single assertion in every file using the other one. An instrument that cries
    wolf is worse than no instrument (rules/shared/memory-integrity.md rule 2),
    and it was caught only by running it against reality rather than against its
    own fixtures.

    A bare `True`/`False` in ANY argument position is the finding, because that
    is decoration under either convention. Strings and numbers are labels or
    detail and are never the condition, so they are not flagged.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []

    # A hard-coded boolean INSIDE a try/except is legitimate and common, and
    # flagging it was this tool's second false-positive class in one sitting:
    #   check("existing target refused", False)   # in the try: must not be reached
    #   check("existing target refused", True)    # in the except: it was refused
    #   want(False, f"a scenario raised: {exc!r}") # a crash marker
    # All three are real control-flow assertions. Only a boolean on the straight
    # line, where nothing can make it go the other way, is decoration.
    # AN if/else GUARD IS CONTROL FLOW TOO, and it is the same argument as the
    # try/except one above (added 2026-09-07, order 76). This shape:
    #
    #     if not (gitrun(...) and gitrun(...)):
    #         check("git init worked in the temp repo", False)
    #     else:
    #         ...the real assertions...
    #
    # is the FAILURE BRANCH of a setup guard. The `False` is hard-coded because
    # reaching that line IS the failure, and it can absolutely go red: it fires
    # whenever git init fails. Both findings this tool reported against
    # tools/lint.py were this, at lines 1566 and 1602, and both were false.
    #
    # Only a boolean on the STRAIGHT LINE is decoration: somewhere nothing can
    # make it go the other way. A branch that another branch can take instead is
    # a decision, and a boolean inside one is reporting which way it went.
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    guarded.add(id(sub))
        # An `if` WITH an `else` is a real two-way decision. A bare `if` is not
        # carved out: a hard-coded boolean under an unguarded condition still
        # deserves the question.
        if isinstance(node, ast.If) and node.orelse:
            for branch in (node.body, node.orelse):
                for stmt in branch:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Call):
                            guarded.add(id(sub))

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in guarded:
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name not in ("want", "check", "assert_", "expect"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, bool):
                out.append(node.lineno)
                break
    return out


def findings() -> list[str]:
    names = tool_names()
    out = []
    for path in sorted(TOOLS.glob("*.py")):
        for callee in scrapes(path, names):
            target = TOOLS / f"{callee}.py"
            if target.exists() and not is_machine(target):
                out.append(f"scrape {path.name} -> {callee}.py")
        for line in fake_assertions(path):
            out.append(f"fake-assertion {path.name}:{line}")
    return sorted(out)


def load_baseline() -> list[str]:
    if not BASELINE.exists():
        return []
    return sorted(json.loads(BASELINE.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--bless", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    # A RATCHET CANNOT BE A GATE FOR WORK THAT IS SUPPOSED TO REDUCE THE COUNT.
    # Measured 2026-08-30: order 06 was gated on this tool's default behaviour,
    # which exits 0 when nothing got WORSE. The driver did nothing at all, the
    # count was unchanged, the gate passed, and the lane was stamped PASSED with
    # zero commits. Doing nothing satisfies a ratchet. `--max` makes the gate
    # assert the count actually came DOWN.
    ap.add_argument("--max", type=int, default=None,
                    help="fail unless the total findings are at most N")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    found = findings()
    if args.bless:
        BASELINE.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
        print(f"baselined {len(found)} known finding(s) into {BASELINE.name}")
        return 0

    base = load_baseline()
    new = [f for f in found if f not in base]
    fixed = [f for f in base if f not in found]

    if args.list:
        for f in found:
            print(("NEW  " if f in new else "known") + "  " + f)

    print(f"tool contracts: {len(found)} finding(s), {len(base)} baselined, "
          f"{len(new)} new, {len(fixed)} fixed")
    for f in fixed:
        print(f"  FIXED (drop it from the baseline): {f}")
    for f in new:
        print(f"  NEW: {f}")
    if args.max is not None and len(found) > args.max:
        print(f"  OVER THE CEILING: {len(found)} findings, at most {args.max} "
              "allowed. Nothing was reduced, so this is not a pass.")
        return 1
    return 1 if new else 0


# --------------------------------------------------------------------------- #

_checks: list[tuple[bool, str, object]] = []


def want(ok: bool, label: str, detail: object = "") -> None:
    _checks.append((bool(ok), label, detail))


def self_test() -> int:
    import shutil
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="contracts-"))
    try:
        human = tmp / "printer.py"
        human.write_text('"""Prints for a person."""\nprint("a  b=1")\n', encoding="utf-8")
        machine = tmp / "emitter.py"
        machine.write_text('"""Emits.\n\nCONTRACT: machine\n"""\n', encoding="utf-8")
        caller = tmp / "reader.py"
        caller.write_text(
            'import subprocess\n'
            'r = subprocess.run(["py", "printer.py"], capture_output=True)\n'
            'x = r.stdout\n', encoding="utf-8")
        exitonly = tmp / "runner.py"
        exitonly.write_text(
            'import subprocess\n'
            'subprocess.run(["py", "printer.py"]).returncode\n', encoding="utf-8")
        # BOTH conventions in one fixture, because the repo really has both and
        # assuming one produced a storm of false findings on first contact.
        faker = tmp / "faker.py"
        faker.write_text(
            'def want(ok, label, detail=""):\n    pass\n'
            'def check(label, ok):\n    pass\n'
            'want(True, "decoration")\n'
            'want(1 + 1 == 2, "real")\n'
            'check("label first, decoration", True)\n'
            'check("label first, real", 1 == 1)\n'
            'try:\n    check("must not be reached", False)\n'
            'except ValueError:\n    check("it was refused", True)\n',
            encoding="utf-8")

        names = {p.stem for p in tmp.glob("*.py")}

        want(scrapes(caller, names) == ["printer"],
             "a tool reading a sibling's stdout is found", scrapes(caller, names))
        want(scrapes(exitonly, names) == [],
             "a tool that only checks an exit code is NOT a finding",
             scrapes(exitonly, names))
        want(is_machine(machine) and not is_machine(human),
             "the CONTRACT: machine tag is what separates them",
             (is_machine(machine), is_machine(human)))

        lines = fake_assertions(faker)
        want(lines == [5, 7],
             "a hard-coded True is found under EITHER argument convention", lines)
        want(6 not in lines and 8 not in lines,
             "and a real condition is left alone under both", lines)
        want(10 not in lines and 12 not in lines,
             "and a boolean inside try/except is a control-flow assertion, "
             "not decoration", lines)

        # AN if/else SETUP GUARD IS THE SAME ARGUMENT (order 76, 2026-09-07).
        # Both findings this tool reported against tools/lint.py were this shape
        # and both were false: the hard-coded False sits in the FAILURE branch
        # of a git-setup guard, so reaching it IS the failure and it can go red.
        guardfile = tmp / "guarded_if.py"
        guardfile.write_text(
            "def t():\n"
            "    if not setup():\n"
            "        check('setup worked', False)\n"
            "    else:\n"
            "        check('the real thing', True)\n"
            "    check('on the straight line', True)\n",
            encoding="utf-8")
        glines = fake_assertions(guardfile)
        want(3 not in glines and 5 not in glines,
             "a boolean in EITHER branch of an if/else is control flow, not "
             "decoration: the failure branch of a setup guard can go red",
             glines)
        want(6 in glines,
             "but a boolean on the STRAIGHT LINE is still a finding, or this "
             "carve-out would blind the whole check", glines)

        # The ast path, not a grep: a STRING containing the text is not a finding.
        stringy = tmp / "stringy.py"
        stringy.write_text('msg = "want(True, \'x\')"\n', encoding="utf-8")
        want(fake_assertions(stringy) == [],
             "text inside a string literal is not an assertion",
             fake_assertions(stringy))

        # And this file's OWN self-test must be clean, or it is preaching.
        want(fake_assertions(pathlib.Path(__file__)) == [],
             "this file contains no fake assertions of its own",
             fake_assertions(pathlib.Path(__file__)))

        bad = [(l, x) for ok, l, x in _checks if not ok]
        for ok, label, detail in _checks:
            print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"  [{detail!r}]"))
        print(f"\n{len(_checks) - len(bad)}/{len(_checks)} checks passed")
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
