#!/usr/bin/env python3
"""union-rows.py: recover rows an append-only data file lost to a side-pick.

    python tools/union-rows.py stash@{1} automation/model-ledger.csv
    python tools/union-rows.py stash@{1} --apply automation/*.csv
    python tools/union-rows.py --self-test

WHY THIS EXISTS. Golden rule A10 (`rules/claude/git-github.md`) already says
what to do when an append-only data file diverges: **UNION, never pick a side.**
Nothing could actually perform one. So the instruction was carried out by hand,
and on 2026-09-03 a `--theirs` on `automation/driver-errors.csv` silently
dropped **781 rows**; the loss was invisible until someone happened to read a
`diff --stat`. A rule with no mechanism is a wish (`rules/shared/memory-integrity.md`).

THE SECOND SHAPE IT ANSWERS, measured 2026-09-16: two `session-start-sync`
autostashes had sat unpopped since 2026-09-13, holding **107 rows** across four
append-only files: driver errors, model-ledger spend rows, driver prompts and
session timings. Every one was a measurement, and "every run is a data point and
recording it is part of the run" (`rules/shared/cost.md`). Dropping the stash to
tidy up would have thrown them away, and nothing would ever have said so.

WHAT IT DOES, and the conservative choice is deliberate: the working-tree file is
left byte-for-byte alone and the rows the other revision has that it lacks are
APPENDED at the end. Every reader of these files keys on the timestamp COLUMN,
never on line position, so append order is not load-bearing; re-sorting would
rewrite the whole file and turn a recovery into an unreviewable diff.

IT REFUSES rather than guesses: a header mismatch means these are not two
versions of one file, and a union of them would be garbage.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def _git_show(rev: str, path: str) -> str | None:
    """The file's content at `rev`, or None when it does not exist there.

    None and "" ARE DIFFERENT and the distinction is the whole point: an empty
    string is a file that exists and holds nothing, None is "that revision has
    no such file". Collapsing them reports "nothing to recover" for a path that
    was never looked at (`rules/shared/memory-integrity.md` rule 2: never infer
    that something did not happen from the absence of a record).
    """
    res = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    if res.returncode != 0:
        return None
    return res.stdout


def _rows(text: str) -> tuple[str, list[str]]:
    """(header, rows) with blank lines dropped. An empty file has no header."""
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return "", []
    return lines[0], lines[1:]


def union(current: str, other: str) -> tuple[list[str], str | None]:
    """(the missing rows, in `other`'s order; a refusal reason or None).

    PURE, so it is tested without git, a repo or a stash.
    """
    cur_header, cur_rows = _rows(current)
    oth_header, oth_rows = _rows(other)
    if not oth_header:
        return [], None
    if cur_header and cur_header != oth_header:
        return [], (f"header mismatch, so these are not two versions of one "
                    f"file: {cur_header!r} vs {oth_header!r}")
    seen = set(cur_rows)
    missing, added = [], set()
    for row in oth_rows:
        if row in seen or row in added:
            continue
        missing.append(row)
        added.add(row)
    return missing, None


def apply_union(path: Path, header: str, missing: list[str]) -> None:
    """Append the missing rows, and nothing else. Creates the file if absent."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing.strip():
        body = "\n".join([header] + missing) + "\n"
    else:
        sep = "" if existing.endswith("\n") else "\n"
        body = existing + sep + "\n".join(missing) + "\n"
    path.write_text(body, encoding="utf-8", newline="\n")


def run(rev: str, paths: list[str], apply: bool) -> int:
    recovered = problems = 0
    for rel in paths:
        rel = rel.replace("\\", "/")
        target = REPO / rel
        other = _git_show(rev, rel)
        if other is None:
            print(f"  {rel}: NOT IN {rev}, so nothing was compared")
            continue
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        missing, refusal = union(current, other)
        if refusal:
            print(f"  {rel}: REFUSED, {refusal}", file=sys.stderr)
            problems += 1
            continue
        if not missing:
            print(f"  {rel}: already a superset of {rev}, 0 rows to recover")
            continue
        recovered += len(missing)
        if not apply:
            print(f"  {rel}: {len(missing)} row(s) in {rev} are MISSING here")
            continue
        apply_union(target, _rows(other)[0], missing)
        print(f"  {rel}: appended {len(missing)} recovered row(s)")
    verb = "recovered" if apply else "pending; re-run with --apply"
    print(f"\n{recovered} row(s) {verb}")
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="union-rows")
    ap.add_argument("rev", nargs="?", help="the other side: a stash, branch or sha")
    ap.add_argument("paths", nargs="*", help="repo-relative append-only files")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.rev or not a.paths:
        ap.error("need a revision and at least one path")
    print(f"union-rows: {a.rev} -> working tree")
    return run(a.rev, a.paths, a.apply)


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    head = "ts,detail"
    cur = f"{head}\na\nb\n"

    # THE WHOLE POINT: a row only the other side has is never lost.
    missing, refusal = union(cur, f"{head}\nb\nc\n")
    want("a row only the other side has is recovered", missing == ["c"])
    want("and it is not a refusal", refusal is None)

    # AND A SIDE-PICK IS WHAT THIS PREVENTS: rows present here are never
    # duplicated, so running it twice is safe.
    missing, _ = union(cur, f"{head}\na\nb\n")
    want("an identical other side recovers nothing, so re-running is safe",
         missing == [])
    missing, _ = union(cur, f"{head}\nc\nc\nd\n")
    want("a duplicate row inside the other side is recovered once",
         missing == ["c", "d"])

    # ORDER IS THE OTHER SIDE'S, because that is the order it recorded them in.
    missing, _ = union(cur, f"{head}\nz\nc\n")
    want("recovered rows keep the other side's own order", missing == ["z", "c"])

    # A HEADER MISMATCH REFUSES. Red-proven by dropping the header check: the
    # union then splices one file's rows into another and reports success.
    missing, refusal = union(cur, "other,header\nx\n")
    want("a header mismatch REFUSES instead of splicing two different files",
         missing == [] and refusal is not None)

    # AN EMPTY WORKING-TREE FILE HAS NO HEADER TO DISAGREE WITH, so recovery
    # into a file a bad merge truncated still works.
    missing, refusal = union("", f"{head}\na\n")
    want("recovery into an empty file works and does not refuse",
         missing == ["a"] and refusal is None)
    missing, _ = union(cur, "")
    want("an empty other side recovers nothing", missing == [])

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "f.csv"
        p.write_text(cur, encoding="utf-8")
        apply_union(p, head, ["c"])
        want("applying appends and leaves every existing row untouched",
             p.read_text(encoding="utf-8") == f"{head}\na\nb\nc\n")

        # A FILE WITH NO TRAILING NEWLINE must not glue two rows together.
        p.write_text(f"{head}\na", encoding="utf-8")
        apply_union(p, head, ["b"])
        want("a file with no trailing newline does not glue rows together",
             p.read_text(encoding="utf-8") == f"{head}\na\nb\n")

        p2 = Path(tmp) / "new.csv"
        apply_union(p2, head, ["a"])
        want("a missing target is created with its header",
             p2.read_text(encoding="utf-8") == f"{head}\na\n")

    if fails:
        print(f"\nunion-rows self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nunion-rows self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
