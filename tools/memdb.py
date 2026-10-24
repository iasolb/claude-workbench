#!/usr/bin/env python3
"""memdb.py: ask the shared memory database a question BY NAME.

    python tools/memdb.py --list             # every question it can answer
    python tools/memdb.py running            # run the stored query called `running`
    python tools/memdb.py commands_for --args pc,<owner>/ExampleProject
    python tools/memdb.py --sql "SELECT 1"   # ad hoc, for developing a new one
    python tools/memdb.py --sql-file <path>  # a statement too big or too quoted
                                             # to survive a shell argument
    python tools/memdb.py --self-test

WHY BY NAME (golden rule A5): a question asked twice becomes a command that
answers it accurately every time. The SQL lives in the `queries` TABLE, not in
this file and not in a session's memory, so the phrasing cannot drift between
sessions and nobody re-derives it. Adding a question is an INSERT, not an edit
here.

PARAMETERS: A STORED QUERY THAT CANNOT TAKE ARGUMENTS CAN ONLY ANSWER ONE
QUESTION. `queries.args` names the parameters in order, comma separated
("machine,repo"), and is NULL for a query that takes none. --args supplies them
positionally, also comma separated. The count is CHECKED before the query runs:
too few or too many is exit 2 naming what the query declared, never a query run
with the wrong shape. A query declaring no parameters refuses --args, and a
query declaring parameters refuses to run without them, because a filter
silently dropped is a wrong answer that looks like a right one.

    KNOWN LIMIT: --args splits on commas, so a VALUE containing a comma cannot
    be passed. It fails loudly on the arity check rather than silently, unless
    the split happens to produce the declared count.

A SHELL ARGUMENT IS NOT A SAFE PLACE FOR SQL, which is why --sql-file exists.
Measured 2026-09-13: a backtick inside a double-quoted --sql argument was
EXECUTED by bash, the span vanished from the statement, and the row committed
with two field names missing and exit 0 (docs/command-forms.md, the Never
list). Anything with quoting in it, and every schema change, goes in a file.

WHERE IT POINTS. The SHARED database is the only default source and its address
lives in MEMDB_URL. When MEMDB_URL is not set the tool refuses to guess and
exits 3: a local answer may never stand in for the shared one, because nothing
in the rows would say which database they came from. --local is the loud,
deliberate opt-in for developing a query against the local ai-memory-db
container before it goes to the shared box.

EVERY ANSWER NAMES ITS SOURCE. Each result is preceded on stderr by the host
and database that produced it, so a reader never has to guess which database a
row came from.

COULD-NOT-ASK IS NOT NOTHING-THERE. If the database cannot be reached this exits
3 and says so, and it NEVER prints an empty result in that case. That is decision
9: a session stops and says it could not look, rather than falling back to a
local answer that looks identical to a real one.

THE DRIVER. Postgres is reached over pg8000, a pure-Python driver, instead of
shelling out to the psql binary. psql is installed on neither machine, and
reading its printed table was one tool scraping another tool's prose
(rules/shared/memory-integrity.md, rule 2): a column containing a tab, a NULL
or a wrapped line changed the parse. pg8000 needs no compiler and no libpq, so
it installs cleanly on both machines. psycopg[binary] was the alternative and
was not chosen because its wheels bundle libpq per platform. Rows come back as
typed values, never as parsed text. Install pg8000 with:

    python -m pip install pg8000

If the driver is missing, the command is built from the active interpreter:

    <active-python> -m pip install pg8000

If the driver is missing this tool says so with that command and exits 3. It
never falls back to psql, and never falls back to the local container.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import pg8000
    from pg8000.exceptions import DatabaseError, InterfaceError
except ImportError:
    pg8000 = None
    DatabaseError = InterfaceError = Exception

CONTAINER = "ai-memory-db"
LOCAL_HOST = "localhost"
LOCAL_PORT_FALLBACK = 5432
DB_USER = "aimem"
DB_NAME = "aimem"


def local_port() -> int:
    """ASK DOCKER which host port the container is on. Never assume 5432.

    Measured 2026-09-07: the container publishes 5432 on host port 55432, and
    something ELSE was listening on 5432. The hardcoded constant therefore
    connected successfully to the wrong server and returned
    `role "aimem" does not exist`, which reads exactly like a broken container
    rather than a wrong address. A plausible answer from the wrong source is
    the failure this repo keeps paying for
    (`rules/shared/memory-integrity.md` rule 2).
    """
    for docker in ("/opt/homebrew/bin/docker", "/usr/bin/docker", "docker"):
        try:
            out = subprocess.run(
                [docker, "port", CONTAINER, "5432/tcp"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        for line in out.stdout.splitlines():
            _, _, hostport = line.strip().rpartition(":")
            if hostport.isdigit():
                return int(hostport)
    return LOCAL_PORT_FALLBACK

UNREACHABLE = 3

_LOCAL = False


class DriverMissing(Exception):
    """The pg8000 driver is not installed."""


class CouldNotAsk(Exception):
    """The database could not be reached. Never the same thing as an empty
    result set."""


class SelfTestWriteRefused(CouldNotAsk):
    """A self-test tried to WRITE to the shared store. Refused before any
    connection, so a fixture can never land in the real tables."""


# A write that does not LEAD with its verb: fact.py's supersede is
# `WITH closed AS (UPDATE facts SET ...) INSERT ...`, which is_write misses.
_WRITE_ANYWHERE = re.compile(
    r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from|truncate)\b", re.I)


def self_test_write_refusal(sql: str, write: bool = False, argv=None,
                            env=None, local: bool | None = None) -> str:
    """Why this statement may not run, or "" when it may.

    THE RULE (queue/global.md, the memory DB goal): a self-test must never be
    able to write to the shared store. It was enforced tool by tool (jobsync.py
    carries its own guard) and listed as UNPROVEN for the rest, about 17 tools
    that open the store. Every write that goes through this module is now
    checked HERE, once: a process whose argv carries --self-test may write only
    to a loopback store (a local or throwaway container), never to the shared
    one. A tool that opens its own connection is outside this guard.
    """
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    local = _LOCAL if local is None else local
    if "--self-test" not in argv or local:
        return ""
    if not (write or is_write(sql) or _WRITE_ANYWHERE.search(sql)):
        return ""
    host = (urlparse(env.get("MEMDB_URL", "")).hostname or "").lower()
    if not host or host in ("localhost", "::1") or host.startswith("127."):
        return ""
    return f"a self-test may never write to the shared store at {host}"


class NoSuchQuestion(Exception):
    """There is no stored query by that name, and the store WAS reachable, so
    this is a wrong name rather than a wrong answer."""


class ArgMismatch(Exception):
    """The parameters supplied do not match the ones the query declares. Its
    own class so a caller can tell a misuse from an unreachable store."""


def _driver_missing_message() -> str:
    install = f"{sys.executable} -m pip install pg8000"
    return (f"memdb: the pg8000 driver is not installed. "
            f"Install it with:\n    {install}")


def _source_label() -> str:
    """Host/database for the source this tool queries, never its credentials."""
    if _LOCAL:
        return (f"LOCAL docker container {CONTAINER} at "
                f"{LOCAL_HOST}:{local_port()}, database {DB_NAME}")
    # .get, never [...]: this runs INSIDE _query's could-not-ask handler, and
    # with MEMDB_URL unset a KeyError here replaced the honest "could not ask"
    # with a traceback (measured on a second environment, 2026-10-16).
    url = os.environ.get("MEMDB_URL", "")
    if not url:
        return "<MEMDB_URL is not set>"
    parsed = urlparse(url)
    host = parsed.hostname or "<unknown-host>"
    db = parsed.path.lstrip("/") or "<unknown-db>"
    if parsed.port:
        return f"{host}:{parsed.port}/{db}"
    return f"{host}/{db}"


def _guard_source() -> int | None:
    """Refuse to guess the source. Exit 3 unless MEMDB_URL or --local is set."""
    if _LOCAL or os.environ.get("MEMDB_URL"):
        return None
    print("memdb: MEMDB_URL is not set and --local was not passed. "
          "A local answer may never stand in for the shared database. "
          "Set MEMDB_URL, or pass --local to deliberately use the local "
          "container.", file=sys.stderr)
    return UNREACHABLE


def _connect():
    """Open a pg8000 connection to the chosen source. The shared database via
    MEMDB_URL by default; the local container only when --local is set, never
    as a silent fallback."""
    if _LOCAL:
        return pg8000.connect(user=DB_USER, host=LOCAL_HOST,
                              port=local_port(), database=DB_NAME, timeout=60)
    url = os.environ.get("MEMDB_URL")
    if not url:
        raise CouldNotAsk("MEMDB_URL is not set, so there is no source to ask")
    parsed = urlparse(url)
    return pg8000.connect(
        user=parsed.username or DB_USER,
        password=parsed.password,
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/") or DB_NAME,
        timeout=60,
    )


WRITE_VERBS = frozenset((
    "insert", "update", "delete", "truncate", "merge", "copy",
    "create", "alter", "drop", "grant", "revoke", "comment", "refresh",
    # A db/ migration opens with BEGIN; treating it as a read let one run
    # with no --write and no (COMMITTED) line (2026-09-23).
    "begin",
))


def is_write(sql: str) -> bool:
    """Does this statement obviously change the database?

    THIS IS AN INTENT GATE, NOT A SAFETY MECHANISM, and the distinction is the
    whole design. It exists so `--sql` cannot perform a write the caller did
    not ask for. It is NOT what prevents a write being lost: `_query` now
    commits unconditionally, so an unrecognised statement is committed rather
    than silently rolled back.

    That split is deliberate. An earlier version guessed the other way, "unsure
    means write", which is the safe direction only if you are guarding danger.
    Here the danger is LOSS, so the conservative direction is to commit, and a
    keyword list only needs to catch the obvious cases it is gating on.
    """
    head = sql.lstrip()
    while head.startswith("--"):
        head = head.split("\n", 1)[1].lstrip() if "\n" in head else ""
    first = (head.split(None, 1) or [""])[0].lower().lstrip("(").rstrip(";")
    return first in WRITE_VERBS


def _query(sql: str, params: tuple = (), write: bool = False) -> list[list]:
    """Run one SQL statement and return its rows as typed values.

    Raises DriverMissing when pg8000 is not installed, CouldNotAsk when the
    database cannot be reached, and DatabaseError when the server refuses the
    SQL. An empty result set is a plain [] return and never shares a value
    with a could-not-ask.
    """
    if pg8000 is None:
        raise DriverMissing()
    refusal = self_test_write_refusal(sql, write)
    if refusal:
        raise SelfTestWriteRefused(refusal)
    try:
        conn = _connect()
    except (InterfaceError, OSError) as exc:
        raise CouldNotAsk(f"{type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        # ANY failure to connect is a could-not-ask, and must exit 3 rather
        # than raise. Measured 2026-09-07: a container that wants a password
        # while no password is configured made pg8000 die inside its auth
        # handler with `AttributeError: 'NoneType' object has no attribute
        # 'decode'`, which reached the user as a nine-frame traceback. A
        # traceback is not a state a caller can branch on.
        raise CouldNotAsk(
            f"{type(exc).__name__}: {exc} (source: {_source_label()})") from exc
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            rows = ([] if cur.description is None
                    else [list(row) for row in cur.fetchall()])
            # COMMIT ALWAYS, AND THAT IS THE FIX. pg8000 opens a transaction
            # and does not autocommit, so the `conn.close()` below used to ROLL
            # BACK every statement. Measured 2026-09-10: an INSERT ... RETURNING
            # through `--sql` printed its new row id, exited 0, and persisted
            # nothing; the table still read 0 rows afterwards. A write that
            # prints a success row and discards the write is the worst shape an
            # instrument can have (`rules/shared/memory-integrity.md` rule 2).
            #
            # Committing a read is harmless: it closes an empty transaction.
            # Making this conditional on a guessed "is this a write" is what
            # reintroduces the bug, because the guess is wrong exactly when the
            # statement is unusual, which is when losing it costs most.
            conn.commit()
            return rows
        except (InterfaceError, OSError) as exc:
            raise CouldNotAsk(f"{type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()


def _cell(value) -> str:
    """One cell as text, verbatim: no escaping that could change the value."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _render(rows: list[list]) -> str:
    """Rows as lines, columns joined by a tab. Every value prints exactly what
    the database returned: a '|', a tab, or a newline inside a value survives
    intact, which the old psql-table scrape could never guarantee."""
    return "\n".join("\t".join(_cell(v) for v in row) for row in rows)


