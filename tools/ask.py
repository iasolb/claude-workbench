#!/usr/bin/env python3
"""Ask the cheap model a question about some files, through n8n.

    python tools/ask.py --question "<question>" --file <path> [--file <path> ...] [--words 300]
    python tools/ask.py --self-test

WHY (2026-10-06): "you should be having opencode help you when you do the
research stuff also - no need to spend a millions tokens in 10 seconds looking
at the 8 cards", then "that logic can live in n8n though and make ai node calls
- I dont need to see your internals, just have them act cheaper and faster".
A session had fanned seven Claude helpers out to read the queue's orders; each
billed the Claude pool. This posts the file text to the `ask` workflow
(automation/workflows/ask.json), which asks gpt-5-nano and returns only the
answer, so the session pays for a short answer instead of the files.

Exit: 0 answered, 1 the model gave no answer (the reason is printed),
2 bad arguments or an unreadable file, 3 n8n could not be reached. A
missing answer is never printed as an empty one.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import os
from pathlib import Path

URL = os.environ.get("ASK_WEBHOOK_URL", "http://localhost:5678/webhook/ask")
MAX_CHARS = 120_000


def build_context(files: list[tuple[str, str]], cap: int = MAX_CHARS) -> tuple[str, bool]:
    """(the labelled file text with line numbers, whether it was cut to fit the cap)."""
    parts: list[str] = []
    for name, text in files:
        numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(text.splitlines(), 1))
        parts.append(f"=== {name} ===\n{numbered}")
    whole = "\n\n".join(parts)
    if len(whole) <= cap:
        return whole, False
    return whole[:cap] + "\n[CUT: the files ran past the size cap here]", True


def parse_reply(raw: str) -> tuple[str | None, str]:
    """(the answer, "") or (None, why there is none)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None, f"n8n did not return JSON: {raw[:200]!r}"
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None, "n8n returned JSON that is not an object"
    if data.get("ok") and str(data.get("answer") or "").strip():
        return str(data["answer"]).strip(), ""
    return None, str(data.get("error") or "the workflow answered without an answer")


def post(payload: dict, url: str = URL, timeout: int = 300) -> tuple[str | None, str]:
    """(the raw reply, "") or (None, why n8n could not be reached)."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), ""
    except (urllib.error.URLError, OSError) as exc:
        return None, f"{url} unreachable: {exc}"


def ask(question: str, paths: list[str], words: int, send=post) -> int:
    """Read the files, post the question, print the answer. Returns the exit code."""
    files = []
    for p in paths:
        try:
            files.append((p, Path(p).read_text(encoding="utf-8", errors="replace")))
        except OSError as exc:
            print(f"ask: cannot read {p}: {exc}", file=sys.stderr)
            return 2
    context, cut = build_context(files)
    raw, why = send({"question": question, "context": context, "max_words": words})
    if raw is None:
        print(f"ask: {why}", file=sys.stderr)
        return 3
    answer, why = parse_reply(raw)
    if answer is None:
        print(f"ask: NO ANSWER: {why}", file=sys.stderr)
        return 1
    if cut:
        print("ask: NOTE the files were cut to fit; the answer saw only the first part")
    print(answer)
    return 0


def self_test() -> int:
    total = passed = 0

    def want(label: str, cond: bool) -> None:
        nonlocal total, passed
        total += 1
        if cond:
            passed += 1
        else:
            print(f"self-test FAIL: {label}")

    ctx, cut = build_context([("a.py", "x = 1\ny = 2"), ("b.md", "# hi")])
    want("each file is labelled by its path", "=== a.py ===" in ctx and "=== b.md ===" in ctx)
    want("lines carry their numbers so the answer can cite file:line", "2: y = 2" in ctx)
    want("small files are not cut", cut is False)
    ctx, cut = build_context([("big.txt", "z" * 500)], cap=100)
    want("oversized text is cut AND says so", cut and ctx.endswith("size cap here]"))
    want("a real answer is returned",
         parse_reply('{"ok": true, "answer": " yes, a.py:2 "}') == ("yes, a.py:2", ""))
    want("n8n's list-wrapped reply is read too",
         parse_reply('[{"ok": true, "answer": "fine"}]')[0] == "fine")
    got, why = parse_reply('{"ok": false, "error": "model refused"}')
    want("a failed call is NO answer with its reason, never an empty answer",
         got is None and why == "model refused")
    got, why = parse_reply('{"ok": true, "answer": "   "}')
    want("a blank answer counts as no answer", got is None)
    want("non-JSON is no answer, named", parse_reply("<html>")[0] is None)
    sent: list[dict] = []
    rc = ask("q?", [__file__], 50, send=lambda p: (sent.append(p) or None, "down"))
    want("n8n unreachable is exit 3, never success", rc == 3)
    want("the question and word limit are what gets posted",
         bool(sent) and sent[0]["question"] == "q?" and sent[0]["max_words"] == 50)
    rc = ask("q?", [__file__], 50, send=lambda p: ('{"ok": true, "answer": "ok"}', ""))
    want("an answered question is exit 0", rc == 0)
    rc = ask("q?", [__file__], 50,
             send=lambda p: ('{"ok": true, "answer": "a \\u2212 b \\u2192 c"}', ""))
    want("an answer the console cannot encode still prints, exit 0", rc == 0)
    rc = ask("q?", [__file__], 50, send=lambda p: ('{"ok": false, "error": "x"}', ""))
    want("a workflow with no answer is exit 1", rc == 1)
    want("an unreadable file is exit 2 and posts nothing",
         ask("q?", ["Z:/no/such/file.txt"], 50,
             send=lambda p: (_ for _ in ()).throw(AssertionError("posted"))) == 2)

    print(f"ask self-test: {passed}/{total} checks passed")
    return 0 if passed == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ask")
    ap.add_argument("--question")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--words", type=int, default=300)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    # The Windows console is cp1252; a model answer with a unicode minus
    # crashed the print after the answer had been paid for (2026-10-06).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if a.self_test:
        return self_test()
    if not a.question or not a.file:
        print("ask: --question and at least one --file are required", file=sys.stderr)
        return 2
    return ask(a.question, a.file, a.words)


if __name__ == "__main__":
    sys.exit(main())
