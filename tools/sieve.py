#!/usr/bin/env python3
"""sieve.py: drop what we will not read again, continuously, not in archaeology sessions.

    sieve.py                  what would be dropped, and why. Changes nothing.
    sieve.py --apply          git rm the drops
    sieve.py --grace 30       days a new file is protected (default 14)
    sieve.py --explain <path> the verdict for one file
    sieve.py --self-test

WHY (2026-09-07): "we need to start making an automated system to clear
and clean the reports and memory as they come, always drop stuff we wont use
again, its like a sieve."

The archaeology version of this pass found **2.2 MB across 72 files that
nothing pointed at**, most of it eight months of superseded mockups and
benchmark logs. That is not a cleaning problem, it is a MISSING SIEVE: every
one of those files was disposable the day after it was written and nothing
disposed of it. `rules/shared/memory-integrity.md` is explicit that stale files
are the largest contributor to internal token burn, because they are not
clutter, they are wrong context that reads as current.

THE THREE VERDICTS, and the reasoning is deliberately dull so it is repeatable:

  KEEP      something reached points at it, so a session can find it on purpose.
  DROP      nothing points at it, it is past the grace period, and its
            directory is one where unreferenced files are disposable by design.
  HOLD      nothing points at it but one of the guards applies: inside the
            grace period, in a protected directory, or carrying `keep: <why>`.

WHAT MAKES THIS SAFE, because a tool that deletes needs its safety stated:
  - **Dry run by default.** `--apply` is the only thing that removes anything.
  - **A grace period.** A report written this turn is unreached by definition;
    nothing younger than 14 days is ever a candidate.
  - **`keep: <reason>` in frontmatter is absolute.** A file that says why it
    should survive, survives, and the reason is printed so it can be argued
    with later.
  - **PROTECTED directories are never swept**, whatever the reachability says.
    `memory/` is protected: a memory file that nothing links yet is an INDEXING
    bug, and the fix is a line in MEMORY.md, never a deletion.
  - **It removes with `git rm`**, so every drop is one revert away.

Exit codes:
    0  nothing to drop
    1  drops found (dry run) or drops applied
    2  bad arguments
    3  cannot answer: reachability could not be computed
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Directories the sieve MAY drop from. Anything not listed is never swept.
# Enumerated, never inferred (docs/scoped-by-declaration.md).
SWEEPABLE = {
    "reports": "working artifacts; the finding belongs in a rule or a memory "
               "file, and git holds the working copy",
}

# ARCHIVE DIRECTORIES AND THEIR RETENTION WINDOW, in days. `None` means never
# sweep, and every `None` has to justify itself.
#
# Nothing inside this repo will ever POINT AT a generated archive file, so
# reachability cannot judge them and the first version of this tool skipped
# them outright. That is exempt-forever, which is not a policy: `reports/go/`
# had reached 97 launcher reports, one per run, growing without limit and
# immune to the sieve by construction (2026-09-07: add the retention
# window). Age is the only honest test available here, so age is the test.
ARCHIVE_RETENTION = {
    "reports/go": (
        30,
        "one status snapshot per launcher run; the durable facts (cost, "
        "outcome, model) are rows in automation/model-ledger.csv, so a "
        "month-old snapshot is read by nobody"),
    "queue/orders/done": (
        90,
        "finished orders. Rule 4 says the commit is the record, but a recent "
        "order is genuinely re-read when its work is revisited, so the window "
        "is long"),
    "queue/jobs/done": (
        90,
        "finished job cards from a pipeline retired 2026-08-21. Same window "
        "as orders deliberately: these age out on their own rather than "
        "being bulk-deleted on the day someone noticed them"),
    "state/fixtures": (
        None,
        "NEVER. These are loaded BY TEST CODE, which addresses them by path "
        "at run time, so neither reachability nor age can tell whether one is "
        "live. Deleting one turns a green self-test red for a reason nobody "
        "will connect to this tool"),
}

# APPEND-ONLY DROP-BOXES, swept LINE BY LINE rather than whole-file.
#
# `reports/personas/fast-lane/INBOX.md` is the case that produced this. Its own
# header, and `agents/fast-lane.md` lines 44-49, both say plainly that acting on
# an item includes DELETING its line in the same commit. The instruction was
# there all along and 231 entries accumulated anyway, because the mechanism
# eats itself: at 80 KB the file is past the ~65 KB one-call read cap, so the
# persona told to clear it CANNOT READ IT, and an entry it never sees is an
# entry it never deletes. Bloat that prevents its own cure.
#
# Age is a fair test here for a reason specific to a drop-box: RE-REPORTING IS
# FREE AND AUTOMATIC. Every persona files a line the moment it hits a hole, so
# a wrongly-dropped entry costs one re-file the next time it bites, while an
# unreadable inbox costs every future pass. The window is 4x Fast Lane's own
# stated service level ("clear it the same day it lands").
LINE_RETENTION = {
    "reports/personas/fast-lane/INBOX.md": (
        14,
        "a permission hole nobody cleared in two weeks is either already "
        "closed by other work or is not biting; either way it will be "
        "re-filed for free the next time it does"),
}

# A drop-box entry: a list item that opens with its date.
DATED_ENTRY = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")

# RAW REFUSAL LOG LINES, dropped from a drop-box at ANY age.
#
# Measured 2026-09-07: of 204 entries in the Fast Lane inbox, about 130 carried
# a single date and roughly 67 are these. They are not judgements, they are a
# driver's transcript of every refused command variant, emitted several per
# minute by one lane. The 2026-08-30 block is one order, one minute (17:08),
# and ONE already-documented cause: a `ForEach-Object { ... }` scriptblock,
# which `docs/command-forms.md` has carried a permanent Never row for since
# 2026-08-21.
#
# So the drop-box was doing two jobs and failing the important one. A refusal
# is STRUCTURED DATA and already has a home in `automation/driver-errors.csv`,
# where it can be counted and grouped. The inbox is for the entries that need
# a judgement, and burying seventy of those under a machine transcript is what
# pushed the file past the read cap its owner has to work inside.
NOISE_SUFFIXES = (
    "auto-rejecting",
    "to use this specific tool call.",
)

# Never swept, with the reason. `memory/` is the important one.
PROTECTED = {
    "memory": "an unreferenced memory file is an INDEXING bug; fix MEMORY.md",
    "rules": "standing rules, reached by persona not by link",
    "docs": "canonical homes; an unreferenced doc is a pathfinding bug",
    "queue": "live state and the order archive",
    "db": "migrations",
    "tools": "the command surface",
    "hooks": "run by the harness",
    "reference": "generated indexes and the knowledge table",
    "state": "per-machine state",
    "automation": "workflows and container definitions",
    "agents": "spawnable agents",
    "commands": "slash commands",
    "assets": "source images and CSS",
    "prompts": "append-only",
    "timelog": "append-only",
    "urgent": "the owner's fast lane",
    "install": "installers",
}

KEEP_RE = re.compile(r"^keep:\s*(.+)$", re.MULTILINE)
EXPIRES_RE = re.compile(r"^expires:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _load_reachable():
    """Import the reachability tool as a MODULE, never scrape its stdout.

    `rules/shared/memory-integrity.md`: one tool must never parse another
    tool's prose, because a regex miss returns an empty list that the caller
    reports as a calm wrong answer. Here a miss would mean "nothing is
    reached", and this tool deletes things.
    """
    path = REPO / "tools" / "reachable.py"
    spec = importlib.util.spec_from_file_location("reachable", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def top_dir(path: str) -> str:
    return path.split("/", 1)[0]


def age_days(path: str) -> float:
    """Days since the file last changed IN GIT, falling back to mtime.

    Git time, not mtime, because a checkout, a rebase or a submodule sync
    rewrites every mtime and would make the whole repo look new, which is the
    direction that silently disables the sieve.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "-1", "--format=%ct", "--", path],
        capture_output=True, text=True, timeout=30)
    stamp = out.stdout.strip()
    if out.returncode == 0 and stamp.isdigit():
        return (time.time() - int(stamp)) / 86400.0
    try:
        return (time.time() - (REPO / path).stat().st_mtime) / 86400.0
    except OSError:
        return 0.0