def _run_query(sql: str, params: tuple = (),
               failed: str = "the query failed",
               write: bool = False) -> tuple[int, list[list] | None]:
    """Run SQL, mapping each failure to its exit code and message. Success
    returns (0, rows); a failure returns (exit_code, None)."""
    try:
        return 0, _query(sql, params, write=write)
    except DriverMissing:
        print(_driver_missing_message(), file=sys.stderr)
        return UNREACHABLE, None
    except CouldNotAsk as exc:
        print(f"memdb: COULD NOT ASK the database. {exc}", file=sys.stderr)
        return UNREACHABLE, None
    except DatabaseError as exc:
        print(f"memdb: {failed}. {exc}", file=sys.stderr)
        return 1, None


def split_args(raw: str | None) -> tuple[str, ...]:
    """A comma-separated --args value as positional parameters. An empty or
    absent value is NO parameters, never one empty one."""
    if raw is None or raw.strip() == "":
        return ()
    return tuple(part.strip() for part in raw.split(","))


def declared_args(raw: str | None) -> tuple[str, ...]:
    """The parameter names a stored query declares, from `queries.args`."""
    return split_args(raw)


def stored_query(name: str, args: tuple[str, ...] = (),
                 local: bool = False) -> list[list]:
    """Run the stored query `name` with `args` and return typed rows.

    THE IN-PROCESS ENTRY POINT, and it exists so no other tool ever parses this
    one's printed output. A tool scraping another tool's prose is the single
    largest source of silent wrong answers in this repo
    (`rules/shared/memory-integrity.md` rule 2): a regex that misses returns an
    empty list, and the caller reports that as a calm "no commands needed".
    Callers get rows or an exception, never text.

    Raises CouldNotAsk (unreachable, or no source configured), DriverMissing,
    NoSuchQuestion, and ArgMismatch. None of those can be confused with an
    empty result set, which is a plain [].
    """
    global _LOCAL
    was_local = _LOCAL
    _LOCAL = local
    try:
        if not local and not os.environ.get("MEMDB_URL"):
            raise CouldNotAsk(
                "MEMDB_URL is not set, so there is no source to ask. A local "
                "answer may never stand in for the shared database.")
        found = _query("SELECT sql_text, args FROM queries WHERE name = %s",
                       (name,))
        if not found:
            raise NoSuchQuestion(
                f"no stored question called '{name}'. Run --list to see them.")
        sql_text, raw_declared = found[0][0], found[0][1]
        wanted = declared_args(raw_declared)
        if len(wanted) != len(args):
            raise ArgMismatch(
                f"'{name}' declares {len(wanted)} parameter(s) "
                f"({', '.join(wanted) if wanted else 'none'}) and "
                f"{len(args)} were supplied. A filter dropped in silence is a "
                f"wrong answer that looks like a right one.")
        return _query(sql_text, args)
    finally:
        _LOCAL = was_local


