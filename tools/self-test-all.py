#!/usr/bin/env python3
"""self-test-all.py: run every tool's self-test and report which ones fail.

    self-test-all.py                 run them all, print a table, exit nonzero
                                     if any failed
    self-test-all.py --only lane     only tools whose name contains "lane"
    self-test-all.py --timeout 30    per-tool timeout in seconds (default 60)
    self-test-all.py --quiet         one line per FAILING tool only
    self-test-all.py --self-test

WHY THIS EXISTS (2026-09-07)
----------------------------
83 tools in `tools/` carry a `--self-test`, and NOTHING RAN THEM TOGETHER. The
repo's own rule is that machinery ships with its test
(`rules/shared/memory-integrity.md` rule 6), and the tests were duly written,
so the gap was not discipline: there was no second occurrence of "run them all"
to trigger golden rule A2, because running them all was never once done.

That is the worst shape a test suite can have. Every individual test passes on
the day it is written, and thereafter nothing notices when one goes red. A
suite nobody runs is indistinguishable from a suite that does not exist, which
is the silence-and-success rule pointed at our own tooling.

WHAT A FAILURE HERE MEANS, and the distinction matters:
    FAIL      the self-test ran and reported failure. A real regression.
    TIMEOUT   it hung. Also a real problem: a self-test must not need a network
              or a database, so a hang means it reaches for something it should
              have stubbed.
    ERROR     it crashed before reporting, usually an import error.
    NO-TEST   the tool has no --self-test at all. Reported, never counted as a
              pass, because "nothing to run" and "everything passed" must never
              print the same way.
    REFUSED   the self-test declined to run because the environment would make
              it write production state (tools/jobsync.py against a shared
              MEMDB_URL). That is the tool WORKING, not failing, so it does not
              make the run red. It is still not a pass and is counted apart.
              Added 2026-09-16: counting it as FAIL made this suite PERMANENTLY
              red, and a suite that is always red is a suite whose colour means
              nothing, so a genuine regression beside it would go unseen. That
              is the silence-and-success rule turned inside out.

Exit codes:
    0  every self-test that exists passed
    1  at least one failed, timed out, or errored
    2  bad arguments
"""

from __future__ import annotations

import argparse
import concurrent.futures
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
INTERPRETER = sys.executable

# Tools that must NOT be run here, each with the reason. An enumerated list,
# never a runtime guess (docs/scoped-by-declaration.md).
#   self-test-all.py  would recurse into itself.
SKIP = {
    "self-test-all.py": "would run itself",
}