def frontmatter(path: str) -> str:
    try:
        text = (REPO / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:2000]


def archive_rule(path: str) -> tuple[str, int | None, str] | None:
    """The LONGEST matching archive prefix, so a nested rule beats its parent."""
    best: tuple[str, int | None, str] | None = None
    for prefix, (days, reason) in ARCHIVE_RETENTION.items():
        if path.startswith(prefix + "/") and (
                best is None or len(prefix) > len(best[0])):
            best = (prefix, days, reason)
    return best


def verdict(path: str, reached: set[str], grace: float) -> tuple[str, str]:
    if path in reached:
        return "KEEP", "something reached points at it"
    head = frontmatter(path)
    keep = KEEP_RE.search(head)
    if keep:
        return "HOLD", f"keep: {keep.group(1).strip()}"

    # ARCHIVE IS CHECKED BEFORE PROTECTED, and the order is load-bearing:
    # `queue/` is a protected top-level directory, so `queue/orders/done/` would
    # otherwise be held forever by its parent and the retention window would
    # never fire.
    archive = archive_rule(path)
    if archive is not None:
        prefix, days, reason = archive
        if days is None:
            return "HOLD", f"{prefix}/ is never swept: {reason}"
        age = age_days(path)
        if age > days:
            return "DROP", (f"{prefix}/ retains {days} days, this is "
                            f"{age:.0f} days old")
        return "HOLD", (f"{prefix}/ retains {days} days, this is "
                        f"{age:.0f} days old")

    top = top_dir(path)
    if top in PROTECTED:
        return "HOLD", f"{top}/ is protected: {PROTECTED[top]}"
    if top not in SWEEPABLE:
        return "HOLD", f"{top}/ is not a sweepable directory"
    expires = EXPIRES_RE.search(head)
    if expires:
        return "DROP", f"expired {expires.group(1)}"
    age = age_days(path)
    if age < grace:
        return "HOLD", f"only {age:.0f} days old, inside the {grace:.0f}-day grace"
    return "DROP", f"unreferenced and {age:.0f} days old"


