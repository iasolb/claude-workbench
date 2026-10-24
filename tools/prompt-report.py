#!/usr/bin/env python3
"""What has been prompting the owner, grouped by CAUSE, read from the prompt log.

    python tools/prompt-report.py                  # last 14 days, this machine
    python tools/prompt-report.py --since 2026-09-20
    python tools/prompt-report.py --machine mac
    python tools/prompt-report.py --self-test

WHY THIS EXISTS (2026-09-27): "your permission prompts have been extremely
bad these past couple weeks". A Fast Lane pass read the "always allow" clicks,
found one gap, and missed the real record: `hooks/prompt-log.*` had written 116
rows to `prompts/<machine>.tsv` in those two weeks while the queue claimed it
had "never written a row". The clicks only capture prompts he chose to make
permanent; the log captures every box. So the log is the instrument, and the
question "what has been prompting" is asked often enough to be a command (A6).

Each row is classified into ONE cause, because the cause decides the fix:

  entry       a plain command no allow entry matches: add one (Fast Lane)
  unfixable   a chain, pipe, redirect or inline code: no entry can ever match,
              the SESSION must stop writing that form
  cmdlet      a state-changing PowerShell cmdlet (Stop-Process, Remove-Item,
              Start-Process...): prompts by design unless deliberately granted
  path        Read/Grep/Glob/Edit/Write: a path outside the allowed roots, or a
              SUBAGENT's call credited to the parent (the hook reads only the
              parent transcript's last tool_use)
  mcp         an MCP tool with no allow entry
  not-a-prompt  AskUserQuestion: a form raises the same notification; it is not
              a permission box and must not be counted as one

LIMIT, stated so the numbers are not over-read: the hook credits the LAST
tool_use in the main transcript, so a prompt raised by a parallel call or a
subagent is attributed to whatever the parent ran last. Counts by cause are
reliable in aggregate; a single `path` row is a lead, not a proof.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELLS = ("Bash", "PowerShell")
FILE_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit")
CMDLETS = ("stop-process", "remove-item", "start-process", "set-content",
           "new-item", "copy-item", "move-item", "set-scheduledtask",
           "register-scheduledtask", "start-scheduledtask", "schtasks")
UNFIXABLE = re.compile(r"(\|\||&&|;|\||(?<![-=])>|\s-c\s|\s-Command\s)")


def classify(tool: str, command: str) -> str:
    """The one cause a logged prompt belongs to."""
    if tool == "AskUserQuestion":
        return "not-a-prompt"
    if tool.startswith("mcp__"):
        return "mcp"
    if tool in FILE_TOOLS:
        return "path"
    if tool in SHELLS:
        # Quoted text is data, not shell structure: strip it before looking for
        # a pipe or a semicolon, or a commit message with a colon-list reads as
        # a chain.
        bare = re.sub(r"\"[^\"]*\"|'[^']*'", "", command)
        if UNFIXABLE.search(bare):
            return "unfixable"
        first = (bare.split() or [""])[0].lower()
        if first in CMDLETS:
            return "cmdlet"
        return "entry"
    return "other"


def program(command: str) -> str:
    """The first word, shortened to a file name, for grouping entry gaps."""
    first = (command.split() or ["?"])[0].strip("\"'")
    return re.split(r"[\\/]", first)[-1] or first


def load(path: Path, since: str):
    """Rows with last_seen >= since, as (count, last_seen, tool, command).

    A missing file is NOT an empty world: the caller is told which it was."""
    if not path.is_file():
        return None
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for rec in csv.reader(fh, delimiter="\t"):
            if len(rec) < 6 or rec[0] == "count":
                continue
            try:
                count = int(rec[0])
            except ValueError:
                continue
            if rec[2] >= since:
                rows.append((count, rec[2], rec[4], rec[5], rec[3]))
    return rows


def prefer_exact(rows):
    """(rows to report, guessed rows dropped).

    Once PermissionRequest rows (`exact`) exist in the window, the guessed
    Notification rows are the SAME prompts credited to the wrong call, so
    counting both double-counts and keeps the misattribution alive."""
    exact = [r for r in rows if r[4] == "exact"]
    if not exact:
        return rows, 0
    return exact, len(rows) - len(exact)


def report(rows) -> str:
    by_cause: dict[str, list] = defaultdict(list)
    for row in rows:
        by_cause[classify(row[2], row[3])].append(row)
    total = sum(r[0] for r in rows)
    out = ["%d prompt(s) across %d distinct call(s)" % (total, len(rows))]
    order = ("entry", "unfixable", "cmdlet", "path", "mcp", "other",
             "not-a-prompt")
    for cause in order:
        got = by_cause.get(cause, [])
        if not got:
            continue
        n = sum(r[0] for r in got)
        out.append("")
        out.append("%-12s %d prompt(s)" % (cause, n))
        if cause == "entry":
            progs = Counter()
            for r in got:
                progs[program(r[3])] += r[0]
            for prog, k in progs.most_common(10):
                out.append("  %4d  %s" % (k, prog))
        for r in sorted(got, key=lambda r: -r[0])[:5]:
            out.append("    e.g. [%s] %s %s" % (r[1], r[2], r[3][:110]))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="prompt-report")
    ap.add_argument("--since", help="YYYY-MM-DD, default 14 days ago")
    ap.add_argument("--machine", default="windows",
                    help="log file stem under prompts/ (windows, mac)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    since = a.since or (dt.date.today() - dt.timedelta(days=14)).isoformat()
    path = REPO / "prompts" / ("%s.tsv" % a.machine)
    rows = load(path, since)
    if rows is None:
        print("prompt-report: NO LOG at %s. That is could-not-read, not "
              "zero prompts." % path, file=sys.stderr)
        return 3
    rows, dropped = prefer_exact(rows)
    print("since %s, %s" % (since, path.name))
    if dropped:
        print("EXACT rows only (from the PermissionRequest hook); %d guessed "
              "row(s) ignored" % dropped)
    else:
        print("GUESSED rows only: each tool is the parent's last call, so a "
              "subagent's prompt is credited to the wrong command")
    print(report(rows))
    return 0


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    want("a form is not a permission prompt",
         classify("AskUserQuestion", '{"questions":[]}') == "not-a-prompt")
    want("a plain command with no entry is an entry gap",
         classify("Bash", "/c/x/.venv/Scripts/python.exe -m pytest t -q")
         == "entry")
    want("a pipe can never be fixed by an entry",
         classify("PowerShell", "Get-Process | Select-Object Id") == "unfixable")
    want("a redirect can never be fixed by an entry",
         classify("PowerShell", "python.exe tool.py --self-test > out.txt")
         == "unfixable")
    want("inline code can never be fixed by an entry",
         classify("Bash", "python.exe -c import os") == "unfixable")
    want("a semicolon INSIDE a quoted message is not a chain",
         classify("Bash", 'git -C r commit -m "a; b" -- p') == "entry")
    want("Stop-Process is a state-changing cmdlet, not an entry gap",
         classify("PowerShell", "Stop-Process -Id 1 -Confirm:$false")
         == "cmdlet")
    want("a file tool prompting is a path problem",
         classify("Grep", '{"pattern":"x"}') == "path")
    want("an MCP tool is its own cause",
         classify("mcp__scheduled-tasks__run_scheduled_task", "{}") == "mcp")
    want("entry gaps group by program name, not full path",
         program("/c/example-home/.venv/Scripts/python.exe -m pytest") == "python.exe")
    want("a flag like --x=>y is not read as a redirect",
         classify("Bash", "tool --since=2026 --a=>b") == "entry")

    with tempfile.TemporaryDirectory(prefix="prompt-report-") as td:
        log = Path(td) / "w.tsv"
        log.write_text(
            "count\tfirst_seen\tlast_seen\tdisposition\ttool\tcommand\n"
            "3\t2026-09-01 10:00\t2026-09-20 10:00\t?\tBash\tssh vm uptime\n"
            "1\t2026-09-01 10:00\t2026-09-02 10:00\t?\tBash\told thing\n"
            "2\t2026-09-21 10:00\t2026-09-21 10:00\t?\tAskUserQuestion\t{}\n",
            encoding="utf-8")
        rows = load(log, "2026-09-13")
        want("rows older than --since are dropped", len(rows) == 2)
        text = report(rows)
        want("the total counts repeats, not rows", text.startswith("5 prompt(s)"))
        want("the entry gap names its program", "ssh" in text and "entry" in text)
        want("forms are reported apart, so they never inflate the gaps",
             "not-a-prompt" in text)
        want("a missing log is None, distinct from an empty one",
             load(Path(td) / "absent.tsv", "2026-09-13") is None)
        mixed = [(1, "2026-09-27", "Grep", "{}", "?"),
                 (1, "2026-09-27", "Bash", "ssh vm x", "exact")]
        kept, dropped = prefer_exact(mixed)
        want("exact rows win over guessed ones once any exist",
             [r[2] for r in kept] == ["Bash"] and dropped == 1)
        want("with no exact rows the guessed ones are still reported",
             prefer_exact(mixed[:1]) == (mixed[:1], 0))

    print("\n%d check(s), %d failure(s)" % (checks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
