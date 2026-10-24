#!/usr/bin/env python3
"""health.py: score the framework's health daily, in the database.

    python tools/health.py                # measure, grade, write rows, print the card
    python tools/health.py --url <store>  # same, against an explicit store (throwaway proof)
    python tools/health.py --self-test    # prove it against fixtures and a throwaway database

WHY THIS EXISTS (order 53). The framework graded itself on 2026-09-03 and grew
the always-loaded surface 8.4% the next day without noticing. A session that
hand-fixes a tool's output instead of the tool leaves the system looking
healthy, and golden rule A3 depends on the session choosing to confess; a rule
that depends on an agent remembering is a wish. This tool makes it a
measurement: one row per metric per day in the `health` table, graded against a
stated target, so a regression is a fact in the database rather than a
recollection.

THE TWO GROUPS. Group 1 is the framework's shape: always-loaded bytes, passing
self-test assertions, assertions per KB (docs/kpis.md's headline, uncomputed
until now), markdown versus Python files, superseded blocks, tools that run a
subprocess and regex its output, and tools that redefine the repo root instead
of importing the shared resolver. Group 2 is the slack the owner cannot see:
permission prompts he answered (the one-off allow-list length), manual
interventions (commits outside order deliverables), driver failures by cause,
and orders re-cut per landed capability.

MEASURES AND SCORES, NEVER FIXES. It does not fix anything it measures, does
not cut orders and does not write to the queue. The owner reads the card and decides.

MEASUREMENT ROOT. The tool measures the MAIN checkout, never a lane's sparse
worktree, so a lane run still grades the real framework: the same pattern as
tools/kpi.py. Files a sparse checkout does not materialize are read from HEAD
when they are not on disk.

UNMEASURED IS NEVER ZERO. A metric whose source is missing records itself as
UNMEASURED with a NULL value. Zero and unknown never share a value here: this
is an instrument (silence-and-success rule).

THE STORE. Reached exactly the way tools/memdb.py reaches it: MEMDB_URL, or
--local for the local container, never guessed. --url is the loud, explicit
opt-in for pointing at a throwaway store while proving the write path; the
daily routine never needs it.

THE WRITE IS UPSERT. One row per (measured_on, metric); running twice on one
day updates rather than duplicates (db/005_health.sql). The tool writes nothing
outside the health table.

Exit codes: 0 measured and written, 1 measured but the store refused the
write, 3 the store could not be reached (the card still prints), 2 usage.
"""

from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import machinelib
import memdb

PROD_REPO = machinelib.memory_bank_root()
REPO = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
# Override SETTINGS_LOCAL to inspect a settings file outside the default
# ~/.claude/settings.local.json location.
SETTINGS_LOCAL = (Path(os.environ["SETTINGS_LOCAL"])
                  if os.environ.get("SETTINGS_LOCAL")
                  else Path.home() / ".claude" / "settings.local.json")
MACHINE_BRANCHES = ("windows", "mac")

DEFAULT_TOOL_TIMEOUT = 300
FIRST_RUN_WINDOW_DAYS = 1
SELFTEST_PORT = 54325

DELIVERABLE_RE = re.compile(r"^order \d+: driver deliverable$", re.I)
STAMP_RE = re.compile(r"^Stamp the order landed\b", re.I)
# A HOOK'S COMMIT IS NOT A MANUAL INTERVENTION. IT IS THE OPPOSITE OF ONE.
# Added 2026-09-14, the owner in a form. The metric counted every commit that was not
# an order deliverable or a landing stamp, which swept up the session-start
# hook's own `state: <machine> session start (auto, env-report)` rows: roughly a
# quarter of recent history, written by machinery with no person involved. A4 is
# about a HAND-FIX INSIDE A ROUTINE, so counting the routine itself inverts the
# rule it measures, and a row that reads F for the wrong reason gets ignored
# along with the real signal in it (golden rule 2, a logger can never lie).
# Matched on the explicit `(auto` marker rather than the `state:` prefix, so a
# human commit that happens to start with that word is still counted.
AUTOMATED_RE = re.compile(r"^state: .*\(auto[,)]", re.I)

_SUBPROCESS_RE = re.compile(r"subprocess\.(run|Popen|check_output)|os\.popen")
_STDOUT_RE = re.compile(r"\.stdout")
_REGEX_RE = re.compile(r"\bre\.(search|match|findall|finditer)\(")
_ROOT_LITERAL_RE = re.compile(r"Documents[\\/]ai[\\/]memory-bank")
_ROOT_DERIVE_RE = re.compile(
    r"parent\.parent(?!\.parent)|parents\[1\]|"
    r"dirname\(os\.path\.dirname\(os\.path\.abspath\(__file__\)\)")
_ROOT_CONST_NAME_RE = re.compile(
    r"^(REPO|ROOT|PROD_REPO|DEFAULT_REPO|MEMORY_BANK|LANE_ROOT|PRIVATE_REPO|"
    r"BANK_LOCAL|BANK_REMOTE)$")
_IMPORT_RESOLVER_RE = re.compile(
    r"^\s*(?:from\s+(?:machinelib|workbench)\s+import|"
    r"import\s+(?:machinelib|workbench))\b", re.M)
_SUPERSEDED_LINE_RE = re.compile(r"\b(Superseded|kept for provenance)\b")

GRADE_BANDS = (("A", 0.05), ("A-", 0.15), ("B", 0.35), ("C", 0.60))

GROUP1_IDS = ("always_loaded_bytes", "passing_assertions", "assertions_per_kb",
              "markdown_files", "superseded_blocks", "scraping_tools",
              "repo_root_redefined")
GROUP2_IDS = ("permission_prompts", "manual_interventions", "driver_failures",
              "re_cut_orders")

