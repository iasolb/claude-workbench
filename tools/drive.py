#!/usr/bin/env python3
"""Send one task to a running opencode server and report its usage.

The server is expected on localhost. Set OPENCODE_SERVER_PASSWORD in the
environment; it is never printed. Workbench settings are read by workbench.py.
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
import workbench

LOCALHOST = "localhost"


def resolve_password() -> str | None:
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


def tally(messages: list[dict]) -> tuple[dict[str, int], float, str | None]:
    """Return assistant token totals, reported cost, and any model error."""
    totals = {key: 0 for key in ("input", "output", "reasoning", "cache_read", "cache_write")}
    cost = 0.0
    model_error = None
    for entry in messages:
        info = entry.get("info", {}) or {}
        if info.get("role") != "assistant":
            continue
        if info.get("error"):
            model_error = json.dumps(info["error"])[:400]
        tokens = info.get("tokens", {}) or {}
        cache = tokens.get("cache", {}) or {}
        totals["input"] += int(tokens.get("input") or 0)
        totals["output"] += int(tokens.get("output") or 0)
        totals["reasoning"] += int(tokens.get("reasoning") or 0)
        totals["cache_read"] += int(cache.get("read") or 0)
        totals["cache_write"] += int(cache.get("write") or 0)
        cost += float(info.get("cost") or 0.0)
    return totals, cost, model_error


def never_reached_model(totals: dict[str, int], cost: float) -> bool:
    """Detect a run with no usage and no reported cost."""
    return cost == 0.0 and totals.get("input", 0) == 0 and totals.get("cache_write", 0) == 0


def self_test() -> int:
    failures: list[str] = []
    checks = 0

    def want(label: str, condition: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    sample = [{"info": {"role": "assistant", "cost": 0.5,
                        "tokens": {"input": 3, "output": 5,
                                   "cache": {"read": 2, "write": 7}}}}]
    totals, cost, error = tally(sample)
    want("cache-write tokens are included", totals["cache_write"] == 7)
    want("prompt tokens sum input and cache-write", totals["input"] + totals["cache_write"] == 10)
    want("reported cost is returned", cost == 0.5)
    want("a clean response has no error", error is None)
    mixed = [{"info": {"role": "user", "tokens": {"input": 90}}}, sample[0]]
    want("user tokens are excluded", tally(mixed)[0]["input"] == 3)
    want("missing usage fields safely read as zero", tally([{}])[1] == 0.0)
    want("an assistant error is retained", tally([{"info": {"role": "assistant",
                                                                "error": {"name": "SampleError"}}}])[2]
         is not None)
    want("zero usage is distinguished", never_reached_model({"input": 0, "cache_write": 0}, 0.0))
    want("a zero-cost run with tokens is valid",
         not never_reached_model({"input": 5, "cache_write": 0}, 0.0))
    print(f"drive self-test: {checks - len(failures)}/{checks} assertions passed")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--dir", default=str(workbench.repo_root()))
    parser.add_argument("--model", default="opencode/gpt-5-nano")
    parser.add_argument("--agent", default="build")
    parser.add_argument("--title")
    parser.add_argument("--cap", type=int, default=900)
    args = parser.parse_args()
    provider, separator, model_id = args.model.partition("/")
    if not separator or not model_id:
        print("model must use provider/model format")
        return 2
    password = resolve_password()
    if not password:
        print("no server password found; set OPENCODE_SERVER_PASSWORD")
        return 2

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

    title = args.title or ("DRIVE " + time.strftime("%H:%M:%S"))
    try:
        directory = urllib.parse.quote(args.dir, safe="")
        session = call("POST", "/session?directory=" + directory, {"title": title})
        session_id = session["id"]
    except Exception as exc:
        print(f"HARNESS FAILURE: could not create session: {exc}")
        return 2

    outcome: dict = {}

    def send() -> None:
        try:
            outcome["response"] = call(
                "POST", f"/session/{session_id}/message",
                {"model": {"providerID": provider, "modelID": model_id},
                 "agent": args.agent, "parts": [{"type": "text", "text": args.task}]},
                timeout=args.cap + 120)
        except Exception as exc:
            outcome["error"] = str(exc)

    started = time.time()
    worker = threading.Thread(target=send, daemon=True)
    worker.start()
    worker.join(args.cap)
    timed_out = worker.is_alive()
    if timed_out:
        try:
            call("POST", f"/session/{session_id}/abort", timeout=30)
        except Exception:
            pass
    wall = round(time.time() - started, 1)
    totals = {key: 0 for key in ("input", "output", "reasoning", "cache_read", "cache_write")}
    cost = 0.0
    model_error = None
    try:
        totals, cost, model_error = tally(call("GET", f"/session/{session_id}/message"))
    except Exception as exc:
        print(f"could not read messages back: {exc}")
    result = {
        "session_id": session_id, "model": args.model, "agent": args.agent,
        "wall_seconds": wall, "timed_out": timed_out,
        "error": outcome.get("error"), "model_error": model_error,
        "cost_reported_by_api": round(cost, 6),
        "prompt_tokens_total": totals["input"] + totals["cache_write"], **totals,
    }
    print(json.dumps(result, indent=2))
    if never_reached_model(totals, cost):
        print("WARNING: zero tokens and zero cost; the request may not have reached the model.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