def has_self_test(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "--self-test" in text or "self_test" in text


def candidates(only: str | None) -> list[Path]:
    found = []
    for path in sorted(TOOLS.glob("*.py")):
        if path.name in SKIP:
            continue
        if only and only.lower() not in path.name.lower():
            continue
        found.append(path)
    return found


def run_one(path: Path, timeout: int) -> tuple[str, str, float, str]:
    """Returns (name, status, seconds, detail)."""
    if not has_self_test(path):
        return (path.name, "NO-TEST", 0.0, "no --self-test in the source")
    started = time.time()
    try:
        # DECODE EXPLICITLY, AND NEVER ON THE LOCALE (fixed 2026-09-07).
        # `text=True` alone decodes with the locale codec, cp1252 on this
        # machine, so a child printing any byte outside that set raised
        # UnicodeDecodeError inside subprocess's reader THREAD. The thread died,
        # the traceback went to stderr, and the run CARRIED ON reporting a
        # status: measured this session, the suite printed a traceback and
        # `PASS=1` in the same breath. So a tool's own output could be lost
        # while the instrument still answered, which is the exact shape of
        # `rules/shared/memory-integrity.md` rule 2 in the tool whose whole job
        # is telling us whether the tools work.
        proc = subprocess.run(
            [INTERPRETER, str(path), "--self-test"],
            capture_output=True, timeout=timeout, cwd=str(REPO),
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return (path.name, "TIMEOUT", float(timeout),
                f"still running after {timeout}s: a self-test must not need "
                f"a network, a database or a lock")
    except OSError as exc:
        return (path.name, "ERROR", time.time() - started, str(exc))
    elapsed = time.time() - started
    if proc.returncode == 0:
        return (path.name, "PASS", elapsed, "")
    tail = (proc.stderr.strip() or proc.stdout.strip() or "").splitlines()
    detail = tail[-1][:160] if tail else f"exit {proc.returncode}"
    combined = (proc.stdout or "") + (proc.stderr or "")
    # A DELIBERATE REFUSAL IS NOT A REGRESSION, and conflating them made this
    # suite permanently red, which is worse than no suite at all: a run that is
    # always red carries no signal, so a real failure appearing beside the
    # standing one is invisible. tools/jobsync.py refuses to self-test when
    # MEMDB_URL points at the shared store, on purpose (order 82: a self-test
    # that can write production state is not a test). That refusal is the tool
    # WORKING. It is still not a pass, so it gets its own word and is counted
    # separately, exactly as NO-TEST is.
    if "SELF-TEST REFUSED" in combined:
        return (path.name, "REFUSED", elapsed, detail)
    # An argparse rejection is not a failing assertion: it means the tool
    # mentions self_test but does not accept the flag. Say which it is.
    status = "ERROR" if proc.returncode == 2 and "usage:" in (
        proc.stderr or "") else "FAIL"
    return (path.name, status, elapsed, detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="substring of the tool filename")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--quiet", action="store_true",
                        help="print only the tools that are not passing")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    paths = candidates(args.only)
    if not paths:
        print(f"no tools matched {args.only!r} in {TOOLS}", file=sys.stderr)
        return 2

    # TOOLS THAT SHARE ONE SINGLETON RUN ALONE, and the list is DECLARED here
    # rather than sniffed at runtime (`docs/scoped-by-declaration.md`).
    #
    # `rules/shared/style.md` states the parallel-safety condition this breaks:
    # "SHARED SINGLETONS COUNT AS WHAT THE OTHER WRITES: the opencode server, a
    # bound port, the dev database." These self-tests write to the one real
    # Postgres store and then assert which tables were left untouched, so two
    # of them at once make each other fail on a true statement about the
    # database and a false one about the tool.
    #
    # MEASURED 2026-09-07: health.py passes 63/63 run alone and FAILED inside
    # the pool with "nothing was written to queries", because jobsync.py was
    # writing to the same database in another thread. An intermittent red that
    # depends on thread scheduling is worse than a steady one: it gets
    # dismissed as flakiness, which is how a real failure hides.
    EXCLUSIVE = ("health.py", "jobsync.py", "memdb.py")

    shared = [p for p in paths if p.name in EXCLUSIVE]
    parallel = [p for p in paths if p.name not in EXCLUSIVE]

    results: list[tuple[str, str, float, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, p, args.timeout): p for p in parallel}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    # One at a time, after the pool has drained, so nothing else is touching the
    # store while these run.
    for p in shared:
        results.append(run_one(p, args.timeout))
    results.sort(key=lambda r: (r[1] != "PASS", r[0]))

    counts: dict[str, int] = {}
    for _, status, _, _ in results:
        counts[status] = counts.get(status, 0) + 1

    for name, status, elapsed, detail in results:
        if args.quiet and status == "PASS":
            continue
        line = f"{status:8} {name:32} {elapsed:5.1f}s"
        print(f"{line}  {detail}" if detail else line)

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    broken = sum(counts.get(k, 0) for k in ("FAIL", "TIMEOUT", "ERROR"))
    if broken:
        print(f"{broken} tool(s) are not green.")
        return 1
    # NEITHER NUMBER IS EVER HIDDEN. A run that exits 0 while some tools have
    # no test, or refused to run theirs, is not the same as an all-green run,
    # and the closing line has to say which it is or the exit code becomes the
    # only thing anyone reads.
    missing = counts.get("NO-TEST", 0)
    refused = counts.get("REFUSED", 0)
    notes = []
    if missing:
        notes.append(f"{missing} tool(s) have none")
    if refused:
        notes.append(f"{refused} refused to run against a shared store, "
                     "which is the refusal working")
    print("every self-test that ran passed"
          + (" (" + "; ".join(notes) + ")" if notes else ""))
    return 0


def _self_test() -> int:
    import tempfile
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(f"{'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    global TOOLS
    saved = TOOLS
    root = Path(tempfile.mkdtemp(prefix="sta-selftest-"))
    try:
        TOOLS = root
        (root / "good.py").write_text(
            "import sys\n"
            "if '--self-test' in sys.argv:\n"
            "    print('ok'); sys.exit(0)\n", encoding="utf-8")
        (root / "bad.py").write_text(
            "import sys\n"
            "if '--self-test' in sys.argv:\n"
            "    print('assertion x failed'); sys.exit(1)\n", encoding="utf-8")
        (root / "untested.py").write_text("print('nothing here')\n",
                                          encoding="utf-8")
        (root / "hangs.py").write_text(
            "import sys, time\n"
            "if '--self-test' in sys.argv:\n"
            "    time.sleep(30)\n", encoding="utf-8")

        name, status, _, _ = run_one(root / "good.py", 20)
        check(status == "PASS", "a passing self-test reports PASS")

        name, status, _, detail = run_one(root / "bad.py", 20)
        check(status == "FAIL", "a failing self-test reports FAIL")
        check("assertion x failed" in detail,
              "the failure carries the tool's own last line, not just an exit code")

        # A CHILD WHOSE OUTPUT IS NOT LOCALE-DECODABLE IS STILL READ. Anchored
        # on the real byte that broke it: 0x90 is undefined in cp1252, and this
        # machine's locale IS cp1252, so `text=True` alone killed the reader
        # thread and lost the child's output while still reporting a status.
        (root / "highbyte.py").write_text(
            "import sys\n"
            "if '--self-test' in sys.argv:\n"
            "    sys.stdout.buffer.write(b'assertion \\x90 failed\\n')\n"
            "    sys.exit(1)\n", encoding="utf-8")
        name, status, _, detail = run_one(root / "highbyte.py", 20)
        check(status == "FAIL",
              "a child emitting a byte the locale cannot decode still reports FAIL")
        check("failed" in detail,
              "and its output survives decoding instead of being lost with the "
              "reader thread")

        name, status, _, _ = run_one(root / "untested.py", 20)
        check(status == "NO-TEST",
              "a tool with no self-test is NO-TEST, never PASS")

        name, status, _, _ = run_one(root / "hangs.py", 2)
        check(status == "TIMEOUT",
              "a hanging self-test is TIMEOUT, distinct from FAIL")

        # A DELIBERATE REFUSAL IS NOT A REGRESSION. The fixture uses the real
        # sentinel and a real nonzero exit, because that is exactly what
        # tools/jobsync.py does when MEMDB_URL points at the shared store.
        (root / "refuses.py").write_text(
            "import sys\n"
            "if '--self-test' in sys.argv:\n"
            "    print('refuses: SELF-TEST REFUSED: MEMDB_URL points to "
            "production/shared store.')\n"
            "    sys.exit(1)\n", encoding="utf-8")
        name, status, _, detail = run_one(root / "refuses.py", 20)
        check(status == "REFUSED",
              "a self-test that refuses a shared store is REFUSED, not FAIL")
        check("SELF-TEST REFUSED" in detail,
              "and it carries its own reason, so the refusal is readable")
        rc = main(["--only", "refuses", "--timeout", "20", "--quiet"])
        check(rc == 0,
              "a refusal does not make the run red, or the suite is red forever "
              "and its colour stops meaning anything")

        # BUT IT IS STILL NOT A PASS. Red-proven by mapping REFUSED onto PASS:
        # the closing line would then claim everything passed while a tool had
        # run no assertions at all.
        check(status != "PASS", "a refusal is never counted as a pass")

        # The whole point: a run containing only NO-TEST must not exit 0 while
        # claiming everything passed, and a run with a FAIL must exit nonzero.
        rc = main(["--only", "bad", "--timeout", "20", "--quiet"])
        check(rc == 1, "a failing tool makes the whole run exit 1")
        rc = main(["--only", "good", "--timeout", "20", "--quiet"])
        check(rc == 0, "an all-green run exits 0")
        rc = main(["--only", "nosuchtool"])
        check(rc == 2, "a filter matching nothing exits 2, never a false green")
    finally:
        TOOLS = saved
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