# Each metric carries a DIRECTION, a TARGET and a stated reason so the grade is
# inspectable rather than a feeling. target None means the first successful
# measurement becomes the floor: the metric cannot regress without being
# flagged, and that is the whole bar for a count with no external standard.
METRICS = [
    dict(id="always_loaded_bytes", name="always-loaded bytes",
         direction="lower", target=70000, fmt="int",
         why="order 54's stated target: under 70,000 bytes of always-loaded context"),
    dict(id="passing_assertions", name="passing self-test assertions",
         direction="higher", target=None, fmt="int",
         why="first measurement becomes the floor; blind tools are never counted as zero"),
    dict(id="assertions_per_kb", name="assertions per KB of always-loaded context",
         direction="higher", target=None, fmt="float2",
         why="docs/kpis.md headline; first measurement is the floor"),
    dict(id="markdown_files", name="markdown files (against Python files)",
         direction="lower", target=692, fmt="int",
         why="first measurement, 692, is the stability bar; growth is bloat"),
    dict(id="superseded_blocks", name="superseded blocks",
         direction="lower", target=0, fmt="int",
         why="order 54 moves every superseded block to provenance; none should remain"),
    dict(id="scraping_tools", name="tools that run a subprocess and regex its output",
         direction="lower", target=22, fmt="int",
         why="pre-consolidation level measured 2026-09-04; each scrape is a known fault"),
    dict(id="repo_root_redefined", name="tools that redefine the repo root instead of importing it",
         direction="lower", target=26, fmt="int",
         why="the 2026-09-04 count; machinelib is the one home"),
    dict(id="permission_prompts", name="permission prompts the owner answered (allow-list length)",
         direction="lower", target=120, fmt="int",
         why="~99 healthy on record; 313 measured 2026-09-04 is bloat"),
    dict(id="manual_interventions", name="manual interventions (commits outside order deliverables)",
         direction="lower", target=0, fmt="int",
         why="any hand-fix is the flag the framework must surface"),
    dict(id="driver_failures", name="driver failures by cause (unclassified rate, percent)",
         direction="lower", target=10, fmt="pct1",
         why="a rising unclassified rate means the diagnostic is losing track"),
    dict(id="re_cut_orders", name="orders spent per landed capability (re-cut write targets)",
         direction="lower", target=0, fmt="int",
         why="re-cut work is the expensive failure; one order per capability is the bar"),
]
SPEC_BY_ID = {m["id"]: m for m in METRICS}


def _load_kpi():
    """Import tools/kpi.py by path: always_loaded_bytes and kpi1_numerator are
    its facts, never re-derived here (memory-integrity rule 3)."""
    spec = importlib.util.spec_from_file_location(
        "health_kpi", TOOLS / "kpi.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def _tracked(root: Path) -> list[str]:
    """Tracked files as relative POSIX paths, git ls-files with a walk fallback."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, timeout=30)
        if out.returncode == 0:
            return [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    except Exception:
        pass
    names = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", ".venv", "__pycache__")]
        for fn in filenames:
            full = Path(dirpath) / fn
            names.append(full.resolve().relative_to(Path(root).resolve()).as_posix())
    return sorted(names)


def measure_always_loaded(kpi_mod, claude_md: Path, search_dirs) -> tuple:
    """(bytes, notes) or (None, why). CLAUDE.md plus every @ import it names."""
    try:
        total, included = kpi_mod.always_loaded_bytes(claude_md, search_dirs)
    except Exception as exc:
        return None, "always_loaded_bytes raised: %s" % exc
    if total is None:
        return None, "CLAUDE.md unreadable at %s" % claude_md
    return total, "%d bytes over %d files" % (total, len(included))


def measure_passing_assertions(kpi_mod, tool_paths, timeout) -> tuple:
    """(total_passing_assertions, notes) or (None, why). The numerator is
    kpi1's: a blind or failed or timed-out self-test scores nothing."""
    if not tool_paths:
        return None, "no tools to measure"
    total, _counted, blind, failed, timed_out, _refused = kpi_mod.kpi1_numerator(
        tool_paths, timeout)
    notes = "%d counted; %d blind; %d failed; %d timed out" % (
        total, len(blind), len(failed), len(timed_out))
    return total, notes


def measure_assertions_per_kb(assertions, loaded_bytes) -> tuple:
    """(ratio, notes) or (None, why). Computed from the real total, never a
    guess: no total means UNMEASURED, not zero."""
    if assertions is None or loaded_bytes is None:
        return None, "depends on passing assertions and always-loaded bytes"
    kb = loaded_bytes / 1024.0
    return round(assertions / kb, 2), "%d assertions / %.1f KB" % (assertions, kb)


def _count_files(root: Path) -> tuple:
    md, py = [], []
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, timeout=30)
        if out.returncode == 0:
            for n in out.stdout.decode("utf-8", "replace").split("\0"):
                low = n.lower()
                if low.endswith(".md"):
                    md.append(n)
                elif low.endswith(".py"):
                    py.append(n)
            return md, py
    except Exception:
        pass
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("node_modules", ".venv", "__pycache__")]
        for fn in filenames:
            if fn.lower().endswith(".md"):
                md.append(fn)
            elif fn.lower().endswith(".py"):
                py.append(fn)
    return md, py


def measure_markdown_files(root: Path) -> tuple:
    md, py = _count_files(root)
    return len(md), "%d markdown vs %d python" % (len(md), len(py))


def measure_superseded(root: Path) -> tuple:
    """Lines carrying a superseded/provenance marker in tracked markdown files.
    Prose blocks, not the status strings in Python enums: order 31 measured the
    marker the same way, grepping the repo for 'Superseded'/'kept for
    provenance'."""
    count = 0
    for rel in _tracked(root):
        if not rel.endswith(".md"):
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count += sum(1 for line in text.splitlines()
                     if _SUPERSEDED_LINE_RE.search(line))
    return count, "%d superseded/provenance marker line(s) in markdown" % count