def main() -> int:
    ap = argparse.ArgumentParser(prog="memdb")
    ap.add_argument("name", nargs="?", help="a stored question's name")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sql", help="run ad hoc SQL instead of a stored question")
    ap.add_argument("--sql-file",
                    help="read the statement from a FILE, so quoting, "
                         "backticks and newlines never pass through a shell "
                         "argument")
    ap.add_argument("--args",
                    help="positional parameters for a stored question, comma "
                         "separated, e.g. --args pc,<owner>/ExampleProject")
    ap.add_argument("--local", action="store_true",
                    help="use the LOCAL container, loudly, instead of MEMDB_URL")
    ap.add_argument("--write", action="store_true",
                    help="COMMIT a statement that changes the database; "
                         "without it a write is refused, never silently lost")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()

    global _LOCAL
    _LOCAL = a.local

    guard = _guard_source()
    if guard is not None:
        return guard

    if a.sql and a.sql_file:
        print("memdb: --sql and --sql-file are two sources for one statement; "
              "pass exactly one.", file=sys.stderr)
        return 2

    if a.sql_file:
        try:
            a.sql = Path(a.sql_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"memdb: could not read {a.sql_file}: {exc}", file=sys.stderr)
            return 2

    if a.args is not None and not a.name:
        print("memdb: --args only applies to a stored question. Name one, or "
              "put the values in the --sql text.", file=sys.stderr)
        return 2

    if a.list:
        # The parameters a question TAKES are part of what it is, so --list
        # shows them. A caller that cannot see them guesses, and a guessed
        # arity is exactly what the check below refuses.
        rc, rows = _run_query(
            "SELECT name, question, coalesce(args, '') FROM queries "
            "ORDER BY name",
            failed="the queries table could not be read")
        if rc != 0:
            return rc
        print(f"memdb: source: {_source_label()}", file=sys.stderr)
        print(_render(rows))
        return 0

    if a.sql:
        writing = is_write(a.sql)
        if writing and not a.write:
            print("memdb: THIS STATEMENT WRITES and --write was not passed, "
                  "so nothing was run. Until 2026-09-10 a write here was "
                  "executed, printed its RETURNING row, and then ROLLED BACK "
                  "on close, so it looked exactly like a success and changed "
                  "nothing. Re-run with --write if you mean it.",
                  file=sys.stderr)
            return 2
        rc, rows = _run_query(a.sql, failed="the SQL was refused",
                              write=writing)
        if rc != 0:
            return rc
        print(f"memdb: source: {_source_label()}"
              + (" (COMMITTED)" if writing else ""), file=sys.stderr)
        print(_render(rows))
        return 0

    if not a.name:
        ap.print_help()
        return 2

    # ONE PATH for the CLI and for an in-process caller. new-order.py imports
    # stored_query directly rather than running this tool and parsing what it
    # printed, so the two can never answer differently.
    try:
        rows = stored_query(a.name, split_args(a.args), local=a.local)
    except DriverMissing:
        print(_driver_missing_message(), file=sys.stderr)
        return UNREACHABLE
    except CouldNotAsk as exc:
        print(f"memdb: COULD NOT ASK the database. {exc}", file=sys.stderr)
        return UNREACHABLE
    except NoSuchQuestion as exc:
        print(f"memdb: {exc}", file=sys.stderr)
        return 1
    except ArgMismatch as exc:
        print(f"memdb: {exc}", file=sys.stderr)
        return 2
    except DatabaseError as exc:
        print(f"memdb: the stored query failed. {exc}", file=sys.stderr)
        return 1
    print(f"memdb: source: {_source_label()}", file=sys.stderr)
    print(_render(rows))
    return 0


