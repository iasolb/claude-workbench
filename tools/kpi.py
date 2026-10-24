#!/usr/bin/env python3
"""kpi.py: one command that computes the six health measures.

Golden rule A5 at the top of the framework: a question asked more than once
needs a command that answers it every time. docs/kpis.md defines six measures
and says "a KPI reported without its rows does not count", and until this
file nothing computed any of them.

    python tools/kpi.py             # the six rows, resolved
    python tools/kpi.py --self-test # prove it against temp fixtures

OBSERVER, NEVER A GATE. It prints findings and exits 0; only --self-test may
exit nonzero. Every number is RESOLVED at run time from the system that owns
it: nothing is stored, nothing is cached, so the answer cannot go stale
between runs. A measure that genuinely cannot be computed prints UNMEASURED
with the reason in one line. A zero is never used to stand in for unknown.

The count parser is NOT re-implemented here: tools/dead-code-check.py owns
`parse_assertion_count`, proven against real captured self-test output, and
this file imports it by path. One home per fact (memory-integrity rule 3).
"""

import argparse
import csv
import importlib.util
import json
import os
import machinelib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"

# Declared production paths, the same constants the sibling tools carry
# (land-lane.py, backfill-landings.py): the memory bank main checkout and the
# host-side run ledger. Resolved from here so a worktree lane still reads the
# real framework, never the sparse copy beside it.
PROD_REPO = machinelib.memory_bank_root()
PROD_LANDINGS = machinelib.landings_csv()

MODEL_LEDGER_CANDIDATES = (
    REPO / "automation" / "model-ledger.csv",
    PROD_REPO / "automation" / "model-ledger.csv",
)
LANDINGS_CANDIDATES = (
    REPO / "state" / "landings.csv",
    PROD_LANDINGS,
)

DEFAULT_TOOL_TIMEOUT = 300
DEFAULT_DUP_TIMEOUT = 300

# Named once, here, rather than inlined at the call site: kpi5() runs this
# script and reads the TOON ARTIFACT it writes via --out, never its stdout.
FIND_DUPLICATES_SCRIPT = TOOLS / "find-duplicates.py"

_AT_IMPORT_RE = re.compile(r"^[ \t]*@(\S+)[ \t]*$", re.M)