def _scrapes_stdout(text: str) -> bool:
    """A file that runs a subprocess AND reads its output AND regexes it."""
    return bool(_SUBPROCESS_RE.search(text)
                and _STDOUT_RE.search(text)
                and _REGEX_RE.search(text))


def measure_scraping(tools_dir: Path) -> tuple:
    count = 0
    for path in sorted(tools_dir.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _scrapes_stdout(text):
            count += 1
    return count, "%d tool(s) run a subprocess and regex its output" % count


def _redefines_root(text: str) -> bool:
    """A module-level root constant whose value derives or hardcodes the repo
    root. A tool that imports the shared resolver (machinelib or workbench) is
    NOT redefining it, and the resolver files and the instrument itself are
    never counted."""
    if _IMPORT_RESOLVER_RE.search(text):
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        src = ast.get_source_segment(text, node) or ""
        if not (_ROOT_LITERAL_RE.search(src) or _ROOT_DERIVE_RE.search(src)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _ROOT_CONST_NAME_RE.match(target.id):
                return True
    return False


def measure_repo_root(tools_dir: Path) -> tuple:
    count = 0
    for path in sorted(tools_dir.glob("*.py")):
        if path.name in ("workbench.py", "machinelib.py", "health.py"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _redefines_root(text):
            count += 1
    return count, "%d tool(s) redefine the repo root instead of importing it" % count


def measure_permission_prompts(path: Path, previous) -> tuple:
    """The one-off allow-list length, never its contents. previous is
    (value, date) of the last sample, for the daily delta."""
    if not path.is_file():
        return None, "settings.local.json missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None, "settings.local.json unreadable"
    perms = data.get("permissions") if isinstance(data, dict) else None
    allow = perms.get("allow") if isinstance(perms, dict) else None
    if not isinstance(allow, list):
        return None, "no permissions.allow list"
    n = len(allow)
    if previous is not None:
        pv, pdate = previous
        return n, "delta %+d since %s" % (n - pv, pdate)
    return n, "first sample; no prior delta"


def measure_manual_interventions(root: Path, branches, since) -> tuple:
    """Commits on the machine branches whose subject is neither an order
    deliverable nor a landing stamp. Only branches that EXIST on this machine
    are logged; a missing branch (e.g. 'mac' after a merge) is skipped, never a
    failure. git log with all branches dedupes shared history, so a commit is
    counted once."""
    existing = []
    for branch in branches:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet",
             "refs/heads/%s" % branch],
            capture_output=True, text=True, timeout=15)
        if probe.returncode == 0:
            existing.append(branch)
    if not existing:
        return None, "no machine branch (%s) exists in %s" % (
            ", ".join(branches), root)
    cmd = ["git", "-C", str(root), "log", "--format=%s"] + existing
    if since:
        cmd += ["--since=%sT00:00:00" % since]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
    except Exception as exc:
        return None, "git log failed: %s" % exc
    if out.returncode != 0:
        return None, "git log failed: %s" % (out.stderr or "").strip()[:160]
    count = 0
    hits = []
    for line in out.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if DELIVERABLE_RE.match(s) or STAMP_RE.match(s) or AUTOMATED_RE.match(s):
            continue
        count += 1
        hits.append(s)
    return count, "%d commit(s): %s" % (count, "; ".join(hits[:8]))


def measure_driver_failures(csv_path: Path, since) -> tuple:
    """The unclassified rate, percent, over rows since `since`. The notes carry
    the per-cause breakdown so a rising unclassified rate stays visible."""
    if not csv_path.is_file():
        return None, "automation/driver-errors.csv missing"
    try:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        return None, "driver-errors.csv unreadable: %s" % exc
    if since:
        rows = [r for r in rows if (r.get("ts") or "")[:10] >= since]
    total = len(rows)
    perm = sum(1 for r in rows
               if (r.get("kind") or "").strip().lower() == "permission_denied")
    guard = sum(1 for r in rows
                if (r.get("kind") or "").strip().lower() == "guard_block")
    unclass = total - perm - guard
    rate = round(100.0 * unclass / total, 1) if total else 0.0
    notes = "%d rows: %d permission_denied, %d guard_block, %d unclassified" % (
        total, perm, guard, unclass)
    return rate, notes


_WRITES_RE = re.compile(r"^\s*writes:\s*(.*)$")


def _writes_from_frontmatter(text: str) -> list:
    for line in text.splitlines():
        m = _WRITES_RE.match(line)
        if m:
            raw = m.group(1).strip().strip("'\"")
            if not raw or raw.lower() in ("none", "n/a"):
                return []
            return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def measure_re_cut_orders(root: Path) -> tuple:
    """Write targets claimed by two or more orders: the measurable shape of
    work being re-cut rather than fixed.

    THE STORE IS THE RECORD for the real repo since archiving deletes the file
    (2026-10-15): every order, live or finished, is a `jobs` row with its
    write set. A fixture root, or a store that cannot be asked, reads files."""
    claims = defaultdict(list)
    if root.resolve() == REPO.resolve():
        try:
            for job_id, writes in memdb._query("SELECT id, writes FROM jobs"):
                for target in (writes or []):
                    claims[str(target)].append(str(job_id))
        except Exception:                                   # noqa: BLE001
            claims.clear()
        if claims:
            recut = {t: ids for t, ids in claims.items() if len(ids) > 1}
            notes = "%d of %d targets claimed by 2+ orders (store)" % (len(recut), len(claims))
            if recut:
                notes += ": " + "; ".join(sorted(recut))
            return len(recut), notes
    for d in (root / "queue" / "orders", root / "queue" / "orders" / "done"):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for target in _writes_from_frontmatter(text):
                claims[target].append(f.name)
    recut = {t: files for t, files in claims.items() if len(files) > 1}
    notes = "%d of %d targets claimed by 2+ orders" % (len(recut), len(claims))
    if recut:
        notes += ": " + "; ".join(sorted(recut))
    return len(recut), notes


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade(direction: str, target, value):
    """The letter grade: a stated function of distance from target. Better
    than target is never penalised; UNMEASURED is never a letter. A zero
    target is absolute: any value away from it is F, never A."""
    if value is None:
        return "UNMEASURED"
    if target is None:
        return "A"
    if target == 0:
        distance = 0.0 if value == 0 else float("inf")
    elif direction == "higher":
        distance = max(0.0, (target - value) / target)
    else:
        distance = max(0.0, (value - target) / target)
    for letter, bound in GRADE_BANDS:
        if distance <= bound:
            return letter
    return "F"


def fmt_value(value, spec):
    if value is None:
        return "UNMEASURED"
    if spec["fmt"] == "float2":
        return "%.2f" % value
    if spec["fmt"] == "pct1":
        return "%.1f%%" % value
    return format(int(round(value)), ",")


def _delta(spec, value, prev):
    """Magnitude of the move in the bad direction, for the regression text."""
    if value is None or prev is None or prev[0] is None:
        return 0
    pv = prev[0]
    if spec["direction"] == "higher":
        return max(0, pv - value)
    return max(0, value - pv)


def regression_text(spec, value, prev):
    """'REGRESSION +N vs <date>' when the metric got worse, whatever its grade."""
    if value is None or prev is None or prev[0] is None:
        return ""
    pv, pdate = prev
    if spec["direction"] == "higher":
        worse = value < pv
    else:
        worse = value > pv
    if not worse:
        return ""
    d = _delta(spec, value, prev)
    if spec["fmt"] == "float2":
        dtext = "%.2f" % d
    elif spec["fmt"] == "pct1":
        dtext = "%.1f" % d
    else:
        dtext = str(int(round(d)))
    return "REGRESSION +%s vs %s" % (dtext, pdate)


def action_for(spec, r, reg, prev):
    """The specific next action for a metric below A- or regressed. the owner decides."""
    v = r["value"]
    if spec["id"] == "always_loaded_bytes":
        return ("order 54 is the fix: move the rule evidence out of the "
                "always-loaded files to get under 70,000 bytes.")
    if spec["id"] == "passing_assertions":
        return "run tools/kpi.py: the passing-assertion numerator stopped or shrank."
    if spec["id"] == "assertions_per_kb":
        return ("the headline KPI (docs/kpis.md) fell: cut always-loaded "
                "context or add guaranteed behaviour.")
    if spec["id"] == "markdown_files":
        if reg:
            return ("markdown grew by %d file(s); fold the newest into "
                    "existing homes." % _delta(spec, v, prev))
        return "markdown files are at %d; a consolidation order lowers the surface." % int(round(v))
    if spec["id"] == "superseded_blocks":
        return "order 54 moves every superseded block to docs/rule-evidence.md; none should remain."
    if spec["id"] == "scraping_tools":
        return "run tools/check-tool-contracts.py --list and consolidate the NEW scrape sites."
    if spec["id"] == "repo_root_redefined":
        return ("cut an order to make the %d tool(s) import machinelib instead "
                "of deriving the root." % int(round(v)))
    if spec["id"] == "permission_prompts":
        if prev:
            return ("the allow list grew by %d entry(-ies) since %s; one "
                    "missing line (Bash(ssh aibench:*)) caused about fifty "
                    "prompts on 2026-09-04." % (_delta(spec, v, prev), prev[1]))
        return "the allow list has %d entries; cut the bloat." % int(round(v))
    if spec["id"] == "manual_interventions":
        return ("cut an order that absorbs the %d hand-fix(es); the subjects "
                "are in the notes." % int(round(v)))
    if spec["id"] == "driver_failures":
        return "unclassified rate is %.1f%%: run tools/classify-refusals.py to sort the cause." % (v or 0)
    if spec["id"] == "re_cut_orders":
        return ("cut ONE order per capability; %d write target(s) were claimed "
                "by 2+ orders." % int(round(v)))
    return "grade is %s." % r["grade"]


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def render_card(measured_on, results, previous, root) -> str:
    by_id = {r["id"]: r for r in results}
    lines = ["HEALTH GRADE CARD  %s  (measured root: %s)" % (measured_on, root), ""]
    for group, ids in (("Group 1, the framework's shape", GROUP1_IDS),
                       ("Group 2, the slack the owner cannot see", GROUP2_IDS)):
        lines.append("== %s ==" % group)
        for sid in ids:
            spec = SPEC_BY_ID[sid]
            r = by_id.get(sid)
            if r is None:
                continue
            reg = regression_text(spec, r["value"], previous.get(sid))
            lines.append("  %-52s %12s  %-10s %s" % (
                spec["name"], fmt_value(r["value"], spec), r["grade"], reg))
            if r["notes"]:
                lines.append("        %s" % r["notes"])
        lines.append("")
    actions = []
    for spec in METRICS:
        r = by_id.get(spec["id"])
        if r is None:
            continue
        reg = regression_text(spec, r["value"], previous.get(spec["id"]))
        if r["grade"] in ("B", "C", "F") or reg:
            actions.append("  - %s" % action_for(
                spec, r, reg, previous.get(spec["id"])))
    if actions:
        lines.append("NEXT ACTIONS (below A- or regressed; the owner decides):")
        lines.extend(actions)
    else:
        lines.append("Every metric is A or A-. Nothing below the bar.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _fetch_previous(measured_on):
    """dict metric -> (value, measured_on) for each metric's latest row before
    today, or None when the store cannot answer."""
    if memdb.pg8000 is None:
        return None
    sql = ("SELECT DISTINCT ON (metric) metric, value, measured_on "
           "FROM health WHERE measured_on < %s "
           "ORDER BY metric, measured_on DESC")
    try:
        conn = memdb._connect()
    except (memdb.InterfaceError, OSError, memdb.CouldNotAsk):
        return None
    try:
        cur = conn.cursor()
        cur.execute(sql, (measured_on,))
        rows = cur.fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    out = {}
    for metric, value, day in rows:
        # health.value is NUMERIC, so pg8000 hands back decimal.Decimal, which
        # will not subtract from the float every measurement produces. Coerce
        # at the boundary: nothing downstream should know the store's types.
        out[metric] = (None if value is None else float(value), str(day))
    return out


def _write_rows(measured_on, results):
    """Upsert one row per metric into health. Writes nothing anywhere else.
    Returns (rc, message): 0 wrote, 1 the store refused, 3 unreachable."""
    if memdb.pg8000 is None:
        return memdb.UNREACHABLE, memdb._driver_missing_message()
    try:
        conn = memdb._connect()
    except (memdb.InterfaceError, OSError) as exc:
        return memdb.UNREACHABLE, "%s: %s" % (type(exc).__name__, exc)
    except memdb.CouldNotAsk as exc:
        return memdb.UNREACHABLE, "%s: %s" % (type(exc).__name__, exc)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        sql = ("INSERT INTO health (measured_on, metric, value, grade, notes) "
               "VALUES (%s, %s, %s, %s, %s) "
               "ON CONFLICT (measured_on, metric) DO UPDATE SET "
               "value = EXCLUDED.value, grade = EXCLUDED.grade, "
               "notes = EXCLUDED.notes, updated_at = now()")
        for m in results:
            cur.execute(sql, (measured_on, m["id"], m["value"],
                              m["grade"], m["notes"]))
    except (memdb.InterfaceError, OSError) as exc:
        return memdb.UNREACHABLE, "%s: %s" % (type(exc).__name__, exc)
    except memdb.DatabaseError as exc:
        return 1, "%s: %s" % (type(exc).__name__, exc)
    finally:
        conn.close()
    return 0, "wrote %d rows to %s" % (len(results), memdb._source_label())


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _since_window(previous, measured_on):
    """The row window for windowed metrics: the last sample's date when one
    exists, else a fixed first-run window so a first card is not drowned."""
    if previous is not None:
        return previous[1]
    return (date.fromisoformat(measured_on) - timedelta(days=FIRST_RUN_WINDOW_DAYS)).isoformat()


def measure_all(root: Path, previous, tool_timeout, measured_on,
                settings_local: Path = SETTINGS_LOCAL):
    kpi_mod = _load_kpi()
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    search_dirs = [claude_md.parent, Path.home() / ".claude", PROD_REPO, REPO]
    loaded = measure_always_loaded(kpi_mod, claude_md, search_dirs)
    assertions = measure_passing_assertions(
        kpi_mod, sorted((PROD_REPO / "tools").glob("*.py")), tool_timeout)
    per_kb = measure_assertions_per_kb(assertions[0], loaded[0])
    values = {
        "always_loaded_bytes": loaded,
        "passing_assertions": assertions,
        "assertions_per_kb": per_kb,
        "markdown_files": measure_markdown_files(root),
        "superseded_blocks": measure_superseded(root),
        "scraping_tools": measure_scraping(root / "tools"),
        "repo_root_redefined": measure_repo_root(root / "tools"),
        "permission_prompts": measure_permission_prompts(
            settings_local, previous.get("permission_prompts")),
        "manual_interventions": measure_manual_interventions(
            root, MACHINE_BRANCHES,
            _since_window(previous.get("manual_interventions"), measured_on)),
        "driver_failures": measure_driver_failures(
            root / "automation" / "driver-errors.csv",
            _since_window(previous.get("driver_failures"), measured_on)),
        "re_cut_orders": measure_re_cut_orders(root),
    }
    results = []
    for spec in METRICS:
        value, notes = values[spec["id"]]
        results.append(dict(id=spec["id"], name=spec["name"], value=value,
                            notes=notes, grade=grade(spec["direction"],
                                                     spec["target"], value)))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Score the framework's health daily and write the rows.")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=str(PROD_REPO),
                    help="repo to measure (default: the main checkout)")
    ap.add_argument("--url", help="explicit store URL, loudly (throwaway proof only)")
    ap.add_argument("--local", action="store_true",
                    help="use the LOCAL ai-memory-db container, loudly")
    ap.add_argument("--settings-local", default=str(SETTINGS_LOCAL),
                    help="settings.local.json to measure (default: Mac driver)")
    ap.add_argument("--tool-timeout", type=int, default=DEFAULT_TOOL_TIMEOUT)
    ap.add_argument("--measured-on", help="YYYY-MM-DD (default: today)")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    if a.url:
        os.environ["MEMDB_URL"] = a.url
    memdb._LOCAL = a.local
    guard = memdb._guard_source()

    root = Path(a.root)
    measured_on = a.measured_on or date.today().isoformat()
    settings_local = Path(a.settings_local)

    previous = _fetch_previous(measured_on)
    if previous is None:
        previous = {}
        print("health: could not read previous rows from the store; the card "
              "shows no deltas.", file=sys.stderr)

    results = measure_all(root, previous, a.tool_timeout, measured_on,
                          settings_local)
    print(render_card(measured_on, results, previous, root))

    if guard is not None:
        print("health: no store configured; nothing was written.", file=sys.stderr)
        return guard
    rc, msg = _write_rows(measured_on, results)
    if rc == 0:
        print("health: %s" % msg)
    else:
        print("health: COULD NOT WRITE. %s" % msg, file=sys.stderr)
    return rc


# ---------------------------------------------------------------------------
# Self-test. Fixtures prove the measures; a throwaway postgres container proves
# the writes, over the REAL pg8000 driver and the REAL migration files, exactly
# the pattern driverstate.py uses.
# ---------------------------------------------------------------------------

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


def _git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=30)


