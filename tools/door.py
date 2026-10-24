#!/usr/bin/env python3
"""Send one request to a configured workbench service.

ORDER_DOOR_URL selects the service endpoint and defaults to
http://localhost/order. ORDER_TOKEN supplies the bearer credential. If the
token is absent, the tool checks ~/.config/workbench/order-token. The endpoint
and token are configuration, not embedded account or network details.

    python door.py view
    python door.py status
    python door.py start <job-id>
    python door.py log <lane>
    python door.py queue "<description>"
    python door.py mail "<subject>" <body-file-or-dash>
    python door.py mail-status
    python door.py --self-test

Exit: 0 answered, 1 refused or unavailable service, 2 invalid request,
3 missing token or unreachable endpoint.

WHY (2026-09-23: cloud work needs a live route to service state; this tool
queries the configured endpoint instead of treating a repository as live state).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DOOR_URL = os.environ.get("ORDER_DOOR_URL", "http://localhost/order")
TOKEN_FILE = Path.home() / ".config" / "workbench" / "order-token"
VERBS = ("view", "status", "start", "log", "queue", "mail", "mail-status")


def token() -> str | None:
    value = os.environ.get("ORDER_TOKEN", "").strip()
    if value:
        return value
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def body_for(verb: str, argument: str | None, text: str | None = None) -> dict:
    if verb in ("start", "log", "queue", "mail") and not argument:
        raise ValueError(f"{verb} needs an argument")
    if verb == "mail":
        if not (text or "").strip():
            raise ValueError("mail needs a body from a file or standard input")
        return {"verb": "mail", "subject": argument, "text": text}
    if verb == "start":
        return {"verb": "start", "job": argument}
    if verb == "log":
        return {"verb": "log", "lane": argument}
    if verb == "queue":
        return {"name": argument}
    return {"verb": verb}


def exit_code_for(answer: dict) -> int:
    if (answer.get("refused") or answer.get("requested") is False
            or answer.get("mail_requested") is False):
        return 1
    return 1 if answer.get("pc") in ("unreachable", "unknown") else 0


def summarise_view(view: dict) -> str:
    slots = view.get("slots", {})
    lines = [f"slots: {slots.get('busy', '?')} of {slots.get('total', '?')} busy"]
    for key, label in (("inflight", "running"), ("waiting", "waiting"),
                       ("claimed_but_no_driver", "claimed, no driver"), ("blocked", "blocked")):
        rows = view.get(key) or []
        lines.append(f"{label}: {len(rows)}")
        for row in rows[:8]:
            lines.append("  - " + "  ".join(str(column) for column in row[:3]))
    if view.get("errors"):
        lines.append(f"the service could not read: {', '.join(view['errors'])}")
    return "\n".join(lines)


def call(body: dict, credential: str) -> tuple[int, dict | str]:
    request = urllib.request.Request(
        DOOR_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Order-Token": credential})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw, status = response.read().decode("utf-8", "replace"), response.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)
    if not raw.strip():
        return status, "(empty reply: service did not answer)"
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw[:300]


def self_test() -> int:
    failures: list[str] = []
    checks = 0

    def want(label: str, condition: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    want("start includes its job identifier", body_for("start", "job-a") ==
         {"verb": "start", "job": "job-a"})
    want("queue uses the service request shape", body_for("queue", "a task") == {"name": "a task"})
    want("view needs no argument", body_for("view", None) == {"verb": "view"})
    try:
        body_for("start", None)
        want("missing start identifier is refused", False)
    except ValueError:
        want("missing start identifier is refused", True)
    want("refusals exit nonzero", exit_code_for({"refused": True}) == 1)
    want("unreachable service exits nonzero", exit_code_for({"pc": "unreachable"}) == 1)
    want("accepted request exits zero", exit_code_for({"requested": True}) == 0)
    want("mail sends subject and body", body_for("mail", "Update", "Details") ==
         {"verb": "mail", "subject": "Update", "text": "Details"})
    for value in (None, "", " \n"):
        try:
            body_for("mail", "Update", value)
            want("empty mail body is refused", False)
        except ValueError:
            want("empty mail body is refused", True)
    want("mail status needs no argument", body_for("mail-status", None) == {"verb": "mail-status"})
    want("unrecorded mail exits nonzero", exit_code_for({"mail_requested": False}) == 1)
    summary = summarise_view({"slots": {"busy": 1, "total": 4},
                              "inflight": [["run", "job", "lane"]], "waiting": [],
                              "blocked": [["job", "blocked", "reason"]]})
    want("view summary counts returned state", "1 of 4 busy" in summary and
         "running: 1" in summary and "blocked: 1" in summary)
    print(f"door self-test: {checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if argv[:1] == ["--self-test"]:
        return self_test()
    if not argv or argv[0] not in VERBS:
        print(__doc__, file=sys.stderr)
        return 2
    verb, argument = argv[0], argv[1] if len(argv) > 1 else None
    text = None
    if verb == "mail" and len(argv) > 2:
        try:
            text = sys.stdin.read() if argv[2] == "-" else Path(argv[2]).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"door: could not read body: {exc}", file=sys.stderr)
            return 2
    try:
        body = body_for(verb, argument, text)
    except ValueError as exc:
        print(f"door: {exc}", file=sys.stderr)
        return 2
    credential = token()
    if not credential:
        print("door: no token; set ORDER_TOKEN or create the configured token file", file=sys.stderr)
        return 3
    status, answer = call(body, credential)
    if status != 200 or not isinstance(answer, dict):
        print(f"door: service could not be queried (HTTP {status}): {answer}", file=sys.stderr)
        return 3
    if verb == "view" and "view" in answer:
        print(summarise_view(answer["view"]))
        print()
    elif verb == "log" and "log" in answer:
        print(answer["log"])
        return 0
    print(json.dumps(answer, indent=1))
    return exit_code_for(answer)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
