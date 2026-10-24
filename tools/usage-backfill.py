#!/usr/bin/env python3
"""Read session costs from opencode's SQLite database.

By default the tool checks the standard per-user database locations under the
home directory and APPDATA. Use --db to select another database explicitly.
The database is opened read-only; schema and columns are discovered at runtime.

WHY THIS EXISTS (2026-09-01: "backfill accurate usage"). Cost reports built
from formatted CLI output can silently drift. Reading the SQLite source avoids
parsing presentation text and makes an unrecognized schema an explicit error.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sqlite3
import sys

DB_CANDIDATES = (
    pathlib.Path.home() / ".local" / "share" / "opencode" / "opencode.db",
    pathlib.Path(os.environ.get("APPDATA", str(pathlib.Path.home()))) / "opencode" / "opencode.db",
)
COST_HINTS = ("cost", "total_cost", "cost_usd")
TIME_HINTS = ("created", "created_at", "time_created", "timestamp", "ts")
MODEL_HINTS = ("model", "model_id", "modelid")


def find_db(explicit: str | None) -> pathlib.Path | None:
    if explicit:
        path = pathlib.Path(explicit).expanduser()
        return path if path.is_file() else None
    return next((candidate for candidate in DB_CANDIDATES if candidate.is_file()), None)


def connect_ro(db: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)


def tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "select name from sqlite_master where type='table' order by name")]


def columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'pragma table_info("{table}")')]


def pick(names: list[str], hints: tuple[str, ...]) -> str | None:
    lowered = {name.lower(): name for name in names}
    return next((lowered[hint.lower()] for hint in hints if hint.lower() in lowered), None)


def cost_table(connection: sqlite3.Connection) -> tuple[str, dict[str, str]] | None:
    best = None
    for table in tables(connection):
        names = columns(connection, table)
        cost = pick(names, COST_HINTS)
        if not cost:
            continue
        resolved = {"cost": cost, "time": pick(names, TIME_HINTS) or "",
                    "model": pick(names, MODEL_HINTS) or ""}
        score = bool(resolved["model"]) * 2 + bool(resolved["time"])
        if best is None or score > best[0]:
            best = (score, table, resolved)
    return None if best is None else (best[1], best[2])


def epoch_scale(max_seen: float) -> float | None:
    for scale in (1.0, 1e3, 1e6):
        try:
            year = datetime.datetime.fromtimestamp(
                max_seen / scale, datetime.timezone.utc).year
        except (ValueError, OverflowError, OSError):
            continue
        if 2020 <= year <= 2100:
            return scale
    return None


def spend_since(connection: sqlite3.Connection, iso: str) -> dict | None:
    found = cost_table(connection)
    if found is None:
        return None
    table, resolved = found
    if not resolved["time"]:
        return None
    max_row = connection.execute(
        f'select max("{resolved["time"]}") from "{table}"').fetchone()
    if not max_row or max_row[0] is None:
        return None
    scale = epoch_scale(float(max_row[0]))
    if scale is None:
        return None
    cut = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * scale
    total, rows, priced, unpriced = connection.execute(
        f'select coalesce(sum("{resolved["cost"]}"), 0), count(*), '
        f'sum(case when coalesce("{resolved["cost"]}", 0) > 0 then 1 else 0 end), '
        f'sum(case when coalesce("{resolved["cost"]}", 0) = 0 then 1 else 0 end) '
        f'from "{table}" where "{resolved["time"]}" > ?', (cut,)).fetchone()
    result = {"since": iso, "table": table, "cost_column": resolved["cost"],
              "time_column": resolved["time"], "total_cost": float(total),
              "rows": int(rows), "priced": int(priced or 0), "unpriced": int(unpriced or 0)}
    by_time = {}
    for name in columns(connection, table):
        if name.lower().startswith("time"):
            amount, count = connection.execute(
                f'select coalesce(sum("{resolved["cost"]}"), 0), count(*) '
                f'from "{table}" where "{name}" > ?', (cut,)).fetchone()
            by_time[name] = {"total_cost": float(amount), "rows": int(count)}
    result["by_time_column"] = by_time

    token_columns = [name for name in columns(connection, table)
                     if name.lower().startswith("tokens")]
    model_column = resolved["model"]
    if not token_columns or not model_column:
        return result
    token_sum = " + ".join(f'coalesce("{name}", 0)' for name in token_columns)
    rates = {}
    for model, cost, count in connection.execute(
            f'select "{model_column}", sum("{resolved["cost"]}"), sum({token_sum}) '
            f'from "{table}" where coalesce("{resolved["cost"]}", 0) > 0 '
            f'group by "{model_column}"').fetchall():
        if count:
            rates[model] = float(cost) / float(count)
    missing = 0.0
    details = []
    for model, count, _ in connection.execute(
            f'select "{model_column}", {token_sum}, "{resolved["time"]}" from "{table}" '
            f'where "{resolved["time"]}" > ? and coalesce("{resolved["cost"]}", 0) = 0',
            (cut,)).fetchall():
        estimate = rates.get(model, 0.0) * float(count or 0)
        missing += estimate
        details.append({"model": model, "tokens": int(count or 0),
                        "estimated_cost": estimate, "priced_rate_known": model in rates})
    result["unpriced_estimate"] = missing
    result["unpriced_detail"] = details
    return result


def session_costs(connection: sqlite3.Connection) -> dict[str, float] | None:
    best = None
    for table in tables(connection):
        names = columns(connection, table)
        cost = pick(names, COST_HINTS)
        identifier = pick(names, ("id", "session_id", "sessionid", "session", "sid"))
        if not cost or not identifier:
            continue
        score = (2 if "session" in table.lower() else 0) + bool(pick(names, MODEL_HINTS))
        if best is None or score > best[0]:
            best = (score, table, identifier, cost)
    if best is None:
        return None
    _, table, identifier, cost = best
    return {str(key): amount for key, amount in connection.execute(
        f'select "{identifier}", coalesce("{cost}", 0) from "{table}" '
        f'where "{identifier}" is not null and "{identifier}" != ""')}


def report(connection: sqlite3.Connection) -> int:
    found = cost_table(connection)
    if found is None:
        print("usage-backfill: no table in this database carries a cost column", file=sys.stderr)
        print("usage-backfill: inspect --schema; do not assume cost is zero", file=sys.stderr)
        return 2
    table, resolved = found
    print(f"source table: {table} (cost={resolved['cost']}, model={resolved['model'] or 'NONE'}, "
          f"time={resolved['time'] or 'NONE'})")
    total, count = connection.execute(
        f'select coalesce(sum("{resolved["cost"]}"), 0), count(*) from "{table}"').fetchone()
    print(f"rows: {count}   total cost: ${total:,.4f}")
    if not resolved["model"]:
        print("no model column, per-model attribution is unavailable")
        return 0
    print(f"\n{'model':<44} {'runs':>7} {'cost':>12} {'mean':>10}")
    for model, runs, cost in connection.execute(
            f'select "{resolved["model"]}", count(*), coalesce(sum("{resolved["cost"]}"), 0) '
            f'from "{table}" group by 1 order by 3 desc'):
        mean = cost / runs if runs else 0
        print(f"{(model or '(null)'):<44} {runs:>7} {cost:>12,.4f} {mean:>10,.5f}")
    return 0


def emit_schema(connection: sqlite3.Connection) -> int:
    for table in tables(connection):
        print(f"{table} -> {','.join(columns(connection, table))}")
    return 0


def self_test() -> int:
    import tempfile

    failures: list[str] = []
    checks = 0

    def want(condition: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as temp:
        db = pathlib.Path(temp) / "fixture.db"
        connection = sqlite3.connect(db)
        connection.execute("create table message (id text, model text, cost real, created integer)")
        connection.executemany("insert into message values (?,?,?,?)", [
            ("a", "model-a", 0.05, 1), ("b", "model-a", 0.15, 2),
            ("c", "model-b", 0.0, 3),
        ])
        connection.execute("create table session (id text, title text)")
        connection.commit()
        connection.close()
        readonly = connect_ro(db)
        found = cost_table(readonly)
        want("tables are discovered", "message" in tables(readonly))
        want("a cost table is found", found is not None)
        want("a decoy without cost is ignored", found is not None and found[0] == "message")
        want("model and time columns resolve", found is not None and
             found[1]["model"] == "model" and found[1]["time"] == "created")
        want("session costs are read", session_costs(readonly) == {"a": 0.05, "b": 0.15, "c": 0.0})
        readonly.close()
        no_cost = pathlib.Path(temp) / "without-cost.db"
        other = sqlite3.connect(no_cost)
        other.execute("create table session (id text, title text)")
        other.commit()
        other.close()
        readonly = connect_ro(no_cost)
        want("no cost table returns None, not zero", cost_table(readonly) is None)
        want("no session costs returns None, not an empty result", session_costs(readonly) is None)
        readonly.close()
    print(f"usage-backfill self-test: {checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="database path; defaults to the platform user location")
    parser.add_argument("--schema", action="store_true", help="print discovered tables and columns")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--since", metavar="ISO", help="summarize spend after this timestamp")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    db = find_db(args.db)
    if db is None:
        print("usage-backfill: database not found; tried:", file=sys.stderr)
        for candidate in DB_CANDIDATES:
            print(f"  {candidate}", file=sys.stderr)
        print("usage-backfill: this is not evidence that spend is zero", file=sys.stderr)
        return 2
    connection = connect_ro(db)
    try:
        if args.schema:
            return emit_schema(connection)
        if args.since:
            try:
                result = spend_since(connection, args.since)
            except ValueError as exc:
                print(f"usage-backfill: invalid ISO timestamp: {exc}", file=sys.stderr)
                return 2
            if result is None:
                print("usage-backfill: database cannot date its spend; this is not zero", file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps(result))
                return 0
            print(f"spend since {result['since']}: ${result['total_cost']:.4f} across {result['rows']} rows")
            print(f"{result['priced']} rows have cost, {result['unpriced']} rows have no cost")
            for name, values in sorted(result.get("by_time_column", {}).items()):
                print(f"dated by {name}: ${values['total_cost']:.4f} across {values['rows']} rows")
            if result["unpriced"]:
                print("WARNING: unpriced rows mean the recorded total is not a full spend reading")
            for detail in result.get("unpriced_detail", []):
                label = "same-model estimate" if detail["priced_rate_known"] else "no priced sample; zero is a floor"
                print(f"unpriced: {detail['model']} {detail['tokens']} tokens, "
                      f"estimated ${detail['estimated_cost']:.4f} ({label})")
            return 0
        if args.json:
            found = cost_table(connection)
            if found is None:
                print(json.dumps({"db": str(db), "cost_table": None}))
                return 2
            table, resolved = found
            total, rows = connection.execute(
                f'select coalesce(sum("{resolved["cost"]}"), 0), count(*) from "{table}"').fetchone()
            print(json.dumps({"db": str(db), "cost_table": table,
                              "rows": rows, "total_cost": total}))
            return 0
        return report(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