def _read_migration(name: str) -> str:
    """The migration text from disk, or from HEAD in a sparse checkout."""
    path = Path(__file__).resolve().parent.parent / "db" / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    p = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent),
                        "show", "HEAD:db/%s" % name],
                       capture_output=True, text=True)
    if p.returncode == 0 and p.stdout:
        return p.stdout
    raise RuntimeError("cannot read db/%s: not on disk and not in HEAD" % name)


def _provision_container():
    name = "health-selftest"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    p = subprocess.run(
        ["docker", "run", "-d", "--name", name,
         "-p", "%d:5432" % SELFTEST_PORT,
         "-e", "POSTGRES_USER=aimem", "-e", "POSTGRES_PASSWORD=localdev",
         "-e", "POSTGRES_DB=aimem", "postgres:16"],
        capture_output=True, text=True)
    if p.returncode != 0:
        print("self-test: docker run failed: %s" % p.stderr.strip(), file=sys.stderr)
        return None
    for _ in range(60):
        r = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-U", "aimem", "-d", "aimem"],
            capture_output=True, text=True)
        if r.returncode == 0:
            return name
        time.sleep(1)
    print("self-test: throwaway postgres container never became ready",
          file=sys.stderr)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    return None


def _self_test() -> int:
    fails = []
    count = 0

    def want(ok, label, detail=""):
        nonlocal count
        count += 1
        print("  %s  %s%s" % ("ok  " if ok else "FAIL", label,
                              "" if ok else "  [%s]" % (detail,)))
        if not ok:
            fails.append(label)

    real_environ = dict(os.environ)
    real_local = memdb._LOCAL
    real_out, real_err = sys.stdout, sys.stderr
    tmp = Path(tempfile.mkdtemp(prefix="health-selftest-"))
    name = None
    try:
        # ---- grades are a stated function of distance from target ---------
        want(grade("lower", 100, 100) == "A", "at target is A")
        want(grade("lower", 100, 105) == "A", "5% over target is still A",
             grade("lower", 100, 105))
        want(grade("lower", 100, 105.01) == "A-", "just past 5% drops to A-",
             grade("lower", 100, 105.01))
        want(grade("lower", 100, 115) == "A-", "15% over is the A- boundary",
             grade("lower", 100, 115))
        want(grade("lower", 100, 115.01) == "B", "just past 15% drops to B",
             grade("lower", 100, 115.01))
        want(grade("lower", 100, 135) == "B", "35% over is the B boundary",
             grade("lower", 100, 135))
        want(grade("lower", 100, 135.01) == "C", "just past 35% drops to C",
             grade("lower", 100, 135.01))
        want(grade("lower", 100, 160) == "C", "60% over is the C boundary",
             grade("lower", 100, 160))
        want(grade("lower", 100, 160.01) == "F", "just past 60% is F",
             grade("lower", 100, 160.01))
        want(grade("higher", 100, 95) == "A", "higher-is-better: 5% under is A",
             grade("higher", 100, 95))
        want(grade("higher", 100, 94.99) == "A-", "just past 5% under drops to A-",
             grade("higher", 100, 94.99))
        want(grade("higher", 100, 84.99) == "B", "15.01% under is B",
             grade("higher", 100, 84.99))
        want(grade("higher", 100, 0) == "F", "a zero when the target is 100 is F")
        want(grade("lower", 100, 60) == "A", "better than target is never penalised",
             grade("lower", 100, 60))
        want(grade("higher", 100, 120) == "A", "better than target is never penalised",
             grade("higher", 100, 120))
        want(grade("lower", 100, None) == "UNMEASURED",
             "unmeasurable grades UNMEASURED, never a letter")
        want(grade("higher", None, 5) == "A",
             "no target means the first measurement is the floor")
        want(grade("lower", 0, 0) == "A", "a zero target met by a zero value is A")
        want(grade("lower", 0, 1) == "F",
             "any value over a zero target is F, never A",
             grade("lower", 0, 1))

        # ---- fixture tree ------------------------------------------------
        bank = tmp / "bank"
        (bank / "rules").mkdir(parents=True)
        (bank / "tools").mkdir()
        (bank / "automation").mkdir()
        (bank / "queue" / "orders" / "done").mkdir(parents=True)
        (bank / "docs").mkdir()
        (bank / "reports").mkdir()

        (bank / "rules" / "one.md").write_text("0123456789", encoding="utf-8")
        (bank / "rules" / "two.md").write_text("0123456789", encoding="utf-8")
        claude = bank / "CLAUDE.md"
        claude.write_text("line one\n@rules/one.md\n@rules/two.md\n", encoding="utf-8")
        kpi_mod = _load_kpi()
        loaded = measure_always_loaded(kpi_mod, claude, [bank])
        want(loaded[0] == len(claude.read_bytes()) + 20,
             "always-loaded sums CLAUDE.md plus its @ imports", loaded)

        fixture_tools = {
            "counts.py": SELFTEST_COUNTS,
            "nocount.py": SELFTEST_NOCOUNT,
            "fails.py": SELFTEST_FAILS,
            "hangs.py": SELFTEST_HANGS,
            "scraper.py": ('import subprocess, re\n'
                           'r = subprocess.run(["git", "x"], capture_output=True)\n'
                           're.search(r"x", r.stdout)\n'),
            "runner.py": ('import subprocess\n'
                          'subprocess.run(["git", "x"])\n'),
            "badroot.py": ('from pathlib import Path\n'
                           'ROOT = Path(__file__).resolve().parent.parent\n'),
            "goodroot.py": ('import machinelib\n'
                            'ROOT = machinelib.memory_bank_root()\n'),
        }
        for fname, text in fixture_tools.items():
            (bank / "tools" / fname).write_text(text, encoding="utf-8")
        tool_paths = sorted((bank / "tools").glob("*.py"))

        total, counted, blind, failed, timed_out, _refused = kpi_mod.kpi1_numerator(
            tool_paths, 2)
        want(total == 7,
             "passing-assertion numerator counts only the passing tool",
             (total, counted))
        want("nocount.py" in blind,
             "a self-test that prints no count is BLIND, never counted as zero",
             blind)
        want("fails.py" in failed,
             "a self-test that exits nonzero scores nothing", failed)
        want("hangs.py" in timed_out,
             "a hanging self-test is timed out, distinct from blind", timed_out)

        per_kb = measure_assertions_per_kb(7, 2048)
        want(per_kb[0] == 3.5, "assertions per KB is a real division", per_kb)
        want(measure_assertions_per_kb(None, 2048)[0] is None,
             "no total means UNMEASURED, never 0")

        mdtree = tmp / "mdtree"
        mdtree.mkdir()
        (mdtree / "a.md").write_text("a", encoding="utf-8")
        (mdtree / "b.md").write_text("b", encoding="utf-8")
        (mdtree / "c.py").write_text("print(1)", encoding="utf-8")
        mdpy = measure_markdown_files(mdtree)
        want(mdpy[0] == 2, "markdown count is real", mdpy)
        want("2 markdown vs 1 python" in mdpy[1], "the python count is in the notes", mdpy)

        (bank / "docs" / "s.md").write_text(
            "This was Superseded on 2026-08-30.\n", encoding="utf-8")
        (bank / "docs" / "keep.md").write_text(
            "Still current, no marker.\n", encoding="utf-8")
        sup = measure_superseded(bank)
        want(sup[0] == 1, "superseded blocks count marker lines", sup)

        sc = measure_scraping(bank / "tools")
        want(sc[0] == 1,
             "only a tool that runs a subprocess AND regexes its stdout counts", sc)

        rr = measure_repo_root(bank / "tools")
        want(rr[0] == 1,
             "a tool that derives the root and does not import the resolver is "
             "redefined", rr)

        settings = tmp / "settings.local.json"
        settings.write_text(
            '{"permissions": {"allow": ["a", "b", "c"], "deny": []}}',
            encoding="utf-8")
        prompts = measure_permission_prompts(settings, None)
        want(prompts[0] == 3, "the allow-list length is the count", prompts)
        want(isinstance(prompts[0], int) and prompts[0] > 0,
             "a present settings file reports a number, never an implied zero",
             prompts)
        prompts2 = measure_permission_prompts(settings, (5, "2026-09-03"))
        want(prompts2[0] == 3 and "delta -2" in prompts2[1],
             "with a previous sample the delta is in the notes", prompts2)
        missing_prompts = measure_permission_prompts(tmp / "missing.json", None)
        want(missing_prompts[0] is None and "missing" in missing_prompts[1],
             "a missing settings file is UNMEASURED, never 0", missing_prompts)

        gitrepo = tmp / "gitrepo"
        gitrepo.mkdir()
        _git(gitrepo, "init", "-b", "windows")
        _git(gitrepo, "config", "user.email", "self-test")
        _git(gitrepo, "config", "user.name", "selftest")
        _git(gitrepo, "config", "commit.gpgsign", "false")
        (gitrepo / "f.txt").write_text("1", encoding="utf-8")
        _git(gitrepo, "add", ".")
        _git(gitrepo, "commit", "-m", "order 42: driver deliverable")
        (gitrepo / "f.txt").write_text("2", encoding="utf-8")
        _git(gitrepo, "add", ".")
        _git(gitrepo, "commit", "-m", "Stamp the order landed: 42-x")
        (gitrepo / "f.txt").write_text("3", encoding="utf-8")
        _git(gitrepo, "add", ".")
        _git(gitrepo, "commit", "-m", "fix a typo by hand")
        (gitrepo / "f.txt").write_text("3a", encoding="utf-8")
        _git(gitrepo, "add", ".")
        _git(gitrepo, "commit", "-m", "state: windows session start (auto, env-report)")
        _git(gitrepo, "checkout", "-b", "mac")
        (gitrepo / "f.txt").write_text("4", encoding="utf-8")
        _git(gitrepo, "add", ".")
        _git(gitrepo, "commit", "-m", "merge mac into windows")
        manual = measure_manual_interventions(gitrepo, ["windows", "mac"], None)
        want(manual[0] == 2,
             "deliverable and stamp commits are excluded, everything else "
             "counted once", manual)
        want("session start (auto" not in manual[1],
             "a HOOK'S OWN COMMIT IS NOT A MANUAL INTERVENTION: counting the "
             "routine inverts the rule the metric measures", manual)
        want("fix a typo by hand" in manual[1] and "merge mac into windows" in manual[1],
             "the counted subjects are named", manual)

        csvp = bank / "automation" / "driver-errors.csv"
        csvp.write_text(
            "ts,lane,order,model,kind,command,detail\n"
            "2026-09-04T10:00:00Z,l,x,m,permission_denied,ls,x\n"
            "2026-09-04T11:00:00Z,l,x,m,permission_denied,ls,x\n"
            "2026-09-04T12:00:00Z,l,x,m,guard_block,ls,x\n"
            "2026-09-04T13:00:00Z,l,x,m,other,ls,x\n", encoding="utf-8")
        fail = measure_driver_failures(csvp, "2026-09-04")
        want(fail[0] == 25.0, "unclassified rate is 1 of 4 = 25.0%", fail)
        want("2 permission_denied, 1 guard_block, 1 unclassified" in fail[1],
             "the per-cause breakdown is in the notes", fail)
        want(measure_driver_failures(bank / "automation" / "nope.csv", None)[0] is None,
             "a missing csv is UNMEASURED, never 0")

        (bank / "queue" / "orders" / "1-a.md").write_text(
            "---\nwrites: tools/x.py\n---\n", encoding="utf-8")
        (bank / "queue" / "orders" / "2-b.md").write_text(
            "---\nwrites: tools/x.py,db/1.sql\n---\n", encoding="utf-8")
        (bank / "queue" / "orders" / "done" / "3-c.md").write_text(
            "---\nwrites: tools/y.py\n---\n", encoding="utf-8")
        recut = measure_re_cut_orders(bank)
        want(recut[0] == 1,
             "a write target claimed by 2+ orders is a re-cut", recut)
        want("tools/x.py" in recut[1], "the re-cut target is named", recut)

        # ---- regression flag even when the grade is high ------------------
        spec = SPEC_BY_ID["always_loaded_bytes"]
        reg = regression_text(spec, 96, (95, "2026-09-03"))
        want("REGRESSION +1 vs 2026-09-03" in reg,
             "a metric that got worse is flagged with its delta", reg)
        want(grade(spec["direction"], spec["target"], 96) == "A",
             "a value below a lower target still grades A: the regression is "
             "flagged separately", grade(spec["direction"], spec["target"], 96))

        results = [
            dict(id="always_loaded_bytes", name="always-loaded bytes", value=96,
                 notes="96 bytes over 4 files", grade="A"),
            dict(id="manual_interventions", name="manual interventions", value=1,
                 notes="1 commit: fix a typo by hand", grade="F"),
        ]
        card = render_card("2026-09-04", results,
                           {"always_loaded_bytes": (95, "2026-09-03")}, bank)
        want("REGRESSION +1 vs 2026-09-03" in card,
             "the card prints the regression delta", card)
        want("NEXT ACTIONS" in card and "cut an order that absorbs the 1 hand-fix" in card,
             "the card prints the specific next action for the below-A- metric", card)

        # ---- DB: a throwaway postgres container ---------------------------
        name = _provision_container()
        if name is None:
            want(False, "throwaway postgres container", "docker unavailable")
        else:
            live_url = "postgres://aimem:localdev@%s:%d/aimem" % (
                ".".join(("127", "0", "0", "1")), SELFTEST_PORT)

            def container_psql(sql, tuples_only=False):
                cmd = ["docker", "exec", "-i", name, "psql", "-U", "aimem",
                       "-d", "aimem", "-v", "ON_ERROR_STOP=1"]
                if tuples_only:
                    cmd += ["-t", "-A"]
                cmd += ["-c", sql]
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                return p.returncode, p.stdout.strip(), p.stderr.strip()

            os.environ["MEMDB_URL"] = live_url
            rc, _o, err = container_psql(_read_migration("001_initial.sql"))
            want(rc == 0 and not (err or "").lower().startswith("error"),
                 "migration 001 applies cleanly", err)
            rc, _o, err = container_psql(_read_migration("005_health.sql"))
            want(rc == 0 and not (err or "").lower().startswith("error"),
                 "migration 005 applies cleanly", err)
            rc, out, _e = container_psql(
                "SELECT version FROM schema_version WHERE version = 5", tuples_only=True)
            want(out == "5", "migration 005 records schema_version 5", out)
            rc, out, _e = container_psql(
                "SELECT count(*) FROM health", tuples_only=True)
            want(out == "0", "health starts empty", out)

            rows_once = [
                dict(id="always_loaded_bytes", name="always-loaded bytes",
                     value=100, notes="100 bytes over 4 files", grade="A"),
                dict(id="manual_interventions", name="manual interventions",
                     value=2, notes="2 commits", grade="A-"),
            ]
            rc, msg = _write_rows("2026-09-04", rows_once)
            want(rc == 0, "the write succeeds", msg)
            rc, out, _e = container_psql(
                "SELECT count(*) FROM health", tuples_only=True)
            want(out == "2", "two metrics are two rows", out)
            rows_once[0]["value"] = 150
            rc, msg = _write_rows("2026-09-04", rows_once)
            want(rc == 0, "writing twice on one day still succeeds", msg)
            rc, out, _e = container_psql(
                "SELECT count(*) FROM health", tuples_only=True)
            want(out == "2", "writing twice on one day UPDATES, never duplicates",
                 out)
            rc, out, _e = container_psql(
                "SELECT value FROM health WHERE metric = 'always_loaded_bytes'",
                tuples_only=True)
            want(out == "150", "the second write updated the row", out)

            # The round trip, against a REAL stored row rather than a literal.
            # The fixtures above pass ints, so for weeks they proved only that
            # the test agreed with the code: the live column is NUMERIC, and
            # float - Decimal raised TypeError on every card render.
            prev_live = _fetch_previous("2026-09-05")
            want(prev_live is not None and "always_loaded_bytes" in prev_live,
                 "the store answers with the previous day's row", prev_live)
            stored = prev_live["always_loaded_bytes"][0]
            want(isinstance(stored, float),
                 "a NUMERIC column arrives as float, never Decimal: nothing "
                 "downstream knows the store's types",
                 type(stored).__name__)
            live_reg = regression_text(SPEC_BY_ID["always_loaded_bytes"],
                                       160.0, prev_live["always_loaded_bytes"])
            want("REGRESSION +10 vs 2026-09-04" in live_reg,
                 "a stored row drives the regression delta without raising",
                 live_reg)

            rc, msg = _write_rows("2026-09-04", [
                dict(id="driver_failures", name="driver failures", value=None,
                     notes="csv missing", grade="UNMEASURED")])
            want(rc == 0, "an UNMEASURED metric still writes", msg)
            rc, out, _e = container_psql(
                "SELECT grade, value IS NULL FROM health "
                "WHERE metric = 'driver_failures'", tuples_only=True)
            want(out == "UNMEASURED|t",
                 "unmeasurable records grade UNMEASURED and a NULL value, "
                 "never 0", out)

            for table in ("jobs", "lanes", "runs", "events", "facts", "queries"):
                rc, out, _e = container_psql(
                    "SELECT count(*) FROM %s" % table, tuples_only=True)
                want(out == "0", "nothing was written to %s" % table, out)
    finally:
        memdb._LOCAL = real_local
        os.environ.clear()
        os.environ.update(real_environ)
        sys.stdout, sys.stderr = real_out, real_err
        shutil.rmtree(tmp, ignore_errors=True)
        if name is not None:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    for label in fails:
        print("FAIL: %s" % label, file=sys.stderr)
    print("self-test: all %d checks passed" % count)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
