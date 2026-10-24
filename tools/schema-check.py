#!/usr/bin/env python3
"""schema-check.py: is the live store reproducible from `db/`? Four ways it is not.

    python tools/schema-check.py            compare db/ against the live store
    python tools/schema-check.py --self-test

WHY THIS EXISTS, measured 2026-09-16 while moving the framework's knowledge into
the store. Every one of the four faults below was live at the same moment, and
not one of them was visible:

  A  A LIVE TABLE NO MIGRATION CREATES. `commands` and its `queries.args`
     column existed in the store, recorded as schema_version 6, and `db/` had
     no file for them. The schema had been changed by an ad-hoc statement, so
     the store was NOT rebuildable from this repo and a pruned volume would
     have taken it.
  B  A LIVE STORED QUERY NO MIGRATION CREATES. Same shape, for `queries` rows,
     which are DATA that behaves like schema (golden rule A6: the question is
     stored, not remembered).
  C  A MIGRATION THAT WAS NEVER APPLIED. `db/006_sky_knowledge.sql` had sat
     unapplied for nine days: the `sky_knowledge` table did not exist and
     neither did its three stored questions, so `sky_open`, `sky_cost` and
     `sky_tenants` answered nothing for anyone who called them.
  D  TWO FILES CLAIMING ONE VERSION NUMBER. That is what hid C: the sky file
     held number 6, `schema_version` had a row 6 with a completely different
     note, and a reader matching file to version saw a 6 and stopped looking.

THE REASON IT WAS INVISIBLE is this repo's most-broken rule: an applied
migration and a missing one looked identical, so silence and success looked
alike (`rules/shared/memory-integrity.md` rule 2). Nothing compared the two
sides, because there was no side to compare against, because nobody had written
this.

"COULD NOT ASK" EXITS 3, never 0 and never 1. An unreachable store is UNCHECKED,
not clean; reporting drift or cleanliness from a failed connection is the same
fault in a different coat.

THE COVERAGE TEST IS DELIBERATELY COARSE and errs toward missing a finding
rather than inventing one: a live name counts as covered when a `db/*.sql` file
creates it by name. It cannot notice a column added to a table some migration
does create. That is the next thing to tighten, and it is written down here
rather than left as an assumption.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DB = REPO / "db"

MIGRATION = re.compile(r"^(\d+)_.+\.sql$")
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)",
                          re.IGNORECASE)
CREATE_VIEW = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([a-z_]+)",
                         re.IGNORECASE)


def _memdb():
    spec = importlib.util.spec_from_file_location("memdb", HERE / "memdb.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# A MIGRATION MAY DECLARE ITSELF A DELIBERATE NO-OP by carrying this marker, and
# then having no `schema_version` row is correct rather than a finding.
# `db/003_heartbeat.sql` is the case: golden rule A7 retired the heartbeat, the
# file applies nothing, and it is kept only so the numbered sequence and the
# self-tests that name it keep resolving. THE FILE DECLARES IT, never a filename
# list in here (`docs/scoped-by-declaration.md`), so renaming the file cannot
# turn a deliberate no-op back into an alarm.
NO_OP_MARKER = "schema-check: no-op"


def migrations(db: Path) -> dict[int, list[Path]]:
    """{version: files claiming it}. A list, so a collision is representable.

    A dict keyed by version would SILENTLY DROP one side of a collision, which
    is the very fault D exists to catch.
    """
    out: dict[int, list[Path]] = {}
    if not db.is_dir():
        return out
    for path in sorted(db.glob("*.sql")):
        m = MIGRATION.match(path.name)
        if not m:
            continue
        out.setdefault(int(m.group(1)), []).append(path)
    return out


def declared(db: Path) -> tuple[set[str], set[str], str]:
    """(table names, view names, all migration text) created by `db/`."""
    tables: set[str] = set()
    views: set[str] = set()
    blob = []
    for paths in migrations(db).values():
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            blob.append(text)
            tables.update(m.lower() for m in CREATE_TABLE.findall(text))
            views.update(m.lower() for m in CREATE_VIEW.findall(text))
    return tables, views, "\n".join(blob)


def audit(db: Path, live_tables: set[str], live_views: set[str],
          live_queries: set[str], applied: set[int]) -> list[str]:
    """Every way the two sides disagree, as plain-words findings. Pure."""
    tables, views, blob = declared(db)
    found: list[str] = []

    for name in sorted(live_tables - tables):
        found.append(f"A  table `{name}` exists in the store and NO db/*.sql "
                     f"creates it: the store cannot be rebuilt from this repo")
    for name in sorted(live_views - views):
        found.append(f"A  view `{name}` exists in the store and NO db/*.sql "
                     f"creates it: the store cannot be rebuilt from this repo")
    for name in sorted(live_queries):
        if f"'{name}'" not in blob:
            found.append(f"B  stored question `{name}` exists in the store and "
                         f"NO db/*.sql creates it: it is data nothing can "
                         f"restore")
    for version, paths in sorted(migrations(db).items()):
        names = ", ".join(p.name for p in paths)
        if len(paths) > 1:
            found.append(f"D  version {version} is claimed by {len(paths)} "
                         f"files ({names}): a reader matching file to version "
                         f"stops at the first one")
        no_ops = [p for p in paths
                  if NO_OP_MARKER in p.read_text(encoding="utf-8",
                                                 errors="replace")]
        if len(no_ops) == len(paths):
            continue
        if version not in applied:
            found.append(f"C  {names} was never applied: no schema_version row "
                         f"{version}, so the file is a plan and the store does "
                         f"not have it")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="schema-check")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    memdb = _memdb()
    try:
        rows = memdb._query(
            "select table_name, table_type from information_schema.tables "
            "where table_schema = 'public'", ())
        qrows = memdb._query("select name from queries", ())
        vrows = memdb._query("select version from schema_version", ())
    except Exception as exc:
        print(f"schema-check: could NOT ask the store ({exc}). Nothing was "
              f"compared, which is UNCHECKED, not clean.", file=sys.stderr)
        return 3

    live_tables = {str(r[0]) for r in rows if str(r[1]) == "BASE TABLE"}
    live_views = {str(r[0]) for r in rows if str(r[1]) == "VIEW"}
    live_queries = {str(r[0]) for r in qrows}
    appliedv = {int(r[0]) for r in vrows}

    found = audit(Path(a.db), live_tables, live_views, live_queries, appliedv)
    if found:
        print("schema-check: db/ and the live store DISAGREE\n")
        for line in found:
            print(f"  {line}")
        print(f"\n{len(found)} disagreement(s). A store that cannot be rebuilt "
              f"from this repo is one pruned volume away from gone.")
        return 1
    print(f"schema-check: {len(live_tables)} table(s), {len(live_views)} "
          f"view(s) and {len(live_queries)} stored question(s) all trace to a "
          f"db/ migration, and every migration is applied")
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

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp)
        (db / "001_initial.sql").write_text(
            "CREATE TABLE IF NOT EXISTS jobs (id text);\n"
            "CREATE VIEW v_cards AS SELECT 1;\n"
            "INSERT INTO queries (name) VALUES ('cards');\n", encoding="utf-8")

        # THE HAPPY PATH FIRST, so a later red is known to mean something.
        found = audit(db, {"jobs"}, {"v_cards"}, {"cards"}, {1})
        want("a store that matches db/ exactly reports nothing", found == [])

        # A: THE 2026-09-16 FAULT. Red-proven by dropping the live-minus-declared
        # difference: `commands` then traces to nothing and nobody is told.
        found = audit(db, {"jobs", "commands"}, {"v_cards"}, {"cards"}, {1})
        want("a live table no migration creates is finding A",
             len(found) == 1 and found[0].startswith("A") and "commands" in found[0])

        found = audit(db, {"jobs"}, {"v_cards", "v_running"}, {"cards"}, {1})
        want("a live VIEW no migration creates is finding A too",
             len(found) == 1 and "v_running" in found[0])

        # B: a stored question is DATA that behaves like schema.
        found = audit(db, {"jobs"}, {"v_cards"}, {"cards", "commands_for"}, {1})
        want("a live stored question no migration creates is finding B",
             len(found) == 1 and found[0].startswith("B")
             and "commands_for" in found[0])

        # C: the sky migration's exact state for nine days.
        (db / "006_sky_knowledge.sql").write_text(
            "CREATE TABLE IF NOT EXISTS sky_knowledge (id bigserial);\n"
            "INSERT INTO queries (name) VALUES ('sky_open');\n",
            encoding="utf-8")
        found = audit(db, {"jobs"}, {"v_cards"}, {"cards"}, {1})
        want("a migration with no schema_version row is finding C",
             any(f.startswith("C") and "006_sky" in f for f in found))
        want("and an unapplied migration's table is NOT also reported as "
             "missing from the store, which would be the same fault twice",
             not any(f.startswith("A") for f in found))

        # D: two files, one number. This is what hid C.
        (db / "006_commands.sql").write_text(
            "CREATE TABLE IF NOT EXISTS commands (id text);\n",
            encoding="utf-8")
        found = audit(db, {"jobs"}, {"v_cards"}, {"cards"}, {1})
        want("two files claiming one version is finding D",
             any(f.startswith("D") and "2 files" in f for f in found))
        want("and the collision names BOTH files, so it is actionable",
             any("006_commands.sql" in f and "006_sky_knowledge.sql" in f
                 for f in found if f.startswith("D")))

        # A DECLARED NO-OP IS NOT A FINDING. Red-proven by dropping the marker
        # check: `db/003_heartbeat.sql`, which golden rule A7 retired and which
        # must NEVER be applied, comes back as a C every session, and an alarm
        # nothing can clear is an alarm that gets read past.
        (db / "003_heartbeat.sql").write_text(
            "-- schema-check: no-op\n-- applies nothing, A7 retired it\n",
            encoding="utf-8")
        found = audit(db, {"jobs"}, {"v_cards"}, {"cards"}, {1})
        want("a migration that declares itself a no-op is not reported unapplied",
             not any("003_heartbeat" in f for f in found))
        want("and a file WITHOUT the marker still is",
             any("006_sky" in f for f in found))

        # A MISSING db/ DIRECTORY MUST NOT READ AS CLEAN: with nothing declared,
        # every live object is unreproducible and that is the honest answer.
        found = audit(Path(tmp) / "nope", {"jobs"}, set(), set(), set())
        want("a missing db/ directory does not read as clean",
             any(f.startswith("A") for f in found))

    if fails:
        print(f"\nschema-check self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nschema-check self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
