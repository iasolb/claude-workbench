#!/usr/bin/env python3
"""run-cost.py: one command reports what ONE driver lane actually cost.

The hole, verbatim from rules/shared/cost.md: "every model choice is justified
by the ledger or explicitly labelled a guess. Right now every one of them is a
guess." This framework can say a job took 157 seconds and passed (state/landings
.csv), and could not say what it cost. This tool is the READER that closes it:
it turns the opencode export surface into one per-run row in the cost ledger
schema. Wiring the row into the landing file is a separate, later change, and
this file does not do it.

    python tools/run-cost.py --lane tools-7ba78a [--session ses_...]
    python tools/run-cost.py --lane tools-7ba78a --json
    python tools/run-cost.py --self-test

The row, per RUN:

    at, lane, order, model, input, output, cache_read, cache_write,
    gross_amount, source

WHERE EACH FIELD COMES FROM (the schema is documented in
docs/cost-ledger-schema.md, which is what a future session reads instead of
rediscovering this surface):

    at            the session's created instant, from `opencode export`
                  (`info.time.created`), formatted local ISO.
    lane          the worktree directory name, which is the lane slug.
    order         the launcher's own record, read from
                  state/drivers/<lane>.timing (`order:`). A lane with no
                  timing file reads empty, which is honest.
    model         providerID/modelID from the export's `info.model`.
    input         `info.tokens.input`, output `info.tokens.output`,
    cache_read    `info.tokens.cache.read`, cache_write
                  `info.tokens.cache.write`.
    gross_amount  `info.cost`, the vendor-reported figure for the session.
                  It has measured LOW against the Go usage console (tools/
                  drive.py, 2026-08-30), so it is the endpoint's number, not
                  the account's; the console is the meter.
    source        endpoint for every row this tool writes: read straight from
                  `opencode export`, never estimated.

THE RULE THAT GOVERNS EVERY CELL (the whole point of the schema):
a field that could not be read is EMPTY, never zero. "I could not read this"
and "this cost nothing" are opposite answers, and a routing decision made on
the wrong one is worse than no decision. A real measured 0 (cache_write is 0
on most of these sessions) stays 0; only a missing value becomes empty. The
self-test proves the two render differently.

HOW A LANE IS FOUND: `opencode session list` carries id, title and updated
time only, and the title is an auto-generated paraphrase, never the lane slug.
The directory is the join key: `opencode export <id>` carries
`info.directory`, whose basename is the lane's worktree name. Discovery
exports sessions newest-first until the directory matches. The list only shows
recent sessions, so an old lane may have fallen off it; `--session` skips
discovery entirely and is the deterministic path.

Three CLI commands cover the whole surface: `opencode stats` (cumulative
only, no per-session breakdown, so it is not used here), `opencode session
list`, and `opencode export <id>`. There is no fourth.

Standard library only. The opencode binary is the real exe, never the npm
shim: the shim is a .cmd/.ps1 wrapper that CreateProcess cannot execute
(same trap documented in tools/bench-lane.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# The executable is resolved from PATH; set OPENCODE_BIN for an explicit path.
OPENCODE_BIN = (os.environ.get("OPENCODE_BIN") or
                shutil.which("opencode") or "opencode")

# Timing files default under ~/.local/share/workbench/state/drivers. Set
# DRIVERS_DIR when the launcher stores state elsewhere. Order and lane are read
# from timing files, never guessed; cost and tokens come from the export.
PROD_DRIVERS = Path(os.environ.get(
    "DRIVERS_DIR", str(Path.home() / ".local" / "share" /
                      "workbench" / "state" / "drivers")))

# The ledger schema, one home. docs/cost-ledger-schema.md explains each cell.
ROW_COLUMNS = [
    "at", "lane", "order", "model", "input", "output",
    "cache_read", "cache_write", "gross_amount", "source",
]

SOURCE_ENDPOINT = "endpoint"

_SESSION_ID_RE = re.compile(r"^(ses_\S+)\s+")
_EXPORT_PREFIX = re.compile(r"Exporting session:")


class ShapeError(Exception):
    """The export answered, and the answer was not the expected shape."""


def _int_or_none(value):
    """An int when the export carried a number, else None (missing, not zero)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_iso(created_ms) -> str:
    """Local ISO instant from the export's epoch-milliseconds, or '' when absent."""
    if created_ms is None:
        return ""
    try:
        return datetime.fromtimestamp(int(created_ms) / 1000.0).strftime("%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _model_name(provider, model_id) -> str:
    if provider and model_id:
        return "%s/%s" % (provider, model_id)
    return model_id or ""


def parse_export(text: str) -> dict:
    """Extract the measured fields from `opencode export` stdout.

    The export is one JSON object prefixed by an `Exporting session: ...` line
    (and sometimes trailing noise), so the parse starts at the first `{`. The
    session-level `info` object carries directory, model, cost and the token
    totals; a message-level sum would double-count nothing but is unnecessary,
    since `info.cost` and `info.tokens` are already the session totals.

    Every missing value comes back as None, never 0: the caller renders None
    as an EMPTY cell and a real 0 as 0, and the self-test proves they differ.
    """
    start = text.find("{")
    if start < 0:
        raise ShapeError("the export carried no JSON object")
    try:
        data = json.loads(text[start:])
    except ValueError as exc:
        raise ShapeError("the export JSON did not parse: %s" % exc)
    info = data.get("info")
    if not isinstance(info, dict):
        raise ShapeError("the export has no session-level info object")
    tokens = info.get("tokens") or {}
    cache = tokens.get("cache") or {}
    model = info.get("model") or {}
    return {
        "directory": info.get("directory") or "",
        "created": _to_iso((info.get("time") or {}).get("created")),
        "model": _model_name(model.get("providerID"), model.get("id")),
        "input": _int_or_none(tokens.get("input")),
        "output": _int_or_none(tokens.get("output")),
        "cache_read": _int_or_none(cache.get("read")),
        "cache_write": _int_or_none(cache.get("write")),
        "cost": _float_or_none(info.get("cost")),
    }


def _fmt_int(value) -> str:
    """'' for a missing count, the plain digits for a measured one (0 stays 0)."""
    return "" if value is None else str(int(value))


def _fmt_cost(value) -> str:
    return "" if value is None else format(float(value), ".10g")


def build_row(lane: str, order: str, fields: dict) -> dict:
    """One ledger row. None -> empty; a measured 0 -> '0'; never a guess."""
    return {
        "at": fields["created"],
        "lane": lane,
        "order": order,
        "model": fields["model"],
        "input": _fmt_int(fields["input"]),
        "output": _fmt_int(fields["output"]),
        "cache_read": _fmt_int(fields["cache_read"]),
        "cache_write": _fmt_int(fields["cache_write"]),
        "gross_amount": _fmt_cost(fields["cost"]),
        "source": SOURCE_ENDPOINT,
    }


def render_row(row: dict) -> str:
    return ",".join(row[c] for c in ROW_COLUMNS)


def read_timing_order(drivers: Path, lane: str) -> str:
    """The launcher's own `order:` for a lane, or '' when it is not recorded.

    The timing file is the one durable join to the landing ledger (lane plus
    launch instant); its order field is where this row's order comes from. A
    missing file or a missing key means the order is not recoverable, which
    reads empty, never a made-up number.
    """
    try:
        text = (drivers / ("%s.timing" % lane)).read_text(encoding="utf-8",
                                                          errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip() == "order":
            return v.strip().strip('"')
    return ""


def session_ids_from_listing(text: str) -> list:
    """Session ids from `opencode session list`, newest first.

    Each row is `ses_...  Title  Updated`; only the leading id is read. The
    header and the separator line never start with `ses_`, so they are skipped
    by construction.
    """
    out = []
    for line in text.splitlines():
        m = _SESSION_ID_RE.match(line.strip())
        if m:
            out.append(m.group(1))
    return out


def find_session_id(lane: str, ids: list, get_export: Callable) -> Optional[str]:
    """The most recent session whose worktree directory basename is the lane.

    Discovery exports candidates newest-first and stops at the first whose
    `info.directory` basename matches, because a lane's session records the
    lane's worktree as its directory. A malformed export is skipped, not fatal.
    """
    for sid in ids:
        try:
            fields = parse_export(get_export(sid))
        except ShapeError:
            continue
        if Path(fields["directory"]).name == lane:
            return sid
    return None


def run_opencode(args: list, timeout: int = 180):
    """Run the opencode binary, returning (returncode, stdout)."""
    try:
        proc = subprocess.run(
            [OPENCODE_BIN] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "could not run %s: %s" % (OPENCODE_BIN, exc)
    return proc.returncode, proc.stdout or ""


def _not_read(reason: str) -> int:
    print("NOT READ: %s. This is not evidence that the run was free." % reason,
          file=sys.stderr)
    return 1


def cmd_row(args) -> int:
    rc, out = run_opencode(["session", "list"])
    if rc is None or rc != 0 or not out.strip():
        return _not_read("`opencode session list` returned nothing (%s)" %
                         (out or rc))
    ids = session_ids_from_listing(out)
    if not ids:
        return _not_read("`opencode session list` carried no sessions")

    if args.session:
        sid = args.session
    else:
        sid = find_session_id(args.lane, ids,
                              lambda i: run_opencode(["export", i])[1])
        if sid is None:
            return _not_read("no session in the list has a directory named "
                             "`%s`; it may have fallen off the list, pass "
                             "--session explicitly" % args.lane)

    rc, out = run_opencode(["export", sid])
    if rc is None or rc != 0 or not out.strip():
        return _not_read("`opencode export %s` returned nothing (%s)" %
                         (sid, out or rc))
    try:
        fields = parse_export(out)
    except ShapeError as exc:
        return _not_read("`opencode export %s` could not be parsed: %s" %
                         (sid, exc))

    order = read_timing_order(Path(args.drivers), args.lane)
    row = build_row(args.lane, order, fields)

    if args.json:
        print(json.dumps(row))
    else:
        print(",".join(ROW_COLUMNS))
        print(render_row(row))
    print("gross_amount is the API's figure from `opencode export` for session "
          "%s; it has measured LOW against the Go usage console, which is the "
          "meter (rules/shared/cost.md)." % sid, file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# Self-test. Machinery ships with its test (memory-integrity rule 6), and an
# assertion only ever seen passing is not known to run, so the load-bearing
# empty-not-zero check was proven RED before being kept: the fixture's absent
# fields were first rendered as 0, the self-test failed, then the mapping was
# restored and the same assertion went green. That red run is the Evidence for
# the empty-not-zero rule, not the green one.
# --------------------------------------------------------------------------

# Representative session-level export fixture, shaped like `opencode export`.
EXPORT_FIXTURE = """Exporting session: ses_example_02
{
  "info": {
    "id": "ses_example_02",
    "slug": "sample-session",
    "projectID": "example-project-id",
    "directory": "$HOME/worktrees/example-lane",
    "path": "",
    "title": "Example task",
    "agent": "build",
    "model": {
      "id": "example-model",
      "providerID": "example-provider",
      "variant": "default"
    },
    "version": "1.0.0",
    "summary": {"additions": 0, "deletions": 0, "files": 0},
    "cost": 0.01,
    "tokens": {
      "input": 3200,
      "output": 1000,
      "reasoning": 10,
      "cache": {"read": 5000, "write": 0}
    },
    "permission": [
      {"permission": "question", "pattern": "*", "action": "deny"},
      {"permission": "plan_enter", "pattern": "*", "action": "deny"},
      {"permission": "plan_exit", "pattern": "*", "action": "deny"}
    ],
    "time": {"created": 1788091200000, "updated": 1788092818756}
  },
  "messages": []
}
"""

TIMING_FIXTURE = (
    'produced_at: "2026-08-30T22:28:49"\n'
    'lane: "example-lane"\n'
    'order: "sample-order"\n'
    'model: ""\n'
    'launched_at: "2026-08-30T22:01:46"\n'
)

# A representative `opencode session list` output, including its header and
# separator so the parser must skip them.
LISTING_FIXTURE = (
    "Session ID                      Title                                "
    "Updated\n"
    "--------------------------------"
    "--------------------------------------------\n"
    "ses_example_01  Example earlier session  "
    "10:39 PM\n"
    "ses_example_02  Example task  10:28 PM\n"
)


def self_test() -> int:
    checks: list = []
    ran = 0

    def want(name: str, cond: bool) -> None:
        # A want() that returns None INTO a list comprehension is how
        # assertions in an older tool never executed while it printed PASS.
        # Append; return nothing.
        nonlocal ran
        ran += 1
        checks.append((name, bool(cond)))

    fields = parse_export(EXPORT_FIXTURE)
    want("the export fixture parses to its directory",
         fields["directory"] == "$HOME/worktrees/example-lane")
    want("the export fixture parses its provider and model",
         fields["model"] == "example-provider/example-model")
    want("the export fixture parses the input token count",
         fields["input"] == 3200)
    want("the export fixture parses the output token count",
         fields["output"] == 1000)
    want("the export fixture parses the cache read count",
         fields["cache_read"] == 5000)
    want("the export fixture parses the cache write (a measured 0)",
         fields["cache_write"] == 0)
    want("the export fixture parses its measured cost",
         fields["cost"] == 0.01)
    # The fixture instant is 2026-08-30 12:00 UTC, so the local date is the
    # same from UTC-12 to UTC+11; an instant near midnight UTC failed in EDT.
    want("the created instant is rendered as a local ISO string",
         fields["created"].startswith("2026-08-30T"))

    want("directory basename is the lane (the join key)",
         Path(fields["directory"]).name == "example-lane")

    order = read_timing_order_from_text(TIMING_FIXTURE)
    want("the launcher's order is read from the timing file",
         order == "sample-order")
    want("a missing timing file yields an empty order, not a guess",
         read_timing_order(Path("Z:/does/not/exist"), "nope") == "")

    row = build_row("example-lane", order, fields)
    want("the row is the exact 10-column schema",
         list(row.keys()) == ROW_COLUMNS)
    want("the row's order is the launcher's record",
         row["order"] == "sample-order")
    want("input renders as the digits, not a float",
         row["input"] == "3200")
    want("cache_write renders 0 (a measured zero stays zero)",
         row["cache_write"] == "0")
    want("gross_amount renders the endpoint's figure",
         row["gross_amount"] == "0.01")
    want("the row's source is endpoint",
         row["source"] == SOURCE_ENDPOINT)
    want("render_row is the 10-field CSV line",
         render_row(row) == ",".join(row[c] for c in ROW_COLUMNS))

    # ---- THE load-bearing pair: a missing field and a real zero must never
    # ---- look alike. "I could not read this" is not "this cost nothing".
    absent_cost = build_row("x", "", dict(fields, cost=None))
    zero_cost = build_row("x", "", dict(fields, cost=0.0))
    want("a missing cost renders EMPTY, not 0",
         absent_cost["gross_amount"] == "")
    want("a measured zero cost renders 0, not empty",
         zero_cost["gross_amount"] == "0")
    want("empty and measured-zero render differently",
         absent_cost["gross_amount"] != zero_cost["gross_amount"])

    absent_input = build_row("x", "", dict(fields, input=None))
    want("a missing token count renders EMPTY, not 0",
         absent_input["input"] == "")
    want("the real measured cache_write is not collapsed to empty",
         row["cache_write"] == "0"
         and build_row("x", "", dict(fields, cache_write=None))["cache_write"] == "")

    # ---- session discovery.
    ids = session_ids_from_listing(LISTING_FIXTURE)
    want("session listing yields the ids in order, header skipped",
         ids == ["ses_example_01", "ses_example_02"])
    want("a listing with no sessions yields an empty list",
         session_ids_from_listing("Session ID\n----\n") == [])

    found = find_session_id(
        "example-lane", ids,
        lambda i: EXPORT_FIXTURE if i == "ses_example_02" else "junk")
    want("discovery finds the lane by its worktree directory",
         found == "ses_example_02")
    want("discovery returns None when no directory matches",
         find_session_id("no-such-lane", ids,
                          lambda i: EXPORT_FIXTURE) is None)

    want("garbage export text is refused, not parsed as a row",
         _raises(ShapeError, lambda: parse_export("no json here")))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print("  %s  %s" % ("ok  " if ok else "FAIL", name))
    if failed:
        print("\n%d checks ran, %d failed" % (ran, len(failed)))
        return 1
    print("\n%d/%d checks passed" % (ran, ran))
    return 0


def _raises(exc_type, fn) -> bool:
    try:
        fn()
    except exc_type:
        return True
    return False


def read_timing_order_from_text(text: str) -> str:
    """Test seam: parse an order key from timing text without the filesystem."""
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip() == "order":
            return v.strip().strip('"')
    return ""


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        prog="run-cost.py",
        description="Report what one driver lane actually cost, in tokens "
                    "and money, from the opencode export surface.")
    ap.add_argument("--lane", help="the lane slug, e.g. tools-7ba78a")
    ap.add_argument("--session", default="",
                    help="skip discovery: export this session id directly")
    ap.add_argument("--drivers", default=str(PROD_DRIVERS),
                    help="directory holding state/drivers timing files")
    ap.add_argument("--json", action="store_true",
                    help="print the row as one JSON object")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the parsers and the empty-not-zero rule")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.lane:
        ap.error("--lane is required unless --self-test is given")
    return cmd_row(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