def collect(grace: float) -> list[tuple[str, str, str]]:
    reachable = _load_reachable()
    reachable.REPO = REPO
    result = reachable.analyse(reachable.ROOTS)
    reached: set[str] = set(result["reached"])
    rows = []
    for path in result["files"]:
        state, why = verdict(path, reached, grace)
        if state != "KEEP":
            rows.append((state, path, why))
    return rows


def sieve_lines(path: str, days: int, today: str | None = None
                ) -> tuple[list[str], list[str]]:
    """Split a drop-box into (kept lines, dropped lines) by entry date.

    A line is only ever a candidate when it OPENS with its own date, so the
    file's header prose, its section titles and any undated note are untouchable
    by construction rather than by care.
    """
    import datetime as _dt
    now = (_dt.date.fromisoformat(today) if today else _dt.date.today())
    cutoff = now - _dt.timedelta(days=days)
    kept: list[str] = []
    dropped: list[str] = []
    try:
        text = (REPO / path).read_text(encoding="utf-8")
    except OSError:
        return [], []
    # AN ENTRY IS A BLOCK, NOT A LINE, and getting that wrong leaves orphaned
    # prose behind. Most drop-box entries wrap over several lines with an
    # indented continuation; dropping only the `- <date>` line would strip the
    # date and the verdict and leave the argument sitting there looking like
    # something someone wrote on purpose.
    #
    # An entry runs from its `- <date>` line until the next line that starts a
    # new list item or a heading. Blank lines inside it are part of it.
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        match = DATED_ENTRY.match(line)
        if not match:
            kept.append(line)
            index += 1
            continue
        block = [line]
        index += 1
        while index < len(lines):
            nxt = lines[index]
            if nxt.startswith("- ") or nxt.startswith("#"):
                break
            block.append(nxt)
            index += 1
        # Trailing blank lines belong to the separator, not the entry, so
        # dropping an entry never eats the blank line before the next one.
        while len(block) > 1 and not block[-1].strip():
            index -= 1
            block.pop()

        body = "".join(block).rstrip()
        # A raw refusal transcript goes at any age: it belongs in
        # automation/driver-errors.csv, and it is what makes this file
        # unreadable for the persona that has to clear it.
        if body.endswith(NOISE_SUFFIXES):
            dropped.extend(block)
            continue
        try:
            stamp = _dt.date.fromisoformat(match.group(1))
        except ValueError:
            kept.extend(block)         # an unparseable date is never dropped
            continue
        (dropped if stamp < cutoff else kept).extend(block)
    return kept, dropped


