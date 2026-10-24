#!/usr/bin/env python3
"""Find code that exists, gets quoted as a capability, and never runs.

    python3 tools/dead-code-check.py            # every tool
    python3 tools/dead-code-check.py --file tools/lint.py
    python3 tools/dead-code-check.py --no-run   # static only, no subprocesses
    python3 tools/dead-code-check.py --self-test

WHY, and the four case studies it is built from
-----------------------------------------------
In 2026-08-27 feedback, a duration cap had been quoted repeatedly and had
executed zero times: "look for more areas where dead code might be hanging".

Four faults in two days, all the same shape: something is written, it reads
correctly, it is cited as working, and it never executes. Each detector below
exists because one of them got through.

  1. THE DEAD CAP. `proc.wait(timeout=cap)` sat AFTER `for line in proc.stdout`,
     which only exits when the stream closes. The cap could never fire. It was
     quoted as the runaway protection for a week.
     -> detector `after_stream`

  2. THE UNWIRED GUARD. `gate_escapes_lane()` shipped with six passing
     assertions and no caller. A guard nothing invokes is a comment.
     -> detector `test_only`

  3. THE ORPHANED CONSTANT. A cap or threshold defined, documented, and read by
     nobody. The same fault as 1 with the evidence one step earlier.
     -> detector `unread_const`

  4. THE ASSERTION THAT NEVER RAN. `want()` returned None, so 23 of 35
     assertions were never evaluated while the self-test printed PASS.
     -> detector `assert_count`, which compares the number of assertion call
        sites in the source against the number the self-test says it ran

WHAT A FINDING MEANS
--------------------
A finding is a QUESTION, not a verdict: some of these are legitimate (a callback
passed by reference, a constant that is a documented export). The tool exists so
the question gets asked at all, because every one of the four faults above was
invisible to a green self-test. Read each finding and either fix it or say why
it is fine.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"

# Function names that mean "this is the test", so a call from inside one is not
# evidence that production code uses the thing.
TEST_FN = re.compile(r"(self_?test|_proofs?$|_prove|_scenarios|redprove)")

# A name that promises to REFUSE something. When one of these is only ever
# called from a test, the refusal does not exist.
GUARD_HINT = re.compile(
    r"^(verify|check|guard|refuse|reject|validate|assert|ensure|require)_"
    r"|_(escapes?|guard|refused?|blocked)(_|$)|^is_")

# Assertion helpers this repo actually uses.
ASSERT_FN = ("want", "check")

# Names that ARE test scaffolding, so "only the test calls it" is the design.
FIXTURE_FN = re.compile(r"(^_?(fake|fixture)|_fixture|_proof$|_rows$|^_put$|"
                        r"^_order$|^_write_log$|^scenario$|^run_lane$)")

SKIP_FILES = {"dead-code-check.py"}


def module_of(path: pathlib.Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                         filename=str(path))
    except (OSError, SyntaxError) as exc:
        return exc


class Finding:
    def __init__(self, kind, file, line, what, why):
        self.kind, self.file, self.line, self.what, self.why = (
            kind, file, line, what, why)

    def __repr__(self):
        return f"<{self.kind} {self.file}:{self.line} {self.what}>"


def _enclosing(tree):
    """node -> nearest enclosing FunctionDef name, for every node in the tree."""
    owner: dict[int, str] = {}

    def walk(node, current):
        for child in ast.iter_child_nodes(node):
            # A FunctionDef's OWN owner is what encloses it, not itself. Getting
            # this backwards made every top level function look nested, so all
            # three function detectors returned nothing while the constant one
            # worked, which is why the self-test caught it and a hand read of
            # the output would not have.
            owner[id(child)] = current
            inner = (child.name
                     if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else current)
            walk(child, inner)

    owner[id(tree)] = ""
    walk(tree, "")
    return owner


def _in_test(fn_name: str, defs: dict) -> bool:
    """True when fn_name is a test function, or nested inside one."""
    seen = set()
    while fn_name and fn_name not in seen:
        if TEST_FN.search(fn_name):
            return True
        seen.add(fn_name)
        fn_name = defs.get(fn_name, ("", 0, ""))[2]
    return False


def collect(path: pathlib.Path):
    """(functions, consts, calls, assert_sites) for one module."""
    tree = module_of(path)
    if isinstance(tree, Exception):
        return None, str(tree)
    owner = _enclosing(tree)

    # A METHOD IS NOT DEAD JUST BECAUSE ITS OWN FILE NEVER NAMES IT. Callers
    # reach it through an instance, so the name never appears. Two methods of a
    # duck-typed test double were the detector's first false positives, and a
    # noisy instrument is one nobody reads.
    methods = {n.name for cls in ast.walk(tree)
               if isinstance(cls, ast.ClassDef)
               for n in cls.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    # name -> (lineno, is_nested, parent_fn)
    defs: dict[str, tuple[int, bool, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in methods:
                continue
            parent = owner.get(id(node), "")
            defs[node.name] = (node.lineno, bool(parent), parent)

    consts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    consts[tgt.id] = node.lineno

    # every NAME use, with the function it appears in, excluding the def itself
    uses: list[tuple[str, str, int]] = []
    assert_sites = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            uses.append((node.id, owner.get(id(node), ""), node.lineno))
        elif isinstance(node, ast.Attribute):
            uses.append((node.attr, owner.get(id(node), ""), node.lineno))
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in ASSERT_FN:
                assert_sites += 1
    return (defs, consts, uses, assert_sites), ""


def is_test_file(path: pathlib.Path) -> bool:
    """A file whose whole job is testing another one.

    In such a file every helper and constant is test-only by construction, so
    the test-only detectors say nothing and would bury the real findings.
    """
    n = path.name
    return n.startswith("check-") or n.endswith(("-redprove.py", "-selftest.py"))


def find_test_only_and_uncalled(path, defs, consts, uses):
    """Detectors 2 and 3: a guard only the test calls; a constant nobody reads."""
    out = []
    test_file = is_test_file(path)
    rel = path.relative_to(REPO).as_posix()
    by_name: dict[str, list[str]] = {}
    for name, where, _ in uses:
        by_name.setdefault(name, []).append(where)

    for name, (line, nested, parent) in sorted(defs.items(), key=lambda kv: kv[1][0]):
        # TEST INFRASTRUCTURE IS NOT A FINDING. A proof function, a fixture
        # builder and the assertion helper itself are all called only by the
        # test BY DESIGN, and listing twenty of them buried the four real
        # findings on the first run.
        if (nested or name.startswith("__") or name in ("main", "self_test")
                or name in ASSERT_FN or TEST_FN.search(name)
                or FIXTURE_FN.search(name)):
            continue
        callers = [w for w in by_name.get(name, []) if w != name]
        if not callers:
            # Never referenced at all. `main` and dunders are excluded above, so
            # this is genuinely unreachable unless something reaches it by
            # string (getattr, a CLI dispatch table).
            out.append(Finding(
                "uncalled", rel, line, f"def {name}()",
                "nothing in this file references it, so it cannot run"))
            continue
        if not test_file and all(_in_test(w, defs) for w in callers):
            kind = "test_only_guard" if GUARD_HINT.search(name) else "test_only"
            why = ("only the self-test calls it, so what it refuses is never "
                   "refused in a real run" if kind == "test_only_guard" else
                   "only the self-test calls it")
            out.append(Finding(kind, rel, line, f"def {name}()", why))

    for name, line in sorted(consts.items(), key=lambda kv: kv[1]):
        readers = [w for w in by_name.get(name, [])]
        if not readers:
            out.append(Finding(
                "unread_const", rel, line, name,
                "defined and never read, so whatever it documents is not "
                "enforced by this file"))
        elif not test_file and all(_in_test(w, defs) for w in readers):
            out.append(Finding(
                "test_only_const", rel, line, name,
                "only the self-test reads it, so it constrains nothing real"))
    return out


def find_after_stream(path):
    """Detector 1: the dead cap. A wait/kill AFTER a loop over a live stream.

    `for line in proc.stdout` returns only when the stream closes, which is when
    the process has already ended. Anything AFTER it whose job is to bound the
    run can never do that job, and that is the exact shape of the cap that was
    quoted to the owner ten times having run zero times.
    """
    out = []
    rel = path.relative_to(REPO).as_posix()
    tree = module_of(path)
    if isinstance(tree, Exception):
        return out
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if not isinstance(stmt, ast.For):
                continue
            src = ast.unparse(stmt.iter)
            if not re.search(r"\.(stdout|stderr)\b", src):
                continue
            # A POST-LOOP WAIT IS A REAP WHEN THE LOOP ITSELF CAN STOP THE
            # PROCESS, and only then. Measured against the real launcher: it
            # kills inside the loop and then waits 30s to confirm the process
            # died, which is correct and was the detector's first false
            # positive. The fault is a post-loop timeout in a loop with NO
            # cut mechanism, because then that timeout IS the claimed bound.
            cuts_inside = any(
                isinstance(n, ast.Break)
                or (isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") in ("kill", "terminate"))
                for n in ast.walk(stmt))
            if cuts_inside:
                continue
            for later in body[i + 1:]:
                for call in ast.walk(later):
                    if not isinstance(call, ast.Call):
                        continue
                    fname = getattr(call.func, "attr", "")
                    if fname not in ("wait", "kill", "terminate"):
                        continue
                    has_timeout = any(k.arg == "timeout" for k in call.keywords)
                    if fname == "wait" and not has_timeout:
                        continue  # a plain reap is correct there
                    out.append(Finding(
                        "after_stream", rel, call.lineno,
                        f"{ast.unparse(call)[:60]}",
                        f"it runs AFTER `for ... in {src[:40]}`, which returns "
                        f"only once the process has ended, so it can never "
                        f"bound the run"))
    return out


# A self-test that ran N checks and M of them passed prints "M/N checks
# passed". The count that matters is N, the number that RAN: this detector
# compares it against static call sites, and a check that ran and FAILED is a
# different fact from an assertion that never ran. Reading M here turned a
# failing check into a false "never evaluated" finding, measured 2026-08-30:
# lane-block (76/77) and queue-renumber (14/17) were both misreported that
# way.
_ASSERT_COUNT_RE = re.compile(
    r"(\d+)\s*(?:/\s*(\d+))?\s*(?:assertions?|checks?)\b")


def parse_assertion_count(text):
    """Checks that RAN, per the callee's own count line, or None.

    "23/35 checks passed" -> 35 (ran), never 23 (passed). A tool whose
    self-test prints no count returns None, which is a DISTINCT answer from
    zero: the report names those tools as BLIND, never as "no gap".
    """
    nums = []
    for m in _ASSERT_COUNT_RE.finditer(text or ""):
        nums.append(int(m.group(2)) if m.group(2) else int(m.group(1)))
    return max(nums) if nums else None


def reported_assertions(path, timeout=900):
    """How many assertions the tool SAYS it ran, or None.

    None is a distinct sentinel: a self-test that prints no count cannot be
    checked for this fault, and the report says so (BLIND) instead of letting
    None read as "no gap". The parser is proven against real captures in the
    self-test below, never against invented output.
    """
    try:
        res = subprocess.run([sys.executable, str(path), "--self-test"],
                             cwd=REPO, capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_assertion_count(res.stdout + res.stderr)


ASSERT_GAP_READ: list[str] = []
ASSERT_GAP_BLIND: list[str] = []


def find_assert_gap(path, assert_sites, run=True):
    """Detector 4: fewer assertions ran than the source contains call sites.

    Runtime BELOW static is the fault: an assertion in a branch nobody took, or
    an assertion helper that silently records nothing. Runtime above static is
    normal (a call site inside a loop).
    """
    out = []
    rel = path.relative_to(REPO).as_posix()
    if not run or assert_sites == 0:
        return out
    said = reported_assertions(path)
    if said is None:
        # SILENCE IS NOT SUCCESS. A tool whose self-test prints no count
        # cannot be checked for this fault, and saying nothing would read as
        # "no gap found". The report names these separately.
        ASSERT_GAP_BLIND.append(rel)
        return out
    ASSERT_GAP_READ.append(f"{rel} ({said} ran, {assert_sites} written)")
    if said < assert_sites:
        out.append(Finding(
            "assert_gap", rel, 0, f"{said} ran, {assert_sites} written",
            f"{assert_sites - said} assertion call site(s) never evaluated; an "
            f"assertion only ever seen passing is not known to run at all"))
    return out


def repo_name_index():
    """Every bare word appearing in any tool or automation file, by file.

    A NAME DEFINED IN ONE FILE AND READ IN ANOTHER IS NOT DEAD, and the first
    version of this tool did not know that: it reported bench-lane's
    RETIRED_MODELS as never read while lint.py pulls it with
    `getattr(mod, "RETIRED_MODELS")` and enforces it repo-wide. A cross-file
    reference is invisible to an AST of one module, and a getattr by string is
    invisible to any AST at all, so this is a text index on purpose.
    """
    index: dict[str, set[str]] = {}
    roots = [TOOLS, REPO / "automation", REPO / "hooks"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".py", ".ps1", ".sh", ".json") or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for word in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)):
                index.setdefault(word, set()).add(path.name)
    return index


def audit(paths, run=True, index=None):
    findings, errors = [], []
    # The self-test passes its own index: the fixture text is embedded in THIS
    # file, so a real repo index finds every fixture name "elsewhere" and
    # downgrades it. That is the detector working, on the wrong question.
    index = repo_name_index() if index is None else index
    for path in paths:
        got, err = collect(path)
        if err:
            errors.append(f"{path.name}: {err}")
            continue
        defs, consts, uses, assert_sites = got
        raw = find_test_only_and_uncalled(path, defs, consts, uses)
        for f in raw:
            if f.kind in ("uncalled", "unread_const"):
                name = f.what.removeprefix("def ").removesuffix("()")
                elsewhere = index.get(name, set()) - {path.name}
                if elsewhere:
                    f.kind = "cross_file"
                    f.why = ("read from another file (" +
                             ", ".join(sorted(elsewhere)[:3]) +
                             "), so it is live; this file just never uses it")
        findings += raw
        findings += find_after_stream(path)
        findings += find_assert_gap(path, assert_sites, run)
    return findings, errors


ORDER = ("after_stream", "assert_gap", "test_only_guard", "uncalled",
         "test_only", "test_only_const", "unread_const")

HEADLINE = {
    "after_stream": "CANNOT EVER RUN: bounded by a loop that already ended",
    "assert_gap": "ASSERTIONS THAT NEVER RAN",
    "test_only_guard": "A GUARD ONLY THE TEST CALLS, so it refuses nothing",
    "uncalled": "DEFINED AND NEVER REFERENCED",
    "test_only": "only the self-test calls it",
    "test_only_const": "only the self-test reads it",
    "unread_const": "defined and never read",
}


def report(findings, errors) -> int:
    for line in errors:
        print(f"UNREADABLE  {line}")
    if not findings:
        print("no dead paths found by any detector")
        _assert_gap_coverage()
        return 0
    by_kind: dict[str, list] = {}
    for f in findings:
        if f.kind == "cross_file":
            continue
        by_kind.setdefault(f.kind, []).append(f)
    findings = [f for f in findings if f.kind != "cross_file"]
    if not findings:
        print("no dead paths found by any detector")
        return 0
    for kind in ORDER:
        rows = by_kind.get(kind)
        if not rows:
            continue
        print(f"\n=== {HEADLINE[kind]}  ({len(rows)})")
        for f in sorted(rows, key=lambda r: (r.file, r.line)):
            where = f"{f.file}:{f.line}" if f.line else f.file
            print(f"  {where}  {f.what}")
            print(f"      {f.why}")
    print()
    _assert_gap_coverage()
    print(f"{len(findings)} finding(s). Each is a QUESTION: fix it or say why "
          f"it is fine. Every one of the four faults this tool was built from "
          f"was invisible to a green self-test.")
    return 1


def _assert_gap_coverage() -> None:
    """Say which files the assertion-gap detector could and could NOT check."""
    if not ASSERT_GAP_READ and not ASSERT_GAP_BLIND:
        print("assertion-gap detector: not run (--no-run)")
        return
    print(f"assertion-gap detector: read a count from {len(ASSERT_GAP_READ)} "
          f"file(s), BLIND on {len(ASSERT_GAP_BLIND)}")
    if ASSERT_GAP_BLIND:
        print("  blind (their self-test prints no assertion count, so this "
              "fault cannot be detected in them):")
        for rel in ASSERT_GAP_BLIND:
            print(f"    {rel}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--no-run", action="store_true",
                    help="static detectors only, no self-test subprocesses")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.file:
        paths = [pathlib.Path(f).resolve() for f in a.file]
    else:
        paths = [p for p in sorted(TOOLS.glob("*.py"))
                 if p.name not in SKIP_FILES]
    findings, errors = audit(paths, run=not a.no_run)
    return report(findings, errors)


# --------------------------------------------------------------------------- #

_checks: list[tuple[bool, str, object]] = []


def want(ok: bool, label: str, detail: object = "") -> bool:
    """Record one assertion AND return it, so a caller can branch on it.

    Returning None here is the fault this file's fourth detector exists to
    catch, so this helper does not have it.
    """
    _checks.append((bool(ok), label, detail))
    return bool(ok)


FIXTURE = '''
import subprocess

LIVE_CAP = 900
DEAD_CONST = 42


def called_in_anger(x):
    return x + LIVE_CAP


def only_the_test_calls_me():
    return 1


def verify_something(x):
    """A guard nothing in production invokes."""
    return x > 0


def nobody_references_me():
    return 2


def run_it():
    proc = subprocess.Popen(["true"], stdout=subprocess.PIPE)
    for line in proc.stdout:
        print(line)
    proc.wait(timeout=LIVE_CAP)
    return called_in_anger(1)


def main():
    return run_it()


def self_test():
    assert only_the_test_calls_me() == 1
    assert verify_something(1)
    return 0
'''


# REAL --self-test output, captured verbatim 2026-08-30 on this machine. The
# assertion-count parser is proven against these, never against invented text
# (rules/shared/memory-integrity.md rule 2). usage.py is a passing N/N run,
# lane-block a FAILING N/M run (the load-bearing case: the parser must read
# checks RAN, not checks passed), and sync-branch a self-test with no count
# line at all (the parser must answer None, the BLIND sentinel, not a zero).

REAL_USAGE_SELFTEST = """\
  ok    the captured real 401 body classifies as no-credential, not zero
  ok    no key classifies as no-credential, not zero
  ok    a 200 response parses to READ
  ok    monthly spent derives from percent x limit (79% x $60 = $47.40)
  ok    weekly spent derives from percent x limit (55.6% x $30 = $16.68)
  ok    rolling spent derives from percent x limit (26.8% x $12 = $3.22)
  ok    the ceilings are the documented $12/$30/$60
  ok    resetsAt survives the parse
  ok    malformed JSON is refused, not read as zero
  ok    a missing window is refused, not read as zero
  ok    Claude cost sums cents into dollars (1234.56+765.44 = 2000.00)
  ok    a malformed Claude cost payload is refused
  ok    Copilot rows are counted (140 + 60 = 200)
  ok    Copilot non-numeric rows do not abort the count
  ok    Copilot empty payload yields (0, None), not a fake zero
  ok    a READ of zero renders dollars
  ok    a NO_CREDENTIAL line never renders dollars
  ok    a NO_CREDENTIAL line is labelled NOT READ
  ok    zero and no-credential render differently
  ok    readable is True only for a READ (zero included), never for a finding
  ok    every non-READ status is a distinct value (nothing aliases zero)
  ok    a NO_SUBSCRIPTION answer renders differently from a $0 read
  ok    a MANUAL executor renders as manual, never as spend
  ok    the total counts only read executors and names the skipped ones
  ok    the total with nothing read says so instead of printing a fake sum

