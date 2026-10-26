#!/usr/bin/env python3
"""corrections.py: review typed owner messages and identify likely corrections.

    python tools/corrections.py                    # likely corrections, 14 days
    python tools/corrections.py --since YYYY-MM-DD --all
    CLAUDE_PROJECTS_DIR=/path/to/projects python tools/corrections.py
    python tools/corrections.py --self-test

By default, transcripts are read from Path.home() / ".claude" / "projects".
Set CLAUDE_PROJECTS_DIR to use another transcript directory. Tool results,
hook output, prompt expansions and continuation summaries are excluded. A
transcript directory that cannot be read exits 4, never as "no corrections".

WHY (2026-10-26: "go through our history and see where you broke our golden
rules and lets guard rail better").
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOTS = [Path(os.environ.get("CLAUDE_PROJECTS_DIR",
                           Path.home() / ".claude" / "projects"))]
MARKERS = re.compile(
    r"\b(fuck|shit|unacceptable|again|still|never|stop|don'?t|do not|why (are|did|is)|"
    r"you (ran|didn'?t|never|broke|keep)|for the \d+\w*\s+ti\w*|0%|not using|failure|"
    r"had to find|are we doing work|youre watching)\b", re.I)
DROP_PREFIXES = ("You are the", "Caveat", "This session is being continued", "Background (from",
                 "Task (from", "[Request interrupted")


def typed_text(entry: dict) -> str | None:
    """A person's own words in one transcript entry, or None."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [p.get("text", "") for p in content
                 if isinstance(p, dict) and p.get("type") == "text"]
    else:
        return None
    for text in (part.strip() for part in parts):
        if "<command-args>" in text:
            args = text.split("<command-args>", 1)[1].split("</command-args>", 1)[0].strip()
            name = (text.split("<command-name>", 1)[1].split("</command-name>", 1)[0]
                    if "<command-name>" in text else "")
            text = f"{name} {args}".strip() if args else ""
        if not text or text.startswith("<") or text.startswith(DROP_PREFIXES):
            continue
        return " ".join(text.split())
    return None


def messages(since: dt.datetime, roots=ROOTS) -> list[tuple[str, str, str]]:
    """(timestamp, session prefix, text), oldest first, one row per message."""
    files = [f for root in roots if root.is_dir() for f in root.glob("*/*.jsonl")]
    if not files:
        raise FileNotFoundError(f"no transcripts under {', '.join(map(str, roots))}")
    seen, out = set(), []
    for file in files:
        if dt.datetime.fromtimestamp(file.stat().st_mtime, dt.timezone.utc) < since:
            continue
        for line in file.open(encoding="utf-8", errors="replace"):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            text = typed_text(entry)
            timestamp = entry.get("timestamp", "")
            if text and timestamp >= since.isoformat()[:19] and (timestamp[:16], text) not in seen:
                seen.add((timestamp[:16], text))
                out.append((timestamp, file.stem[:8], text))
    return sorted(out)


def self_test() -> int:
    bad = 0

    def want(cond: bool, what: str) -> None:
        nonlocal bad
        bad += not cond
        print(("PASS  " if cond else "FAIL  ") + what)

    typed = {"type": "user", "message": {"content": "do not send an email"}}
    skill = {"type": "user", "message": {"content": [{"type": "text", "text":
             "<command-message>helper</command-message>\n<command-name>/helper</command-name>\n"
             "<command-args>review the work</command-args>"}]}}
    bare_skill = {"type": "user", "message": {"content": "<command-message>helper</command-message> "
                   "<command-name>/helper</command-name>"}}
    tool_result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
    expansion = {"type": "user", "isMeta": True, "message": {"content": "You are the helper persona"}}
    brief = {"type": "user", "message": {"content": "Background (from the prior session): x"}}
    want(typed_text(typed) == "do not send an email", "a typed message is kept")
    want(typed_text(skill) == "/helper review the work", "a command keeps its arguments")
    want(typed_text(bare_skill) is None, "a command with no arguments is not a message")
    want(typed_text(tool_result) is None, "a tool result is not typed text")
    want(typed_text(expansion) is None, "a prompt expansion is not typed text")
    want(typed_text(brief) is None, "a continuation brief is not typed text")
    want(bool(MARKERS.search("fix this shit for the 10th time")), "a repeated failure is flagged")
    want(bool(MARKERS.search("you ran tons of permission prompts")), "an explicit complaint is flagged")
    want(not MARKERS.search("Run the project briefing on the updated data."),
         "a plain request is not classified as a correction")
    try:
        messages(dt.datetime.now(dt.timezone.utc), roots=[Path("missing-transcript-directory")])
        want(False, "no transcript directory is an error, never an empty answer")
    except FileNotFoundError:
        want(True, "no transcript directory is an error, never an empty answer")
    print(f"\n{'PASS' if not bad else 'FAIL'}: {bad} failing")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD, default 14 days ago")
    ap.add_argument("--all", action="store_true", help="every typed message, not only likely corrections")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    since = (dt.datetime.fromisoformat(a.since).replace(tzinfo=dt.timezone.utc) if a.since
             else dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14))
    try:
        rows = messages(since)
    except FileNotFoundError as exc:
        print(f"COULD NOT READ transcripts: {exc}", file=sys.stderr)
        return 4
    sys.stdout.reconfigure(encoding="utf-8")
    shown = 0
    for timestamp, session_id, text in rows:
        if a.all or MARKERS.search(text):
            shown += 1
            print(timestamp[:16].replace("T", " "), session_id, text[:400])
    print(f"[corrections] {shown} of {len(rows)} typed messages since {since.date()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