def size_kb(path: str) -> int:
    try:
        return (REPO / path).stat().st_size // 1024
    except OSError:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--grace", type=float, default=14.0)
    parser.add_argument("--explain", metavar="PATH")
    parser.add_argument("--quiet", action="store_true",
                        help="one summary line, for an end-of-turn check")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        rows = collect(args.grace)
    except Exception as exc:  # noqa: BLE001 - any failure is a cannot-answer
        print(f"CANNOT ANSWER: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    if args.explain:
        target = args.explain.replace("\\", "/").lstrip("./")
        for state, path, why in rows:
            if path == target:
                print(f"{state}  {target}  ({why})")
                return 0
        print(f"KEEP  {target}  (something reached points at it, "
              f"or it is in an archive directory)")
        return 0

    drops = [r for r in rows if r[0] == "DROP"]
    holds = [r for r in rows if r[0] == "HOLD"]
    total = sum(size_kb(p) for _, p, _ in drops)

    # Line-level sieve over the append-only drop-boxes.
    line_work: list[tuple[str, list[str], list[str], str]] = []
    for path, (days, reason) in LINE_RETENTION.items():
        if not (REPO / path).exists():
            continue
        kept, dropped = sieve_lines(path, days)
        if dropped:
            line_work.append((path, kept, dropped, reason))

    # --quiet suppresses the LISTING, never the action. An earlier version
    # returned here, so `--apply --quiet` printed a count and removed nothing:
    # a flag combination that silently does the opposite of what it says.
    entry_count = sum(len(d) for _, _, d, _ in line_work)
    if args.quiet:
        print(f"sieve: {len(drops)} file(s), {total} KB ready to drop"
              + (f"; {entry_count} stale drop-box entr(ies)" if entry_count
                 else "")
              + " (run tools/sieve.py to see them)")
    else:
        # Archives are held BY THE HUNDRED once retention applies to them, so
        # the hold list is summarised per reason. Printing 480 identical lines
        # is how a report stops being read, which is the same disease as the
        # litter it is reporting.
        by_reason: dict[str, list[str]] = {}
        for _, path, why in holds:
            by_reason.setdefault(why.split(",")[0], []).append(path)
        for reason in sorted(by_reason, key=lambda r: -len(by_reason[r])):
            paths = by_reason[reason]
            kb = sum(size_kb(p) for p in paths)
            print(f"HOLD  {kb:6} KB  {len(paths):4} file(s)  <- {reason}")
            if len(paths) <= 3:
                for path in sorted(paths):
                    print(f"                          {path}")
        print()
        for state, path, why in sorted(drops, key=lambda r: -size_kb(r[1])):
            print(f"DROP  {size_kb(path):5} KB  {path}  <- {why}")
        for path, kept, dropped, reason in line_work:
            now_kb = len("".join(kept).encode()) // 1024
            print(f"TRIM  {size_kb(path):5} KB -> {now_kb} KB  {path}")
            print(f"        {len(dropped)} entr(ies) older than "
                  f"{LINE_RETENTION[path][0]} days  <- {reason}")
        print(f"\n{len(drops)} drop(s), {total} KB; {len(holds)} held"
              + (f"; {entry_count} drop-box entr(ies) to trim"
                 if entry_count else ""))

    if not drops and not line_work:
        return 0
    if not args.apply:
        print("dry run. Re-run with --apply to act.")
        return 1

    paths = [p for _, p, _ in drops]
    for chunk in (paths[i:i + 40] for i in range(0, len(paths), 40)):
        out = subprocess.run(["git", "-C", str(REPO), "rm", "-q", *chunk],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            print(f"git rm failed: {out.stderr.strip()}", file=sys.stderr)
            return 3
    for path, kept, dropped, _ in line_work:
        (REPO / path).write_text("".join(kept), encoding="utf-8")
        print(f"trimmed {len(dropped)} entr(ies) from {path}")
    if paths:
        print(f"removed {len(paths)} file(s). Commit them.")
    return 1


def _self_test() -> int:
    import shutil
    import tempfile
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(f"{'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    global REPO
    saved = REPO
    root = Path(tempfile.mkdtemp(prefix="sieve-selftest-"))
    try:
        # The fixture is a real git repo with a real reachable.py beside it,
        # because the whole tool hangs on that import.
        (root / "tools").mkdir()
        shutil.copy(saved / "tools" / "reachable.py", root / "tools")
        (root / "reports").mkdir()
        (root / "memory").mkdir()
        (root / "CLAUDE.md").write_text("we keep reports/kept.md\n",
                                        encoding="utf-8")
        (root / "reports" / "kept.md").write_text("named by CLAUDE.md\n",
                                                  encoding="utf-8")
        (root / "reports" / "stale.md").write_text("nobody names me\n",
                                                   encoding="utf-8")
        (root / "reports" / "declared.md").write_text(
            "---\nkeep: the only copy of the walk contract\n---\nbody\n",
            encoding="utf-8")
        (root / "memory" / "unindexed.md").write_text("orphan memory\n",
                                                      encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.email=fixture",
                        "-c", "user.name=t", "commit", "-qm", "fixture"],
                       check=True)
        REPO = root

        rows = {path: (state, why) for state, path, why in collect(grace=14)}

        check("reports/kept.md" not in rows,
              "a file something points at is KEEP and never listed")
        check(rows["reports/stale.md"][0] == "HOLD"
              and "grace" in rows["reports/stale.md"][1],
              "a brand new unreferenced report is HELD by the grace period")
        no_grace = {path: state for state, path, _ in collect(grace=0)}
        check(no_grace["reports/stale.md"] == "DROP",
              "with the grace at zero the same file becomes a DROP")
        check(no_grace["memory/unindexed.md"] == "HOLD",
              "the grace is not what protects memory/; zero grace keeps it HELD")
        check(rows["reports/declared.md"][0] == "HOLD"
              and "keep:" in rows["reports/declared.md"][1],
              "a `keep:` reason overrides everything and prints the reason")
        check(rows["memory/unindexed.md"][0] == "HOLD"
              and "protected" in rows["memory/unindexed.md"][1],
              "an unindexed MEMORY file is HELD, never dropped")

        # The safety that matters most: a dry run must not remove anything.
        before = sorted(p.name for p in (root / "reports").iterdir())
        main(["--grace", "0", "--quiet"])
        after = sorted(p.name for p in (root / "reports").iterdir())
        check(before == after, "a dry run removes nothing from disk")

        rc = main(["--grace", "0", "--quiet"])
        check(rc == 1, "having drops exits 1")
        rc = main(["--grace", "9999", "--quiet"])
        check(rc == 0, "with everything inside the grace it exits 0")

        # --quiet must suppress the listing, never the action. This combination
        # printed a count and removed nothing until 2026-09-07.
        main(["--grace", "0", "--quiet", "--apply"])
        gone = not (root / "reports" / "stale.md").exists()
        check(gone, "--apply --quiet actually removes the file")
        check((root / "reports" / "kept.md").exists(),
              "--apply left the reached file alone")
        check((root / "memory" / "unindexed.md").exists(),
              "--apply left the protected memory file alone")

        # ---- the retention window on archive directories ----
        # Archives are unreachable BY DESIGN, so age is the only test. Both
        # sides are asserted: inside the window is HELD, past it is DROPPED,
        # and a `None` window is never dropped however old.
        global ARCHIVE_RETENTION
        saved_retention = ARCHIVE_RETENTION
        try:
            (root / "reports" / "go").mkdir()
            (root / "state").mkdir()
            (root / "state" / "fixtures").mkdir()
            (root / "reports" / "go" / "run.md").write_text("snapshot\n",
                                                            encoding="utf-8")
            (root / "state" / "fixtures" / "f.md").write_text("fixture\n",
                                                              encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=fixture",
                            "-c", "user.name=t", "commit", "-qm", "arch"],
                           check=True)

            ARCHIVE_RETENTION = {
                "reports/go": (30, "test window"),
                "state/fixtures": (None, "never"),
            }
            fresh = {p: (s, w) for s, p, w in collect(grace=0)}
            check(fresh["reports/go/run.md"][0] == "HOLD",
                  "an archive file inside its retention window is HELD")
            check("retains 30 days" in fresh["reports/go/run.md"][1],
                  "the hold says what the window is and how old the file is")
            check(fresh["state/fixtures/f.md"][0] == "HOLD",
                  "a None retention window is never dropped")

            ARCHIVE_RETENTION = {
                "reports/go": (-1, "test window, already expired"),
                "state/fixtures": (None, "never"),
            }
            aged = {p: (s, w) for s, p, w in collect(grace=0)}
            check(aged["reports/go/run.md"][0] == "DROP",
                  "the SAME file drops once past its window, so the window "
                  "is what decides and not something constant")
            check(aged["state/fixtures/f.md"][0] == "HOLD",
                  "a None window still holds when its neighbour is dropping")
        finally:
            ARCHIVE_RETENTION = saved_retention

        # ---- the line-level sieve over a drop-box ----
        box = root / "box.md"
        box.write_text(
            "# header prose that must never be touched\n"
            "\n"
            "## Open\n"
            "- 2020-01-01 ancient, a hole nobody cleared\n"
            "- 2099-12-31 future, still live\n"
            "- undated note with no leading date at all\n"
            "- 20xx-bad-date malformed\n",
            encoding="utf-8")
        kept, dropped = sieve_lines("box.md", days=14, today="2026-09-07")
        text = "".join(kept)
        check(len(dropped) == 1 and "2020-01-01" in dropped[0],
              "an entry past the window is dropped")
        check("2099-12-31" in text, "an entry inside the window is kept")
        check("# header prose" in text and "## Open" in text,
              "header prose and section titles are never candidates")
        check("undated note" in text,
              "a list item with no leading date is never dropped")
        check("20xx-bad-date" in text,
              "an unparseable date is kept, never guessed at")
        check(len(kept) + len(dropped) ==
              len(box.read_text(encoding="utf-8").splitlines()),
              "every line is accounted for: nothing is silently lost")

        # A raw refusal transcript goes at ANY age, including today's.
        box.write_text(
            "- 2099-12-31 driver , `Get-Content x | ForEach-Object { $_ }`, "
            "bash (...); auto-rejecting\n"
            "- 2099-12-31 quarterback , a real judgement that must survive\n",
            encoding="utf-8")
        kept, dropped = sieve_lines("box.md", days=14, today="2026-09-07")
        check(len(dropped) == 1 and "auto-rejecting" in dropped[0],
              "a raw refusal transcript is dropped even dated in the future")
        check(len(kept) == 1 and "real judgement" in kept[0],
              "a judgement on the same date survives beside it")

        # AN ENTRY IS A BLOCK. Dropping one must take its continuation lines
        # with it, or the file keeps a headless paragraph that reads like prose
        # somebody wrote deliberately.
        box.write_text(
            "## Open\n"
            "\n"
            "- 2020-01-01 old entry, first line\n"
            "  continued argument on a second line\n"
            "  and a third\n"
            "\n"
            "- 2099-12-31 live entry\n"
            "  its own continuation\n",
            encoding="utf-8")
        kept, dropped = sieve_lines("box.md", days=14, today="2026-09-07")
        text = "".join(kept)
        check("continued argument" not in text and "and a third" not in text,
              "dropping an entry takes its continuation lines with it")
        check("its own continuation" in text,
              "the surviving entry keeps its own continuation")
        check("## Open" in text, "the section heading is untouched")
        check(len(dropped) == 3,
              f"the whole three-line block was dropped (got {len(dropped)})")
        check(text.count("- 2099-12-31") == 1 and "\n\n- 2099" in text,
              "the blank line separating entries survives the drop")
    finally:
        REPO = saved
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
