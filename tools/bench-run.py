#!/usr/bin/env python3
"""Run one benchmark task on an opencode server and print its result as JSON.

Standard library plus the public drive.py helper. The server is addressed via
localhost. The password comes from a file, environment, or user environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import tally

LOCALHOST = "localhost"


def verdict_of(stalled: bool, got_response: bool) -> str:
    if stalled:
        return "stalled"
    return "passed" if got_response else "failed"


def resolve_password(password_file: str | None = None) -> str | None:
    """Resolve a password without printing it: file, environment, then Windows user setting."""
    if password_file:
        with open(password_file, encoding="utf-8") as stream:
            return stream.read().strip()
    value = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if value:
        return value.strip()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "OPENCODE_SERVER_PASSWORD")
            return value.strip()
    except Exception:
        return None


def self_test() -> int:
    failures: list[str] = []
    checks = 0

    def want(label: str, condition: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    want("a response inside the cap passes", verdict_of(False, True) == "passed")
    want("no response fails", verdict_of(False, False) == "failed")
    want("a timed-out task is stalled", verdict_of(True, False) == "stalled")
    want("stall status wins over a late response", verdict_of(True, True) == "stalled")
    want("only the three expected verdicts are possible",
         {verdict_of(stall, response) for stall in (True, False)
          for response in (True, False)} == {"passed", "failed", "stalled"})
    totals, cost, _ = tally([{"info": {"role": "assistant", "cost": 0.5,
                                        "tokens": {"input": 3, "cache": {"write": 7}}}}])
    want("the public usage helper counts cache-write tokens", totals["cache_write"] == 7)
    want("the prompt total includes input and cache writes", totals["input"] + totals["cache_write"] == 10)
    want("the shared helper returns cost", cost == 0.5)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="bench-run-") as temp:
        password_file = os.path.join(temp, "password.txt")
        with open(password_file, "w", encoding="utf-8") as stream:
            stream.write("  sample-secret  \n")
        want("an explicit password file is stripped", resolve_password(password_file) == "sample-secret")
        previous = os.environ.get("OPENCODE_SERVER_PASSWORD")
        try:
            os.environ["OPENCODE_SERVER_PASSWORD"] = "from-environment"
            want("the explicit file takes precedence", resolve_password(password_file) == "sample-secret")
            want("the environment is used without a file", resolve_password() == "from-environment")
        finally:
            if previous is None:
                os.environ.pop("OPENCODE_SERVER_PASSWORD", None)
            else:
                os.environ["OPENCODE_SERVER_PASSWORD"] = previous
    print(f"bench-run self-test: {checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--provider", default="opencode")
    parser.add_argument("--model", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--password-file")
    parser.add_argument("--cap", type=int, default=900)
    parser.add_argument("--agent", default="build")
    args = parser.parse_args()
    password = resolve_password(args.password_file)
    if not password:
        print("HARNESS FAILURE: no server password; set OPENCODE_SERVER_PASSWORD or provide --password-file")
        return 2
    with open(args.prompt_file, encoding="utf-8") as stream:
        prompt = stream.read()

    base = f"http://{LOCALHOST}:{args.port}"
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, base, "opencode", password)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(manager))

    def call(method: str, path: str, body=None, timeout: int = 60):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        directory = urllib.parse.quote(args.worktree, safe="")
        session = call("POST", "/session?directory=" + directory,
                       {"title": "bench-" + args.model})
        session_id = session["id"]
    except Exception as exc:
        print(f"HARNESS FAILURE: could not create session: {exc}")
        return 2

    started = time.time()
    outcome: dict = {}

    def send() -> None:
        try:
            outcome["response"] = call(
                "POST", f"/session/{session_id}/message",
                {"model": {"providerID": args.provider, "modelID": args.model},
                 "agent": args.agent, "parts": [{"type": "text", "text": prompt}]},
                timeout=args.cap + 120)
        except Exception as exc:
            outcome["send_error"] = str(exc)

    worker = threading.Thread(target=send, daemon=True)
    worker.start()
    worker.join(args.cap)
    stalled = worker.is_alive()
    if stalled:
        try:
            call("POST", f"/session/{session_id}/abort", timeout=30)
        except Exception:
            pass
    wall = round(time.time() - started, 1)
    try:
        messages = call("GET", f"/session/{session_id}/message", timeout=60)
    except Exception as exc:
        print(f"HARNESS FAILURE: run ended but messages could not be read: {exc}")
        return 2
    totals, cost, model_error = tally(messages)
    result = {
        "model": args.model, "provider": args.provider, "session_id": session_id,
        "wall_seconds": wall, "completed": not stalled and "response" in outcome,
        "stalled": stalled, "verdict": verdict_of(stalled, "response" in outcome),
        "send_error": outcome.get("send_error"), "model_error": model_error,
        "input_tokens": totals["input"], "output_tokens": totals["output"],
        "reasoning_tokens": totals["reasoning"], "cache_read": totals["cache_read"],
        "cache_write": totals["cache_write"],
        "prompt_tokens_total": totals["input"] + totals["cache_write"],
        "cost": round(cost, 6),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
