#!/usr/bin/env python3
"""ptr.py: the linking mechanism. Resolve a pointer, and refuse to let one rot.

    python tools/ptr.py store:facts/lane/id_is_derived   resolve one pointer
    python tools/ptr.py --check                          every pointer in the repo
    python tools/ptr.py --list-tables                    what may be pointed at
    python tools/ptr.py --self-test

WHY THIS EXISTS (2026-09-16): "please make sure that the repository stores
mostly pointers to the REAL data stores. this makes the linking mechanism the
main focus here ... the thing at the end of the pointer must also be stored in
the most efficient means possible such that we can section ourselves off well."

THE LINKING MECHANISM IS THE LOAD-BEARING PART, not the pointing. "If it can be
a pointer it should be" has been a rule since 2026-08-07 and it has always had
the same hole, written into the rule itself: **a pointer with nothing to point at
becomes a copy.** Nothing could tell the difference, so a reference that rotted
read exactly like one that worked, and a session followed it and believed what
it found. That is the fog-of-war fault: wrong context that reads as current
(`rules/shared/memory-integrity.md`).

A file path at least fails loudly when a session opens it. A STORE ROW does not:
`facts/lane/id_is_derived` is a string in a markdown file, and if the row is
renamed or superseded the string keeps looking fine. So the moment the repo holds
mostly store pointers, an unverifiable pointer becomes the dominant failure mode
rather than an edge case. This is the check that stops that, and `lint.py` runs
it so it cannot be skipped.

THE SYNTAX, one spelling, greppable:

    store:<table>/<key>          a row in the shared store   store:queries/facts
    store:facts/<subject>/<key>  a fact, which is keyed twice

A FACT RESOLVES ONLY WHILE IT IS CURRENT. `facts` carries `valid_to`, so a
superseded fact is kept for provenance and is NOT the answer; pointing at one is
a dangler, loudly, because the whole point of the store is that recency is free.

"COULD NOT ASK" IS NEVER "NOTHING THERE" and exits 3, never 0 and never 1. An
unreachable store is the one state where this tool must not render a verdict
(`rules/shared/memory-integrity.md` rule 2, and `tools/pc.py` is the precedent).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# WHICH TABLE IS KEYED BY WHAT, DECLARED, never worked out at runtime
# (`docs/scoped-by-declaration.md`). A resolver that inferred the key column
# from the schema would silently start resolving a table nobody meant to expose.
STORE_KEYS: dict[str, tuple[str, ...]] = {
    "facts": ("subject", "key"),
    "findings": ("slug",),
    "queries": ("name",),
    "commands": ("id",),
    "jobs": ("id",),
    "lanes": ("id",),
    "runs": ("id",),
    "health": ("metric",),
    # ADDED 2026-09-16, and the gap is the lesson. `projects` and
    # `project_state` went live in `db/012_projects.sql` and this declared list
    # was not updated, so the FIRST memory file folded into them pointed at two
    # tables the resolver refused by name. A declared list is the right shape
    # (`docs/scoped-by-declaration.md`) precisely because it fails loudly like
    # this instead of resolving whatever the schema happens to hold.
    #
    # `project_state` is keyed by the PROJECT, not by a row id: a project has
    # several state lines and a pointer means "where does this project stand",
    # which is the question a reader actually has.
    "projects": ("id",),
    "project_state": ("project_id",),
}

# The column worth printing first when a pointer resolves, so the answer is the
# THING and not a row dump.
STORE_VALUE: dict[str, str] = {
    "facts": "value",
    "findings": "title",
    "queries": "question",
    "commands": "form",
    "jobs": "name",
    "lanes": "worktree_path",
    "runs": "verdict",
    "health": "value",
    "projects": "name",
    "project_state": "body",
}

# THIS FILE DEFINES THE SYNTAX, so its own examples and self-test fixtures are
# not references to check. One path, declared, because an exclusion nobody can
# see is how a checker quietly stops checking.
NOT_SCANNED = frozenset({"tools/ptr.py"})

# ARCHIVES ARE A RECORD OF WHAT WAS TRUE THEN, so their links are allowed to
# rot. A finished card cited a memory file that has since been folded into the
# store; editing that card to keep the link green would falsify the record of
# what the card actually said. Declared here rather than judged per finding,
# because an exclusion nobody can see is how a checker quietly stops checking.
NOT_SCANNED_UNDER = ("queue/jobs/done/", "queue/orders/done/")

# Where pointers are allowed to live. Binary trees and vendored state are not
# prose and scanning them only invents findings.
SCANNED_SUFFIXES = (".md", ".py", ".ps1", ".sh", ".toon", ".json", ".jsonc")

POINTER = re.compile(r"store:([a-z_]+)((?:/[A-Za-z0-9_.\-]+)+)")

# THE OTHER TWO KINDS OF POINTER, added 2026-09-16. Store rows were never the
# majority: almost every link in this repo is a file, and `memory/` links to
# itself with the auto-memory `[[slug]]` convention.
WIKILINK = re.compile(r"\[\[([A-Za-z0-9_.\-]+)\]\]")
# A markdown link to a repo file. `\.md` only, and no scheme, so an http URL
# and a bare word are both left alone: this checks the links that CAN be
# resolved locally, and says nothing about the ones that cannot.
MDLINK = re.compile(r"\[[^\]\n]+\]\((?!\w+://)([^)\s#]+\.md)(?:#[^)\n]*)?\)")


class CouldNotAsk(Exception):
    """The store was unreachable. A distinct state from 'the row is missing'."""


def _memdb():
    spec = importlib.util.spec_from_file_location("memdb", HERE / "memdb.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse(pointer: str) -> tuple[str, tuple[str, ...]]:
    """(table, key parts). Raises ValueError with the reason, never a guess."""
    m = POINTER.fullmatch(pointer.strip())
    if not m:
        raise ValueError(f"not a store pointer: {pointer!r}")
    table = m.group(1)
    parts = tuple(p for p in m.group(2).split("/") if p)
    want = STORE_KEYS.get(table)
    if want is None:
        raise ValueError(f"no table {table!r} may be pointed at; "
                         f"declared: {', '.join(sorted(STORE_KEYS))}")
    if len(parts) != len(want):
        raise ValueError(f"store:{table} is keyed by {'/'.join(want)}, so it "
                         f"needs {len(want)} key part(s), got {len(parts)}")
    return table, parts


def resolve(pointer: str, query) -> list[list]:
    """The rows a pointer names. Empty list means the pointer DANGLES.

    `query` is injected so this is tested without a database.
    """
    table, parts = parse(pointer)
    keys = STORE_KEYS[table]
    where = " and ".join(f"{col} = %s" for col in keys)
    # A FACT MUST STILL BE CURRENT. Superseded rows stay for provenance and are
    # deliberately not reachable by pointer.
    if table == "facts":
        where += " and valid_to is null"
    col = STORE_VALUE[table]
    return query(f"select {col} from {table} where {where}", tuple(parts))


def scan(root: Path) -> list[tuple[Path, int, str]]:
    """Every store pointer in the repo, as (file, line number, pointer)."""
    out: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in NOT_SCANNED or rel.startswith(".git/"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "store:" not in text:
            continue
        for n, line in enumerate(text.split("\n"), start=1):
            for m in POINTER.finditer(line):
                out.append((path, n, m.group(0)))
    return out


def file_links(root: Path) -> list[tuple[Path, int, str, str, Path]]:
    """Every resolvable FILE pointer: (file, line, raw, kind, target).

    Two kinds, and they resolve differently:
      `wiki`  `[[slug]]` in a memory file, which means `memory/<slug>.md`
      `md`    a markdown link to a `.md` path, relative to its OWN directory
    """
    out: list[tuple[Path, int, str, str, Path]] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if (rel in NOT_SCANNED or rel.startswith(".git/")
                or rel.startswith(NOT_SCANNED_UNDER)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.split("\n"), start=1):
            for m in WIKILINK.finditer(line):
                slug = m.group(1)
                out.append((path, n, m.group(0), "wiki",
                            root / "memory" / f"{slug}.md"))
            for m in MDLINK.finditer(line):
                target = m.group(1)
                out.append((path, n, target, "md",
                            (path.parent / target).resolve()))
    return out


def check(root: Path, query) -> tuple[int, list[str]]:
    """(dangler count, the lines to print). One query per distinct pointer."""
    found = scan(root)
    seen: dict[str, bool] = {}
    problems: list[str] = []
    for path, n, pointer in found:
        if pointer not in seen:
            try:
                seen[pointer] = bool(resolve(pointer, query))
            except ValueError as exc:
                seen[pointer] = False
                problems.append(f"{path.relative_to(root).as_posix()}:{n}  "
                                f"{exc}")
                continue
        if not seen[pointer]:
            problems.append(f"{path.relative_to(root).as_posix()}:{n}  "
                            f"DANGLES: {pointer} resolves to nothing")
    return len(problems), problems


def ever_existed(root: Path, target: Path) -> bool:
    """Did git ever track this path? Distinguishes DELETED from NOT-YET-WRITTEN.

    WITHOUT THIS THE TOOL LIES. A dangling `[[slug]]` has two opposite meanings
    and they must not share a message: "nobody has written it yet", which the
    repository convention permits deliberately, versus "the file it pointed
    at was deleted", which is a broken reference. Measured 2026-09-16, minutes
    after the file-link check shipped: two memory files were folded into the
    store and deleted, and this tool immediately reported the references to them
    as something worth writing later. That is the instrument stating an
    inference as a fact (golden rule 2).
    """
    try:
        rel = target.relative_to(root).as_posix()
    except ValueError:
        return False
    res = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "--oneline", "-1", "--", rel],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60)
    return res.returncode == 0 and bool(res.stdout.strip())


def check_files(root: Path) -> tuple[list[str], list[str]]:
    """(broken markdown links, wikilinks with no file yet).

    THE TWO ARE NOT THE SAME SEVERITY and must not be reported together. A
    broken markdown link is a defect: it was written as a route to something
    and the route is gone. **A dangling `[[slug]]` is not an error at all** by
    the repository convention: "a `[[name]]` that doesn't match an existing
    memory yet is fine; it marks something worth writing later". Reporting those
    as failures would make the check red forever and teach the next session to
    ignore it, which is the fault this tool was written to remove.
    """
    broken: list[str] = []
    todo: list[str] = []
    for path, n, raw, kind, target in file_links(root):
        if target.exists():
            continue
        where = f"{path.relative_to(root).as_posix()}:{n}"
        if kind == "md":
            broken.append(f"{where}  BROKEN LINK: {raw}")
        elif ever_existed(root, target):
            broken.append(f"{where}  BROKEN LINK: {raw} pointed at "
                          f"{target.name}, which git shows was DELETED")
        else:
            todo.append(f"{where}  {raw} has no memory file yet")
    return broken, todo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ptr")
    ap.add_argument("pointer", nargs="?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list-tables", action="store_true")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if a.list_tables:
        for table in sorted(STORE_KEYS):
            print(f"store:{table}/" + "/".join(f"<{k}>" for k in STORE_KEYS[table]))
        return 0

    memdb = _memdb()

    def query(sql, params):
        try:
            return memdb._query(sql, params)
        except Exception as exc:  # unreachable store, missing driver, bad creds
            raise CouldNotAsk(str(exc)) from exc

    if a.check:
        root = Path(a.root).resolve()
        try:
            count, problems = check(root, query)
        except CouldNotAsk as exc:
            print(f"ptr: could NOT ask the store ({exc}). No pointer was "
                  f"checked, which is UNCHECKED, not clean.", file=sys.stderr)
            return 3
        total = len({p for _, _, p in scan(root)})
        broken, todo = check_files(root)
        links = len(file_links(root))
        for line in problems + broken:
            print(f"  {line}")
        if todo:
            print(f"\n{len(todo)} wikilink(s) name a memory file that does not "
                  f"exist yet. That is NOT an error: it marks something worth "
                  f"writing. Not counted.")
        bad = count + len(broken)
        if bad:
            print(f"\n{bad} rotten pointer(s): {count} store, {len(broken)} "
                  f"file, of {total} store pointer(s) and {links} file link(s)")
            return 1
        print(f"ptr: {total} store pointer(s) and {links} file link(s) checked, "
              f"all resolve")
        return 0

    if not a.pointer:
        ap.error("give a pointer, or --check")
    try:
        rows = resolve(a.pointer, query)
    except ValueError as exc:
        print(f"ptr: {exc}", file=sys.stderr)
        return 2
    except CouldNotAsk as exc:
        print(f"ptr: could NOT ask the store ({exc}). That is UNRESOLVED, not "
              f"missing.", file=sys.stderr)
        return 3
    if not rows:
        print(f"ptr: {a.pointer} DANGLES, nothing at the end of it",
              file=sys.stderr)
        return 1
    for row in rows:
        print(row[0])
    return 0


def self_test() -> int:
    import tempfile
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    want("a fact needs BOTH its key parts",
         parse("store:facts/lane/id_is_derived") == ("facts",
                                                     ("lane", "id_is_derived")))
    want("a singly-keyed table takes one part",
         parse("store:queries/facts") == ("queries", ("facts",)))

    def refuses(pointer):
        try:
            parse(pointer)
        except ValueError:
            return True
        return False

    # AN UNDECLARED TABLE IS REFUSED. Red-proven by dropping the STORE_KEYS
    # lookup: a pointer at any table name then builds SQL against it.
    want("a table nobody declared is REFUSED, not queried",
         refuses("store:pg_shadow/x"))
    want("a fact with one key part is refused, because it would match many rows",
         refuses("store:facts/lane"))
    want("a fact with three key parts is refused", refuses("store:facts/a/b/c"))
    want("a plain file path is not a store pointer", refuses("queue/global.md"))

    # A SUPERSEDED FACT IS NOT THE ANSWER. Red-proven by dropping the valid_to
    # clause: the SQL then matches a fact that has been replaced, which is the
    # single thing the store exists to prevent.
    seen_sql = []

    def spy(sql, params):
        seen_sql.append((sql, params))
        return [["x"]]

    resolve("store:facts/lane/id_is_derived", spy)
    want("resolving a fact asks only for the CURRENT one",
         "valid_to is null" in seen_sql[0][0])
    want("and it passes the key parts as PARAMETERS, never spliced into SQL",
         seen_sql[0][1] == ("lane", "id_is_derived"))
    resolve("store:queries/facts", spy)
    want("a table without validity is not given a valid_to clause",
         "valid_to" not in seen_sql[1][0])

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "docs").mkdir()
        (root / "docs" / "a.md").write_text(
            "see store:facts/lane/id_is_derived for the rule\n"
            "and store:queries/facts answers it\n", encoding="utf-8")
        (root / "docs" / "b.toon").write_text(
            "ref: store:commands/git-any\n", encoding="utf-8")
        (root / "docs" / "skip.png").write_text("store:facts/x/y\n",
                                                encoding="utf-8")
        found = {p for _, _, p in scan(root)}
        want("every pointer in prose is found, across file types",
             found == {"store:facts/lane/id_is_derived", "store:queries/facts",
                       "store:commands/git-any"})
        want("a binary-ish file is not scanned, so it cannot invent a finding",
             "store:facts/x/y" not in found)

        # THE WHOLE POINT: a pointer at nothing is a FINDING, not silence.
        # Red-proven by having check() ignore an empty result: the rotten
        # pointer then reads exactly like a working one.
        count, problems = check(root, lambda sql, params: [])
        want("a pointer that resolves to nothing is reported as rotten",
             count == 3 and all("DANGLES" in p for p in problems))
        count, problems = check(root, lambda sql, params: [["x"]])
        want("and pointers that resolve are silent", count == 0 and not problems)
        want("the report names the file and line, so it is actionable",
             any("docs/a.md:1" in p for p in
                 check(root, lambda sql, params: [])[1]))

        # AN UNDECLARED TABLE IN PROSE is a finding too, with its reason, not a
        # crash and not a dangle.
        (root / "docs" / "c.md").write_text("store:nosuchtable/x\n",
                                            encoding="utf-8")
        count, problems = check(root, lambda sql, params: [["x"]])
        want("a pointer at an undeclared table is a finding with its reason",
             count == 1 and any("no table" in p for p in problems))

        # FILE LINKS. A markdown link to a missing file is a DEFECT; a
        # wikilink to a memory file that does not exist yet is DELIBERATELY
        # allowed by the repository convention. Red-proven by treating
        # both the same: the check then goes red on every forward reference and
        # the next session learns to ignore it.
        (root / "memory").mkdir()
        (root / "memory" / "real-note.md").write_text("here\n", encoding="utf-8")
        (root / "index.md").write_text(
            "- [Real](memory/real-note.md) fine\n"
            "- [Gone](memory/vanished.md) broken\n"
            "- see [[real-note]] and [[not-written-yet]]\n"
            "- [external](https://example.com/thing.md) untouched\n",
            encoding="utf-8")
        broken, todo = check_files(root)
        want("a markdown link to a missing file is reported broken",
             len(broken) == 1 and "memory/vanished.md" in broken[0])
        want("a link to a file that exists is silent",
             not any("real-note" in b for b in broken))
        want("a wikilink with no file yet is a NOTE, never a failure",
             len(todo) == 1 and "not-written-yet" in todo[0])
        want("a wikilink that resolves is silent",
             not any("[[real-note]]" in t for t in todo))
        want("an http link is left alone, because it cannot be resolved here",
             not any("example.com" in b for b in broken))
        want("the report names file and line for a broken link",
             "index.md:2" in broken[0])

    if fails:
        print(f"\nptr self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nptr self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