def _self_test() -> int:
    fails: list[str] = []

    def want(label: str, ok: bool) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            fails.append(label)

    want("unreachable has its own exit code, distinct from a missing question",
         UNREACHABLE == 3 and UNREACHABLE != 1)

    # A SELF-TEST NEVER WRITES TO THE SHARED STORE (2026-10-04). Pure, so no
    # connection is needed. Red-proven by making the function return "".
    shared = {"MEMDB_URL": "postgresql://box.example:5432/aimem"}
    st = ["tool.py", "--self-test"]
    want("a self-test's INSERT to the shared store is refused",
         self_test_write_refusal("INSERT INTO facts VALUES (1)", False, st, shared, False) != "")
    want("so is a write that leads with WITH, like fact.py's supersede",
         self_test_write_refusal("WITH c AS (UPDATE facts SET v = 1 RETURNING id) "
                                 "INSERT INTO facts SELECT 1", False, st, shared, False) != "")
    want("and a write the caller flags with write=True",
         self_test_write_refusal("CALL p()", True, st, shared, False) != "")
    want("a self-test may still READ the shared store",
         self_test_write_refusal("SELECT 1", False, st, shared, False) == "")
    want("a normal run may write to the shared store",
         self_test_write_refusal("INSERT INTO facts VALUES (1)", False,
                                 ["tool.py"], shared, False) == "")
    want("a self-test may write to a LOOPBACK store, its own throwaway",
         self_test_write_refusal("INSERT INTO facts VALUES (1)", False, st,
                                 {"MEMDB_URL": "postgresql://aimem@%s:55432/aimem"
                                  % ".".join(("127", "0", "0", "1"))},
                                 False) == "")
    want("and to the local container chosen with --local",
         self_test_write_refusal("INSERT INTO facts VALUES (1)", False, st, shared, True) == "")

    # Offline by construction: pg8000.connect is stubbed at the driver
    # boundary, so nothing needs a real database or server. The fake serves
    # canned rows and records every connection it would have made.
    global _LOCAL, pg8000
    real_pg8000 = pg8000
    real_environ = os.environ
    real_argv = sys.argv
    real_stdout, real_stderr = sys.stdout, sys.stderr

    connect_calls: list[dict] = []
    connect_exc: list[Exception] = []
    commits: list[bool] = []

    class FakeCursor:
        def __init__(self, conn: "FakeConn") -> None:
            self._conn = conn
            self.description = None
            self._rows = []

        def execute(self, sql: str, args=()) -> None:
            self._rows = self._conn._answer(sql, args)
            self.description = [("?",)] if not isinstance(self._rows, str) else None

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, **kwargs) -> None:
            connect_calls.append(dict(kwargs))
            if connect_exc:
                raise connect_exc.pop(0)

        def cursor(self) -> FakeCursor:
            return FakeCursor(self)

        def commit(self) -> None:
            # RECORDED, because "was this committed" is the assertion that
            # would have caught the 2026-09-10 silent-rollback bug. A fake
            # missing a method the real connection has cannot fail the way
            # the real one does.
            commits.append(True)

        def close(self) -> None:
            pass

        def _answer(self, sql: str, args=()):
            if "FROM queries WHERE name" in sql:
                if "running" in args:
                    return [["SELECT count(*) FROM sessions", None]]
                if "commands_for" in args:
                    # A query that DECLARES parameters, so the arity check has
                    # something real to check against.
                    return [["SELECT form FROM commands WHERE machine = %s "
                             "OR machine = %s", "machine,fallback"]]
                return []
            if "FROM queries ORDER BY name" in sql:
                return [["commands_for", "which commands apply here",
                         "machine,fallback"],
                        ["running", "what is running right now", ""],
                        ["recent", "recent sessions", ""],
                        ["lane", "the current lane", ""],
                        ["errors", "recent failures", ""],
                        ["checks", "open checks", ""],
                        ["usage", "cost this week", ""],
                        ["queue", "the global queue", ""]]
            if "FROM commands" in sql:
                return [["form-for-" + "-".join(str(a) for a in args)]]
            if "FROM sessions" in sql:
                return [[42]]
            if "roundtrip" in sql:
                return [["a|b\tc\nd"]]
            if "types" in sql:
                return [[42, 3.5, True, None]]
            if "SELECT 42" in sql:
                return [[42]]
            if "SELECT 1" in sql:
                return [[1]]
            if "empty" in sql:
                return []
            return [[7]]

    class FakeDriver:
        connect = FakeConn

    def run_main(argv: list[str], url: str | None) -> tuple[int, str]:
        if url is None:
            os.environ.pop("MEMDB_URL", None)
        else:
            os.environ["MEMDB_URL"] = url
        sys.argv = ["memdb", *argv]
        connect_calls.clear()
        buf = io.StringIO()
        sys.stdout, sys.stderr = buf, buf
        try:
            rc = main()
        finally:
            sys.stdout, sys.stderr = real_stdout, real_stderr
        return rc, buf.getvalue()

    try:
        pg8000 = FakeDriver()

        rc, out = run_main(["--sql", "SELECT 1"], None)
        want("with MEMDB_URL unset the tool exits 3", rc == 3)
        want("with MEMDB_URL unset it prints NO rows", "42" not in out and "7" not in out)
        want("and the message names MEMDB_URL as the thing that is missing",
             "MEMDB_URL" in out)
        want("the unset-source message fires before the driver message",
             "pip install" not in out)
        want("with MEMDB_URL unset no connection is ever attempted",
             len(connect_calls) == 0)

        rc, out = run_main(["--sql", "SELECT 1"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("a successful answer carries the host it queried",
             rc == 0 and "shared-host" in out)
        want("a successful answer carries the database it queried",
             rc == 0 and "aimem" in out)

        pg8000 = None
        rc, out = run_main(["--sql", "SELECT 1"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("with the driver missing the tool exits 3", rc == 3)
        want("the driver-missing message names the exact install command",
             "pip install pg8000" in out)
        want("the driver-missing message is NOT the COULD NOT ASK message",
             "COULD NOT ASK" not in out)
        want("with the driver missing no connection is attempted",
             len(connect_calls) == 0)
        pg8000 = FakeDriver()

        rc, out = run_main(["--local", "--sql", "SELECT 42"], None)
        want("--local still works", rc == 0 and "42" in out)
        want("a local answer says out loud that it is local",
             "LOCAL" in out and CONTAINER in out and "localhost" in out)
        want("a local answer never claims to be the shared source",
             "shared-host" not in out)

        _LOCAL = False
        os.environ.pop("MEMDB_URL", None)
        connect_calls.clear()
        refused = False
        try:
            _connect()
        except CouldNotAsk:
            refused = True
        want("_connect with no source refuses instead of guessing", refused)
        want("_connect refused without touching any host",
             len(connect_calls) == 0)

        rc_empty, out_empty = run_main(["--sql", "empty"],
                                       "postgres://u:p@shared-host:5432/aimem")
        want("an empty result set exits 0, not 3", rc_empty == 0)
        want("an empty result set never prints COULD NOT ASK",
             "COULD NOT ASK" not in out_empty)
        want("empty-because-empty still names its source",
             "source:" in out_empty and "shared-host" in out_empty)

        connect_exc.append(InterfaceError("connection refused"))
        rc_broken, out_broken = run_main(["--sql", "empty"],
                                         "postgres://u:p@shared-host:5432/aimem")
        want("an unreachable server exits 3 with COULD NOT ASK",
             rc_broken == 3 and "COULD NOT ASK" in out_broken)
        want("empty-because-broken and empty-because-empty never share a return value",
             rc_broken == 3 and rc_empty == 0 and rc_broken != rc_empty)
        want("the unreachable failure is distinct from the driver-missing one",
             "pip install" not in out_broken)

        rc, out = run_main(["--sql", "types"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("rows come back as typed values, not parsed text",
             rc == 0 and "42\t3.5\tTrue" in out)

        rc, out = run_main(["--sql", "roundtrip"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("a value with a pipe, a tab and a newline survives a round trip intact",
             rc == 0 and "a|b\tc\nd" in out)

        # THE 2026-09-10 SILENT-ROLLBACK REGRESSION. pg8000 does not
        # autocommit, so `conn.close()` discarded every statement while the
        # RETURNING row still printed and the exit code was still 0. Proven
        # red by removing the `conn.commit()` in `_query`, which drops this to
        # zero commits while every other assertion here stays green: the old
        # suite could not tell the two worlds apart.
        commits.clear()
        run_main(["--sql", "empty"], "postgres://u:p@shared-host:5432/aimem")
        want("every statement is COMMITTED, so nothing is silently rolled back",
             len(commits) == 1)

        # And the intent gate: a real write needs --write, and is refused
        # rather than executed-then-discarded.
        rc_nw, out_nw = run_main(["--sql", "insert into facts values (1)"],
                                 "postgres://u:p@shared-host:5432/aimem")
        want("a write without --write is REFUSED, not silently lost",
             rc_nw == 2 and "nothing was run" in out_nw)
        want("is_write knows a write from a read",
             is_write("INSERT INTO t VALUES (1)")
             and is_write("  -- c\n update t set a=1")
             and not is_write("select 1")
             and not is_write("WITH x AS (select 1) select * from x"))
        # Every db/ migration opens with BEGIN. Measured 2026-09-23: applying
        # db/015 printed no (COMMITTED) and needed no --write, so the intent
        # gate was blind to exactly the files that change the schema.
        want("a migration that opens with BEGIN is a write",
             is_write("-- header\nBEGIN;\nCREATE TABLE t (a int);\nCOMMIT;"))

        rc, out = run_main(["running"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("a stored question runs against the shared source and names it",
             rc == 0 and "42" in out and "shared-host" in out)

        rc, out = run_main(["nope"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("a missing question exits 1, not 3", rc == 1)
        want("a lookup miss re-checks the connection before blaming the name",
             "no stored question" in out)

        connect_exc.append(InterfaceError("connection refused"))
        rc, out = run_main(["nope"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("a missing question with an unreachable server is COULD NOT ASK, "
             "not 'no such question'", rc == 3 and "COULD NOT ASK" in out)

        rc, out = run_main(["--list"],
                           "postgres://u:p@shared-host:5432/aimem")
        want("--list names every stored question and its source",
             rc == 0 and "running" in out and "queue" in out
             and "source:" in out and "shared-host" in out)
        want("--list says which parameters a question takes",
             rc == 0 and "machine,fallback" in out)

        # --- PARAMETERISED STORED QUERIES (order 100) --------------------
        # A stored query that cannot take arguments can only ever answer one
        # question, which is why `commands_for` needed this before it could
        # exist at all.
        url = "postgres://u:p@shared-host:5432/aimem"

        rc, out = run_main(["commands_for", "--args", "pc,any"], url)
        want("a stored query RECEIVES its --args, in order",
             rc == 0 and "form-for-pc-any" in out)

        rc, out = run_main(["commands_for", "--args", "mac,any"], url)
        want("different --args reach the query, so one question answers many",
             rc == 0 and "form-for-mac-any" in out)

        rc, out = run_main(["commands_for"], url)
        want("a query that DECLARES parameters refuses to run without them",
             rc == 2)
        want("the arity refusal NAMES the parameters the query declares",
             "machine,fallback" in out or
             ("machine" in out and "fallback" in out))
        want("the arity refusal prints no rows",
             "form-for-" not in out)

        rc, out = run_main(["commands_for", "--args", "pc"], url)
        want("too FEW parameters is refused, not padded", rc == 2)

        rc, out = run_main(["commands_for", "--args", "pc,any,extra"], url)
        want("too MANY parameters is refused, not truncated", rc == 2)

        rc, out = run_main(["running", "--args", "pc"], url)
        want("a query that declares NO parameters refuses --args", rc == 2)

        rc, out = run_main(["--sql", "SELECT 1", "--args", "pc"], url)
        want("--args on ad hoc SQL is refused rather than ignored", rc == 2)

        want("split_args turns nothing into NO parameters, never one empty one",
             split_args(None) == () and split_args("") == ()
             and split_args("  ") == ())
        want("split_args keeps the order it was given",
             split_args("pc,<owner>/ExampleProject")
             == ("pc", "<owner>/ExampleProject"))

        # An unreachable store must reach an in-process caller as an
        # EXCEPTION, never as an empty list: new-order.py branches on this to
        # decide whether to write a file at all.
        connect_exc.append(InterfaceError("connection refused"))
        os.environ["MEMDB_URL"] = url
        raised = ""
        try:
            stored_query("commands_for", ("pc", "any"))
        except CouldNotAsk:
            raised = "CouldNotAsk"
        except Exception as exc:                      # noqa: BLE001
            raised = type(exc).__name__
        want("stored_query RAISES on an unreachable store, never returns []",
             raised == "CouldNotAsk")

        rows = stored_query("commands_for", ("pc", "any"))
        want("stored_query returns typed ROWS, so no caller parses printed text",
             rows == [["form-for-pc-any"]])

        raised = ""
        try:
            stored_query("commands_for", ("pc",))
        except ArgMismatch:
            raised = "ArgMismatch"
        except Exception as exc:                      # noqa: BLE001
            raised = type(exc).__name__
        want("a misuse raises ArgMismatch, distinct from an unreachable store",
             raised == "ArgMismatch")

        raised = ""
        try:
            stored_query("nope", ())
        except NoSuchQuestion:
            raised = "NoSuchQuestion"
        except Exception as exc:                      # noqa: BLE001
            raised = type(exc).__name__
        want("a wrong NAME raises NoSuchQuestion, distinct from both",
             raised == "NoSuchQuestion")

        os.environ.pop("MEMDB_URL", None)
        raised = ""
        try:
            stored_query("commands_for", ("pc", "any"))
        except CouldNotAsk:
            raised = "CouldNotAsk"
        except Exception as exc:                      # noqa: BLE001
            raised = type(exc).__name__
        want("stored_query with no source configured is COULD NOT ASK, "
             "not an empty answer", raised == "CouldNotAsk")

        # --sql-file: SQL never passes through a shell argument again.
        import tempfile
        holder = tempfile.mkdtemp(prefix="memdb-selftest-")
        try:
            sqlf = Path(holder) / "stmt.sql"
            sqlf.write_text("SELECT 42\n", encoding="utf-8")
            rc, out = run_main(["--sql-file", str(sqlf)], url)
            want("--sql-file runs the statement in the file",
                 rc == 0 and "42" in out)
            rc, out = run_main(["--sql", "SELECT 1", "--sql-file", str(sqlf)],
                               url)
            want("--sql and --sql-file together are refused, not merged",
                 rc == 2)
            rc, out = run_main(["--sql-file", str(Path(holder) / "gone.sql")],
                               url)
            want("a missing --sql-file is refused and runs nothing", rc == 2)
        finally:
            import shutil
            shutil.rmtree(holder, ignore_errors=True)
    finally:
        _LOCAL = False
        pg8000 = real_pg8000
        os.environ = real_environ
        sys.argv = real_argv

    print(f"\npassed: {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