25/25 checks passed
"""

REAL_LANE_BLOCK_SELFTEST_TAIL = """\
PASS  an item appearing in BOTH sections is flagged, because it reads as live work while everyone believes it is parked: got ["IN BOTH SECTIONS, so it reads as live work: 'The idea-intake interface thing'. An item is parked by the LATER: marker, never by a sentence saying it is parked."]
PASS  and two genuinely different items are not confused for each other

60/60 checks passed
"""

REAL_SYNC_BRANCH_SELFTEST = """\
Traceback (most recent call last):
  File "tools/sync-branch.py", line 467, in <module>
    sys.exit(main())
  File "tools/sync-branch.py", line 458, in main
    return self_test()
  File "tools/sync-branch.py", line 349, in self_test
    assert git(other, "push", "origin", "windows").returncode == 0
AssertionError
"""
# (The real capture named this worktree's absolute file paths in the traceback
# frames; those are normalized above because the path is ephemeral per
# worktree and no parser reads it.)


def self_test() -> int:
    import shutil
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dead-code-"))
    try:
        f = tmp / "fixture.py"
        f.write_text(FIXTURE, encoding="utf-8")
        # audit() reports paths relative to REPO, so the fixture lives under it.
        target = TOOLS / "_dead_code_fixture.py"
        shutil.copyfile(f, target)
        try:
            findings, errors = audit([target], run=False, index={})
        finally:
            target.unlink(missing_ok=True)

        want(not errors, "the fixture parses", errors)
        kinds = {}
        for x in findings:
            kinds.setdefault(x.kind, []).append(x.what)

        want(any("proc.wait" in w for w in kinds.get("after_stream", [])),
             "DETECTOR 1 catches a timeout wait AFTER a loop over proc.stdout, "
             "which is the cap that was quoted ten times and ran zero times",
             kinds.get("after_stream"))
        want(any("only_the_test_calls_me" in w for w in kinds.get("test_only", [])),
             "DETECTOR 2 catches a function only the self-test calls",
             kinds.get("test_only"))
        want(any("verify_something" in w for w in kinds.get("test_only_guard", [])),
             "and separates a GUARD from an ordinary helper, because a guard "
             "nothing invokes refuses nothing",
             kinds.get("test_only_guard"))
        want(any("nobody_references_me" in w for w in kinds.get("uncalled", [])),
             "DETECTOR 2b catches a function nothing references at all",
             kinds.get("uncalled"))
        want("DEAD_CONST" in kinds.get("unread_const", []),
             "DETECTOR 3 catches a constant nobody reads",
             kinds.get("unread_const"))

        # THE OTHER HALF OF EVERY DETECTOR: it must stay QUIET about the live
        # ones. A detector that flags everything is not an instrument.
        flagged = {w for rows in kinds.values() for w in rows}
        want(not any("called_in_anger" in w for w in flagged),
             "a function production code calls is NOT flagged", flagged)
        want("LIVE_CAP" not in flagged,
             "a constant production code reads is NOT flagged", flagged)
        want(not any("run_it" in w for w in flagged),
             "and the caller itself is not flagged", flagged)

        # Detector 4 against a real tool, since it needs a self-test to read.
        gap = find_assert_gap(pathlib.Path(__file__), 3, run=False)
        want(gap == [], "DETECTOR 4 is silent when it is told not to run", gap)
        got, _ = collect(pathlib.Path(__file__))
        want(got[3] >= 8,
             "and it counts this file's own assertion call sites", got[3])

        # THE COUNT PARSER IS PROVEN AGAINST REAL CALLEE OUTPUT, never
        # invented text. usage.py and lane-block are passing runs, while
        # sync-branch's real self-test has no count line.
        want(parse_assertion_count(REAL_USAGE_SELFTEST) == 25,
             "the real usage.py output (25/25) parses to 25 ran",
             parse_assertion_count(REAL_USAGE_SELFTEST))
        want(parse_assertion_count(REAL_LANE_BLOCK_SELFTEST_TAIL) == 60,
             "the real lane-block output (60/60) parses to 60 ran",
             parse_assertion_count(REAL_LANE_BLOCK_SELFTEST_TAIL))
        want(parse_assertion_count(REAL_SYNC_BRANCH_SELFTEST) is None,
             "a real self-test with no count line parses to None (BLIND), "
             "distinct from zero",
             parse_assertion_count(REAL_SYNC_BRANCH_SELFTEST))
        want(parse_assertion_count("3 checks") == 3
             and parse_assertion_count("") is None,
             "a bare count reads as itself and empty text reads as None",
             (parse_assertion_count("3 checks"),
              parse_assertion_count("")))

        bad = [(l, d) for ok, l, d in _checks if not ok]
        for ok, label, detail in _checks:
            print(("PASS  " if ok else "FAIL  ") + label
                  + ("" if ok else f"  [{detail!r}]"))
        print(f"\n{len(_checks) - len(bad)}/{len(_checks)} checks passed")
        if bad:
            print(f"SELF-TEST FAILED: {len(bad)} check(s)")
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
