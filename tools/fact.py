#!/usr/bin/env python3
"""Store one fact in the shared store, superseding an old one only when told.

    python tools/fact.py add <subject> <key> --value "<text>" --source "<who, when>"
    python tools/fact.py add <subject> <key> --value "..." --source "..." --supersede
    python tools/fact.py add <subject> <key> --pointer store:facts/<s>/<k> --source "..."
    python tools/fact.py --self-test

WHY (A3, 2026-10-03). Facts are the store's settled answers: `memdb.py facts`
lists them and every session brief prints them. One session wrote three as
hand-typed INSERT files, and a hand INSERT cannot see that its key already
holds a live fact. Two live rows for one key is two answers to one question,
the drift the store exists to end.

So a key that already holds a live fact is REFUSED unless --supersede says so,
and then the old row is closed (valid_to) and the new one inserted in ONE
statement: there is never a moment with no answer, or with two.

Exit: 0 stored, 1 the store refused the statement, 2 refused here with nothing
written, 3 the store could not be reached.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import machinelib  # noqa: E402
import memdb  # noqa: E402

SUBJECT = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,120}$")

LIVE = ("SELECT count(*) FROM facts "
        "WHERE subject = %s AND key = %s AND valid_to IS NULL")
WRITE = ("WITH closed AS (UPDATE facts SET valid_to = now() "
         "WHERE subject = %s AND key = %s AND valid_to IS NULL RETURNING id) "
         "INSERT INTO facts (subject, key, value, value_kind, source, machine) "
         "VALUES (%s, %s, %s, %s, %s, %s) "
         "RETURNING id, (SELECT count(*) FROM closed)")


def _refuse(why: str) -> int:
    print(f"fact: REFUSED, {why}. Nothing was written.")
    return 2


def add(subject: str, key: str, value: str, source: str, kind: str = "scalar",
        supersede: bool = False, machine: str | None = None, run=None) -> int:
    run = run or memdb._query
    if not SUBJECT.match(subject):
        return _refuse(f"a subject is lowercase words and dashes, not {subject!r}")
    if not KEY.match(key):
        return _refuse(f"a key is lowercase words and underscores, not {key!r}")
    if not value.strip():
        return _refuse("a fact needs a value")
    if not source.strip():
        return _refuse("a fact needs a source: who said it, or what measured it, and when")
    if kind == "pointer" and not value.startswith("store:"):
        return _refuse("a pointer is a store: address, like store:facts/<subject>/<key>")
    try:
        live = run(LIVE, (subject, key))[0][0]
        if live and not supersede:
            return _refuse(f"{subject}/{key} already holds a live fact; read it with "
                           f"tools/ptr.py store:facts/{subject}/{key}, then pass "
                           "--supersede to replace it")
        rows = run(WRITE, (subject, key, subject, key, value, kind, source,
                           machine or machinelib.machine_name()), write=True)
    except (memdb.CouldNotAsk, memdb.DriverMissing) as exc:
        print(f"fact: could not reach the store ({exc}). Nothing was written.")
        return 3
    except Exception as exc:  # the server refused the statement
        print(f"fact: the store refused it: {type(exc).__name__}: {exc}")
        return 1
    closed = int(rows[0][1]) if rows else 0
    print(f"fact: stored store:facts/{subject}/{key}"
          + (f", closing {closed} older row(s)" if closed else ""))
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

    class FakeStore:
        """The two statements this tool sends, answered the way Postgres would."""

        def __init__(self) -> None:
            self.rows: list[dict] = []
            self.calls = 0

        def __call__(self, sql: str, params: tuple, write: bool = False) -> list[list]:
            self.calls += 1
            if sql == LIVE:
                s, k = params
                return [[sum(1 for r in self.rows if (r["s"], r["k"]) == (s, k) and r["live"])]]
            if sql == WRITE:
                s, k, s2, k2, value, kind, source, machine = params
                closed = 0
                for r in self.rows:
                    if (r["s"], r["k"]) == (s, k) and r["live"]:
                        r["live"] = False
                        closed += 1
                self.rows.append({"s": s2, "k": k2, "v": value, "kind": kind, "live": True})
                return [[len(self.rows), closed]]
            raise AssertionError(f"unexpected statement: {sql}")

        def live(self, s: str, k: str) -> list[dict]:
            return [r for r in self.rows if (r["s"], r["k"]) == (s, k) and r["live"]]

    store = FakeStore()
    ok = dict(source="a self-test, 2026-10-03", machine="pc", run=store)
    want("a bad subject is refused before the store is asked",
         add("Farm App", "x", "v", **ok) == 2 and store.calls == 0)
    want("a bad key is refused", add("farm-app", "Has Spaces", "v", **ok) == 2)
    want("an empty value is refused", add("farm-app", "x", "  ", **ok) == 2)
    want("a fact with no source is refused",
         add("farm-app", "x", "v", source=" ", machine="pc", run=store) == 2)
    want("a pointer that is not a store address is refused",
         add("farm-app", "x", "docs/a.md", kind="pointer", **ok) == 2)
    want("nothing reached the store from any refusal", store.calls == 0)

    want("a new fact is stored", add("farm-app", "sql_quotes", "quote them", **ok) == 0
         and len(store.live("farm-app", "sql_quotes")) == 1)
    want("a second fact for a live key is REFUSED without --supersede",
         add("farm-app", "sql_quotes", "other", **ok) == 2)
    want("and the live answer is untouched",
         [r["v"] for r in store.live("farm-app", "sql_quotes")] == ["quote them"])
    want("with --supersede the old row closes and the new one is the only live answer",
         add("farm-app", "sql_quotes", "quote every identifier", supersede=True, **ok) == 0
         and [r["v"] for r in store.live("farm-app", "sql_quotes")] == ["quote every identifier"]
         and len(store.rows) == 2)

    def unreachable(sql: str, params: tuple, write: bool = False) -> list[list]:
        raise memdb.CouldNotAsk("no route to the store")

    want("an unreachable store exits 3, never a quiet success",
         add("farm-app", "y", "v", source="t", machine="pc", run=unreachable) == 3)

    print("\nfact self-test: %d/%d checks passed" % (checks[1], checks[0]))
    return 0 if checks[1] == checks[0] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fact")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("verb", nargs="?", choices=("add",))
    ap.add_argument("subject", nargs="?")
    ap.add_argument("key", nargs="?")
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--value", help="the fact, in words")
    what.add_argument("--pointer", help="a store: address this fact points at")
    ap.add_argument("--source", default="", help="who said it, or what measured it, and when")
    ap.add_argument("--supersede", action="store_true",
                    help="replace the live fact this key already holds")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.verb != "add" or not a.subject or not a.key or (a.value is None and a.pointer is None):
        ap.error("add <subject> <key> --value TEXT (or --pointer store:...) --source TEXT")
    if a.pointer is not None:
        return add(a.subject, a.key, a.pointer, a.source, kind="pointer", supersede=a.supersede)
    return add(a.subject, a.key, a.value, a.source, supersede=a.supersede)


if __name__ == "__main__":
    raise SystemExit(main())