def _dead_code_check():
    """Import parse_assertion_count from its owner, dead-code-check.py."""
    spec = importlib.util.spec_from_file_location(
        "dead_code_check_mod", TOOLS / "dead-code-check.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def first_existing(candidates):
    for raw in candidates:
        path = Path(raw)
        if path.is_file():
            return path
    return None


def resolve_ledger(candidates):
    path = first_existing(candidates)
    if path is not None:
        return path
    return Path(candidates[0])


# --------------------------------------------------------------------------- #
# KPI 1: behaviours per KB of always-loaded context
# --------------------------------------------------------------------------- #

def run_one_selftest(path, timeout):
    """Run one tool's --self-test. Never raises on a slow or broken tool."""
    try:
        res = subprocess.run(
            [sys.executable, str(path), "--self-test"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
        return {"exit": res.returncode, "timed_out": False,
                "text": (res.stdout or "") + (res.stderr or ""), "error": ""}
    except subprocess.TimeoutExpired:
        return {"exit": None, "timed_out": True, "text": "", "error": ""}
    except OSError as exc:
        return {"exit": None, "timed_out": False, "text": "",
                "error": str(exc)}


def collect_selftests(tool_paths, timeout):
    """name -> run result, for every tools/*.py."""
    out = {}
    for path in sorted(tool_paths):
        out[path.name] = run_one_selftest(path, timeout)
    return out


def kpi1_numerator(tool_paths, timeout):
    """(total, counted, blind, failed, timed_out, refused).

    A behaviour counts only when it is GUARANTEED: a passing self-test
    assertion. A tool whose self-test exited nonzero scores nothing, a tool
    that timed out scores nothing, and a tool whose self-test prints no count
    at all is BLIND: invisible to the numerator, which is itself the finding.

    REFUSED IS NOT FAILED. A tool that declines to run against the shared store
    is working as designed, and reporting it as a failure trains a reader to
    skim the failed list, which is the one list that must stay meaningful.
    """
    dcc = _dead_code_check()
    parse = dcc.parse_assertion_count
    results = collect_selftests(tool_paths, timeout)
    total = 0
    counted, blind, failed, timed_out, refused = [], [], [], [], []
    for name in sorted(results):
        r = results[name]
        if r["timed_out"]:
            timed_out.append(name)
            continue
        if r["error"]:
            failed.append(name + " (could not run: %s)" % r["error"])
            continue
        if "SELF-TEST REFUSED" in r["text"]:
            refused.append(name + " (declines to run against a shared store)")
            continue
        if r["exit"] != 0:
            failed.append(name)
            continue
        count = parse(r["text"])
        if count is None:
            blind.append(name)
            continue
        total += count
        counted.append(name)
    return total, counted, blind, failed, timed_out, refused


def always_loaded_bytes(claude_md, search_dirs):
    """(total_bytes, included_files) or (None, why).

    The byte size of CLAUDE.md plus every file it imports on an `@` line,
    FOLLOWING the import lines rather than any hardcoded file list: adding an
    import changes the number. An import that resolves to nothing is skipped;
    it cannot be measured and a guess would be remembered, not resolved.
    """
    try:
        total = len(claude_md.read_bytes())
    except OSError as exc:
        return None, "CLAUDE.md unreadable at %s: %s" % (claude_md, exc)
    try:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, "CLAUDE.md unreadable at %s: %s" % (claude_md, exc)
    included = [claude_md]
    for rel in _AT_IMPORT_RE.findall(text):
        resolved = None
        for d in search_dirs:
            cand = Path(d) / rel
            if cand.is_file():
                resolved = cand
                break
        if resolved is None or resolved in included:
            continue
        try:
            total += len(resolved.read_bytes())
        except OSError:
            continue
        included.append(resolved)
    return total, included


def kpi1(tool_paths, claude_md, search_dirs, tool_timeout):
    """The KPI 1 row: (value, source, note_lines)."""
    total, counted, blind, failed, timed_out, refused = kpi1_numerator(
        tool_paths, tool_timeout)
    bytes_and_files = always_loaded_bytes(claude_md, search_dirs)
    if bytes_and_files[0] is None:
        value = "UNMEASURED (%s)" % bytes_and_files[1]
        return value, "~/.claude/CLAUDE.md", []
    loaded_bytes, included = bytes_and_files
    ratio = total / (loaded_bytes / 1024.0)
    value = "%.2f behaviours per KB (= %d passing assertions / %.1f KB)" % (
        ratio, total, loaded_bytes / 1024.0)
    notes = []
    notes.append("numerator: %d passing assertion(s) across %d tool(s); "
                 "%d blind, %d failed, %d refused, %d timed out"
                 % (total, len(counted) + len(blind) + len(failed)
                    + len(timed_out) + len(refused), len(blind), len(failed),
                    len(refused), len(timed_out)))
    if refused:
        notes.append("refused by design, which is the tool working and NOT a "
                     "fault (scores nothing): %s" % ", ".join(refused))
    if blind:
        notes.append("blind (self-test prints no assertion count, so the "
                     "numerator cannot see them): %s" % ", ".join(blind))
    if failed:
        notes.append("self-test exited nonzero (scores nothing): %s"
                     % ", ".join(failed))
    if timed_out:
        notes.append("self-test timed out after %ds (scores nothing): %s"
                     % (tool_timeout, ", ".join(timed_out)))
    notes.append("denominator: %d bytes over %d file(s): %s + %d @-imported "
                 "file(s)" % (loaded_bytes, len(included),
                              claude_md.name, len(included) - 1))
    source = "%s + its @ imports" % claude_md
    return value, source, notes


# --------------------------------------------------------------------------- #
# Ledger helpers shared by KPIs 2, 3 and 4
# --------------------------------------------------------------------------- #

def read_ledger(path):
    """(rows, "") or (None, why). A missing or empty ledger is a real answer."""
    if not path.is_file():
        return None, "ledger missing"
    try:
        with path.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        return None, "ledger unreadable: %s" % exc
    return rows, ""


def is_landed_verdict(v):
    v = (v or "").strip().upper()
    return v.startswith("PASSED") or v.startswith("DONE") or v == "LANDED"


# --------------------------------------------------------------------------- #
# KPI 2: input tokens per successful run
# --------------------------------------------------------------------------- #

def kpi2(ledger_path):
    """Mean input_tokens over landed rows of the token ledger.

    Landed means gate=pass, the outcome the ledger's own `record` subcommand
    stamps at landing. A failed run scores nothing. If the ledger cannot
    distinguish landed from failed, or holds no numeric tokens on landed rows,
    the row is UNMEASURED with the reason.
    """
    rows, why = read_ledger(ledger_path)
    if rows is None:
        return "UNMEASURED (%s)" % why, str(ledger_path), []
    if not rows:
        return "UNMEASURED (ledger has no rows)", str(ledger_path), []
    gates = {r.get("gate", "").strip().lower() for r in rows}
    if not any(g in ("pass", "fail") for g in gates):
        return ("UNMEASURED (ledger cannot distinguish landed from failed: "
                "no gate=pass or gate=fail on any row)"), str(ledger_path), []
    landed = [r for r in rows if r.get("gate", "").strip().lower() == "pass"]
    if not landed:
        return ("UNMEASURED (no landed rows, meaning no gate=pass row)"), \
            str(ledger_path), []
    tokens = []
    for r in landed:
        raw = (r.get("input_tokens") or "").strip()
        if raw.isdigit():
            tokens.append(int(raw))
    if not tokens:
        return ("UNMEASURED (no numeric input_tokens on the landed rows)"), \
            str(ledger_path), []
    mean = int(round(sum(tokens) / len(tokens)))
    notes = ["mean over %d landed row(s), gate=pass, input_tokens" %
             len(landed)]
    return str(mean), str(ledger_path), notes


# --------------------------------------------------------------------------- #
# KPI 3: wall time per successful run
# --------------------------------------------------------------------------- #

def kpi3(landings_path):
    """Mean total_s over landed rows of the run ledger.

    Landed means a PASSED/DONE verdict, the word the launcher itself writes.
    total_s is the launcher's own measured wall duration.
    """
    rows, why = read_ledger(landings_path)
    if rows is None:
        return "UNMEASURED (%s)" % why, str(landings_path), []
    if not rows:
        return "UNMEASURED (run ledger has no rows)", str(landings_path), []
    verdicts = {r.get("verdict", "").strip().upper() for r in rows}
    if not any(verdicts):
        return ("UNMEASURED (ledger cannot distinguish landed from failed: "
                "verdict is empty on every row)"), str(landings_path), []
    landed = [r for r in rows if is_landed_verdict(r.get("verdict"))]
    if not landed:
        return ("UNMEASURED (no landed row, meaning no PASSED/DONE verdict)"), \
            str(landings_path), []
    secs = []
    for r in landed:
        raw = (r.get("total_s") or "").strip()
        if re.fullmatch(r"\d+(\.\d+)?", raw):
            secs.append(float(raw))
    if not secs:
        return ("UNMEASURED (no numeric total_s on the landed rows)"), \
            str(landings_path), []
    mean = sum(secs) / len(secs)
    notes = ["mean over %d landed row(s), PASSED/DONE verdict, total_s" %
             len(landed)]
    return "%.1f" % mean, str(landings_path), notes


# --------------------------------------------------------------------------- #
# KPI 4: first-attempt landing rate
# --------------------------------------------------------------------------- #

def kpi4(landings_path):
    """Landed-on-first-launch over launched, from the same run ledger.

    First launch means attempts=1, the value land-lane.py reads from the
    launcher's dated run logs. If the ledger cannot tell a first launch from
    a relaunch, the row is UNMEASURED.
    """
    rows, why = read_ledger(landings_path)
    if rows is None:
        return "UNMEASURED (%s)" % why, str(landings_path), []
    if not rows:
        return "UNMEASURED (run ledger has no rows)", str(landings_path), []
    attempts = [r.get("attempts", "").strip() for r in rows]
    if not any(attempts):
        return ("UNMEASURED (ledger cannot tell a first launch from a "
                "relaunch: attempts is empty on every row)"), \
            str(landings_path), []
    verdicts = {r.get("verdict", "").strip().upper() for r in rows}
    if not any(verdicts):
        return ("UNMEASURED (ledger cannot distinguish landed from failed: "
                "verdict is empty on every row)"), str(landings_path), []
    launched = [r for r in rows if r.get("verdict", "").strip()]
    landed_first = [
        r for r in launched
        if is_landed_verdict(r.get("verdict"))
        and r.get("attempts", "").strip() == "1"]
    pct = 100.0 * len(landed_first) / len(launched)
    notes = ["%d of %d launched run(s) landed on the first launch"
             % (len(landed_first), len(launched))]
    return "%.0f%%" % pct, str(landings_path), notes


# --------------------------------------------------------------------------- #
# KPI 5: duplication count
# --------------------------------------------------------------------------- #

TOTAL_FINDINGS_RE = re.compile(r"^total_findings:\s*(\d+)\s*$", re.M)


def kpi5(script, root, timeout):
    """Run find-duplicates.py, then read its TOON ARTIFACT, never its banner.

    find-duplicates.py already writes a stable, parseable file via its own
    `--out` flag: `total_findings: N` on its own line, ahead of the findings
    table. The FIRST version of this function threw that artifact away
    (`artifact.unlink()` ran before a single byte of it was ever read) and
    instead regexed `find-duplicates: N pair(s)...` out of the tool's PRINTED
    BANNER, meant for a person. A banner wording change would have made that
    match silently stop, and this observer would have reported UNMEASURED
    forever without saying why. Reading the artifact's own declared field is
    asking the tool directly for the number instead of guessing it out of
    prose (`rules/shared/memory-integrity.md` rule 2; `check-tool-contracts.py`).

    On 2026-09-03 the real scan ran over five minutes with no output, which
    is exactly why nobody ran it: the bound turns that hang into a visible
    UNMEASURED (timed out after Ns), distinct from a zero and from a missing
    source. The artifact goes to a temp file so this observer leaves no trace
    in the repo it is measuring, and it is deleted only AFTER being read.
    """
    artifact = Path(tempfile.gettempdir()) / (
        "kpi-dup-" + str(time.time_ns()) + ".toon")
    label = "%s over %s" % (script.name, root)
    try:
        try:
            res = subprocess.run(
                [sys.executable, str(script), "--root", str(root),
                 "--out", str(artifact)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return ("UNMEASURED (timed out after %ds)" % timeout), label, []
        except OSError as exc:
            return ("UNMEASURED (find-duplicates could not run: %s)" % exc), \
                label, []
        if res.returncode != 0:
            return ("UNMEASURED (find-duplicates exited %d)"
                    % res.returncode), label, []
        try:
            text = artifact.read_text(encoding="utf-8")
        except OSError as exc:
            return ("UNMEASURED (artifact unreadable: %s)" % exc), label, []
    finally:
        try:
            artifact.unlink(missing_ok=True)
        except OSError:
            pass
    m = TOTAL_FINDINGS_RE.search(text)
    if not m:
        return ("UNMEASURED (artifact has no total_findings field)"), \
            label, []
    notes = ["pairs at or above the tool's default similarity threshold, "
             "read from the artifact's own total_findings field"]
    return m.group(1), label, notes


# --------------------------------------------------------------------------- #
# KPI 6: public surface completeness
# --------------------------------------------------------------------------- #

def kpi6(owner=None):
    """Public non-fork repos that have a description and topics.

    Set GITHUB_OWNER to the account slug to inspect.


    The definition is public and non-fork, so the fields include isPrivate and
    isFork even though the hand description of the command omitted them: a
    private repo must never count as a complete public surface.

    A LICENCE IS NOT A CRITERION AND NEVER WAS. This function required one
    until 2026-09-17 and failed 8 of 20 repos on it alone, every one of them
    coursework or a profile page. `docs/kpis.md` does not ask for a licence: its
    four countable parts are a description and topics, tests and a visibly
    passing CI run FOR THE REPOS THAT ARE LIBRARIES, no leaked personal data,
    and a README that says what the thing does. The licence check was invented
    here and applied to every repo.

    A review of the 8 failures on 2026-09-17 concluded: "the public repos
    without licenses probs shouldnt have em." The substance is sound too.
    A licence is meaningful on a LIBRARY a stranger installs, which is what the
    three published loader packages are and they have one. Stamping MIT on a
    class project invites reuse of something nobody maintains, so the measure
    was pushing the surface in the wrong direction, not merely counting wrong.

    That makes this the third KPI instrument fault found in one evening, beside
    a passing self-test reported as failed and a duplication count that times
    out: `store:facts/framework/kpi_reports_a_passing_self_test_as_failed`.
    """
    owner = owner or os.environ.get("GITHUB_OWNER", "").strip()
    if not owner:
        return "UNMEASURED (set GITHUB_OWNER to the account slug)", "GITHUB_OWNER", []
    cmd = ["gh", "repo", "list", owner, "--json",
           "name,description,repositoryTopics,isFork,isPrivate"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return ("UNMEASURED (gh repo list could not run: %s)" % exc), \
            "gh repo list %s" % owner, []
    if res.returncode != 0:
        tail = ((res.stderr or "") + (res.stdout or "")).strip()[-160:]
        return ("UNMEASURED (gh repo list exited %d: %s)"
                % (res.returncode, tail or "no output")), \
            "gh repo list %s" % owner, []
    try:
        repos = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return "UNMEASURED (gh repo list returned non-JSON)", \
            "gh repo list %s" % owner, []
    public = [r for r in repos
              if not r.get("isPrivate") and not r.get("isFork")]
    if not public:
        return "UNMEASURED (no public non-fork repos under %s)" % owner, \
            "gh repo list %s" % owner, []
    complete, failing = [], []
    for r in public:
        desc = (r.get("description") or "").strip()
        topics = r.get("repositoryTopics") or []
        topics = [t.get("name") if isinstance(t, dict) else str(t)
                  for t in topics]
        if desc and topics:
            complete.append(r.get("name"))
        else:
            missing = []
            if not desc:
                missing.append("no description")
            if not topics:
                missing.append("no topics")
            failing.append("%s (%s)" % (r.get("name"), ", ".join(missing)))
    notes = ["failing: %s" % "; ".join(failing)] if failing else []
    return ("%d of %d public non-fork repos complete"
            % (len(complete), len(public))), "gh repo list %s" % owner, notes


# --------------------------------------------------------------------------- #
# Assembly, rendering, entry point
# --------------------------------------------------------------------------- #

def compute_all(args):
    """The six rows, each (number, name, value, source, note_lines)."""
    tool_paths = [p for p in sorted(TOOLS.glob("*.py"))]
    home = Path.home() / ".claude" / "CLAUDE.md"
    search_dirs = [home.parent, Path.home() / ".claude", PROD_REPO, REPO]
    return [
        (1, "behaviours per KB of always-loaded context",
         kpi1(tool_paths, home, search_dirs, args.tool_timeout)),
        (2, "input tokens per successful run",
         kpi2(resolve_ledger(MODEL_LEDGER_CANDIDATES))),
        (3, "wall seconds per successful run",
         kpi3(resolve_ledger(LANDINGS_CANDIDATES))),
        (4, "first-attempt landing rate",
         kpi4(resolve_ledger(LANDINGS_CANDIDATES))),
        (5, "duplication count",
         kpi5(FIND_DUPLICATES_SCRIPT, PROD_REPO, args.dup_timeout)),
        (6, "public surface completeness", kpi6()),
    ]


def emit(rows, out=sys.stdout):
    """Print one row per measure and return the observer exit code, 0."""
    for number, name, (value, source, notes) in rows:
        out.write("KPI %d  %s: %s\n" % (number, name, value))
        out.write("  source: %s\n" % source)
        for note in notes:
            out.write("  %s\n" % note)
    resolved = sum(1 for _n, _nm, (value, _s, _nt) in rows
                   if not value.startswith("UNMEASURED"))
    out.write("\nsummary: %d of %d measures resolved; observer exit 0\n"
              % (resolved, len(rows)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Compute the six health measures of docs/kpis.md, "
                    "resolved from the system that owns each number.")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the measures against temp fixtures and exit")
    ap.add_argument("--tool-timeout", type=int, default=DEFAULT_TOOL_TIMEOUT,
                    help="seconds per tools/*.py --self-test run "
                         "(default %d)" % DEFAULT_TOOL_TIMEOUT)
    ap.add_argument("--dup-timeout", type=int, default=DEFAULT_DUP_TIMEOUT,
                    help="seconds to bound the find-duplicates scan "
                         "(default %d)" % DEFAULT_DUP_TIMEOUT)
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    return emit(compute_all(args))


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

SELFTEST_COUNTS = (
    'import sys\n'
    'def main():\n'
    '    if "--self-test" in sys.argv:\n'
    '        print("self-test: all 7 checks passed")\n'
    '        return 0\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    sys.exit(main())\n'
)

SELFTEST_FRACTION = (
    'import sys\n'
    'def main():\n'
    '    if "--self-test" in sys.argv:\n'
    '        print("25/25 checks passed")\n'
    '        return 0\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    sys.exit(main())\n'
)

SELFTEST_NOCOUNT = (
    'import sys\n'
    'def main():\n'
    '    if "--self-test" in sys.argv:\n'
    '        print("self-test: looks fine")\n'
    '        return 0\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    sys.exit(main())\n'
)

SELFTEST_FAILS = (
    'import sys\n'
    'def main():\n'
    '    if "--self-test" in sys.argv:\n'
    '        print("self-test: 3/4 checks passed")\n'
    '        return 1\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    sys.exit(main())\n'
)

SELFTEST_HANGS = (
    'import sys\n'
    'import time\n'
    'def main():\n'
    '    time.sleep(60)\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    sys.exit(main())\n'
)

# A REAL script, run as a real subprocess, shaped like find-duplicates.py:
# it writes an artifact via --out AND prints a human banner. The banner
# deliberately carries the WRONG count (99) while the artifact carries the
# real one (3), so the self-test can tell whether kpi5 is reading the
# artifact's own total_findings field or still scraping the banner.
SELFTEST_DUPFAKE = (
    'import sys\n'
    'def main():\n'
    '    args = sys.argv[1:]\n'
    '    out = None\n'
    '    for i, a in enumerate(args):\n'
    '        if a == "--out":\n'
    '            out = args[i + 1]\n'
    '    if out:\n'
    '        with open(out, "w", encoding="utf-8") as f:\n'
    '            f.write("produced_at: \\"2026-09-12T00:00:00Z\\"\\n")\n'
    '            f.write("total_findings: 3\\n")\n'
    '    print("find-duplicates: 99 pair(s) >= 0.8 over rules, docs")\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    sys.exit(main())\n'
)


SELFTEST_REFUSES = (
    'import sys\n'
    'def main():\n'
    '    if "--self-test" in sys.argv:\n'
    '        print("fixture: SELF-TEST REFUSED: shared store")\n'
    '        return 2\n'
    '    return 0\n'
    'if __name__ == "__main__":\n'
    '    raise SystemExit(main())\n'
)


def self_test():
    import io
    import shutil

    checks = []
    fails = []

    def want(cond, label):
        checks.append(cond)
        if not cond:
            fails.append(label)

    tmp = Path(tempfile.mkdtemp(prefix="kpi-selftest-"))
    try:
        tools = tmp / "tools"
        tools.mkdir()
        names = {
            "counts.py": SELFTEST_COUNTS,
            "fraction.py": SELFTEST_FRACTION,
            "nocount.py": SELFTEST_NOCOUNT,
            "fails.py": SELFTEST_FAILS,
            "hangs.py": SELFTEST_HANGS,
            "refuses.py": SELFTEST_REFUSES,
        }
        for name, text in names.items():
            (tools / name).write_text(text, encoding="utf-8")
        paths = sorted(tools.glob("*.py"))

        # ---- 1. a tool whose self-test prints no count is BLIND, not zero --
        total, counted, blind, failed, timed_out, refused = kpi1_numerator(paths, 2)
        want(total == 32,
             "numerator is %d, expected 32 from the two passing counted "
             "tools (7 + 25)" % total)
        want(sorted(blind) == ["nocount.py"],
             "the no-count tool is listed as blind, not counted as zero: %s"
             % blind)
        want("fails.py" in failed,
             "a self-test that exits nonzero scores nothing and is listed: %s"
             % failed)
        refused_row = [f for f in refused if f.startswith("refuses.py")]
        want(len(refused_row) == 1,
             "a refusing tool is reported in its OWN bucket, never dropped: %s"
             % refused)
        want(not any(f.startswith("refuses.py") for f in failed),
             "a refusal is NOT in the failed bucket; refused is not failed: %s"
             % failed)
        want(len(failed) == 1,
             "exactly one genuine failure in the fixture (fails.py), so the "
             "failed count is not inflated by the refusal: %s" % failed)
        kpi1_md = tmp / "KPI1_FIXTURE.md"
        kpi1_md.write_text("fixture\n", encoding="utf-8")
        _v, _s, kpi1_notes = kpi1(paths, kpi1_md, [tmp], 2)
        want(any("refused by design" in n for n in kpi1_notes),
             "the KPI 1 row carries a refused-by-design note line: %s"
             % kpi1_notes)
        want(any("1 failed, 1 refused" in n for n in kpi1_notes),
             "the headline counts refusals separately from failures: %s"
             % kpi1_notes)

        # ---- 2. a hanging source is cut off and reported as timed out ------
        hang = tools / "hangs.py"
        value, source, notes = kpi5(hang, tmp, 2)
        want(value.startswith("UNMEASURED (timed out after 2s)"),
             "a hanging source reports UNMEASURED (timed out after Ns), got "
             "%r" % value)
        want("timed out" in value and "0 pair" not in value,
             "timed out is distinct from a zero count: %r" % value)
        want("UNMEASURED" in kpi5(hang, tmp, 2)[0],
             "the timeout row still carries the UNMEASURED word")

        # ---- 2b. the count comes from the ARTIFACT, never the banner ------
        # A real subprocess run: the fake tool's printed banner says 99 pair(s)
        # and its artifact's total_findings field says 3. If kpi5 ever regexes
        # the banner again this assertion catches it immediately.
        dupfake = tools / "dupfake.py"
        dupfake.write_text(SELFTEST_DUPFAKE, encoding="utf-8")
        dvalue, dsource, dnotes = kpi5(dupfake, tmp, 5)
        want(dvalue == "3",
             "kpi5 reads total_findings from the artifact (3), not the "
             "banner's 99: got %r" % dvalue)
        want("99" not in dvalue,
             "the banner's wrong count never leaks into the result: %r"
             % dvalue)

        # ---- 3. a missing source prints UNMEASURED and never a 0 ----------
        missing = tmp / "missing-ledger.csv"
        value, _source, _notes = kpi2(missing)
        row = "KPI 2  input tokens per successful run: %s" % value
        want("UNMEASURED" in value, "a missing ledger prints UNMEASURED, got "
             "%r" % value)
        want("0" not in row,
             "the string 0 appears nowhere on the missing-source row: %r"
             % row)
        # A ledger that cannot distinguish landed from failed is UNMEASURED
        # with the reason, never a zero either.
        bad = tmp / "no-gate.csv"
        bad.write_text("session_id,input_tokens\nses_a,100\n", encoding="utf-8")
        v2, _s2, _n2 = kpi2(bad)
        want(v2.startswith("UNMEASURED") and "0" not in v2,
             "an undifferentiable ledger is UNMEASURED, never zero: %r" % v2)

        # ---- 4. the denominator follows the @ import lines -----------------
        base = tmp / "memory"
        base.mkdir()
        (base / "rules").mkdir()
        (base / "rules" / "one.md").write_text("0123456789", encoding="utf-8")
        (base / "rules" / "two.md").write_text("0123456789", encoding="utf-8")
        (base / "rules" / "three.md").write_text("0123456789", encoding="utf-8")
        claude = base / "CLAUDE.md"
        claude.write_text("line one\n@rules/one.md\n@rules/two.md\n",
                          encoding="utf-8")
        bytes_a, files_a = always_loaded_bytes(claude, [base])
        want(bytes_a == len(claude.read_bytes()) + 20,
             "denominator sums CLAUDE.md plus its @ imports, got %d"
             % bytes_a)
        want(len(files_a) == 3, "both imports resolved into the file list")
        claude_before = len(claude.read_bytes())
        claude.write_text(
            "line one\n@rules/one.md\n@rules/two.md\n@rules/three.md\n",
            encoding="utf-8")
        bytes_b, _files_b = always_loaded_bytes(claude, [base])
        want(bytes_b == bytes_a + (len(claude.read_bytes()) - claude_before)
             + 10,
             "adding one @ import changes the number: %d -> %d"
             % (bytes_a, bytes_b))
        # an unresolvable import is skipped, never guessed
        claude.write_text("x\n@rules/none.md\n", encoding="utf-8")
        b3, files3 = always_loaded_bytes(claude, [base])
        want(b3 == len(claude.read_bytes()) and len(files3) == 1,
             "an unresolvable @ import is skipped, never guessed")

        # ---- 5. the summary exits 0 even when measures are UNMEASURED ------
        buf = io.StringIO()
        rc = emit([
            (2, "input tokens per successful run",
             ("UNMEASURED (ledger missing)", "fixture", [])),
            (5, "duplication count",
             ("UNMEASURED (timed out after 2s)", "fixture", [])),
        ], out=buf)
        want(rc == 0, "the observer summary exits 0 with UNMEASURED rows")
        want("UNMEASURED" in buf.getvalue() and "0 of 2" in buf.getvalue(),
             "the summary counts resolved measures, never printing a fake "
             "zero row")

        # ---- KPI 3 and 4 against a fixture ledger with a landed + failed row
        landing = tmp / "landings.csv"
        landing.write_text(
            "verdict,total_s,attempts\n"
            "PASSED,120,1\n"
            "FAILED,80,2\n"
            "PASSED,40,1\n", encoding="utf-8")
        v3, _s3, _n3 = kpi3(landing)
        want(v3 == "80.0",
             "KPI 3 is the mean total_s over landed rows only (120+40)/2, got "
             "%s" % v3)
        v4, _s4, _n4 = kpi4(landing)
        want(v4 == "67%",
             "KPI 4 is landed-on-first-launch over launched (2 of 3), got %s"
             % v4)
        noatt = tmp / "no-attempts.csv"
        noatt.write_text("verdict,total_s,attempts\nPASSED,120,\n",
                         encoding="utf-8")
        w4, _s5, _n5 = kpi4(noatt)
        want(w4.startswith("UNMEASURED"),
             "a ledger that cannot tell a first launch from a relaunch is "
             "UNMEASURED: %r" % w4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # THE FLOOR IS THE GATE, same pattern as tools/lint.py. A pass/fail
    # self-test cannot notice assertions an order asked for and never got:
    # it reads the same green before and after. Making the COUNT go red is
    # the only thing that catches a dropped assertion.
    #
    # RAISED to 24 on 2026-09-19 as step one of the order that teaches this
    # tool to tell a by-design REFUSAL apart from a FAILURE. It is red at 22
    # on purpose until that order lands. Raise it whenever assertions are
    # added; it counts itself.
    #
    # IT SITS ABOVE THE REPORTING LOOP DELIBERATELY. Placed below it, the
    # floor still failed the run and its LABEL was never printed, so the gate
    # went red saying nothing about why. Caught the same turn it was written.
    want(len(checks) + 1 >= 24,
         "the self-test runs at least 24 checks, so assertions an order asked "
         "for cannot be dropped without this going red (currently %d)"
         % (len(checks) + 1))
    for label in fails:
        print("FAIL: %s" % label, file=sys.stderr)
    passed = sum(1 for c in checks if c)
    print("self-test: %d/%d checks passed" % (passed, len(checks)))
    if fails:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
