"""Name every switched-on n8n workflow with no run on record.

The server prunes old runs, so "no run on record" is what it can say; it is not
proof a workflow never ran.
"""

import argparse
import contextlib
import importlib.util
import io

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def server_workflows(text: str) -> list[tuple[str, str, bool]] | None:
    """Parse the workflows list and return (name, id, active) sorted by name.

    Returns None if the text is not valid JSON or there is no top-level
    "data" list. Empty data is allowed and results in an empty list.
    """
    try:
        obj = json.loads(text)
    except Exception:
        return None

    if not isinstance(obj, dict) or "data" not in obj:
        return None

    data = obj.get("data")
    if not isinstance(data, list):
        return None

    results: list[tuple[str, str, bool]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        wid = item.get("id")
        active = bool(item.get("active", False))
        if name is None or wid is None:
            continue
        results.append((str(name), str(wid), active))

    results.sort(key=lambda t: t[0])
    return results


def last_started(text: str) -> str | None:
    """Return startedAt string for the first data item, or "" if empty, or None if invalid."""
    try:
        obj = json.loads(text)
    except Exception:
        return None

    if not isinstance(obj, dict) or "data" not in obj:
        return None

    data = obj.get("data")
    if not isinstance(data, list):
        return None

    if len(data) == 0:
        return ""
    first = data[0]
    if not isinstance(first, dict):
        return None
    started = first.get("startedAt")
    if started is None:
        return None
    return str(started)


def load_api(host: str = ""):
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("n8n_deploy", here / "n8n-deploy.py")
    if spec is None or spec.loader is None:
        return lambda path: (0, "cannot load n8n-deploy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    if host:
        mod.set_host(host)
    return mod.api


def self_test() -> int:
    """Self-test harness for the n8n-runs helpers without using the real server."""
    total_checks = 0
    passed_checks = 0

    def want(label: str, condition: bool):
        nonlocal total_checks, passed_checks
        total_checks += 1
        if condition:
            passed_checks += 1
        # Do not print per-check noise; only summarize at the end.

    # 1) server_workflows returns name, id, active for each entry
    t1 = json.dumps({"data": [
        {"id": "b2", "name": "Beta", "active": True},
        {"id": "a1", "name": "Alpha", "active": False},
    ]})
    w1 = server_workflows(t1)
    want("server_workflows returns 2 entries", w1 is not None and len(w1) == 2)
    if w1:
        want("server_workflows sorted by name", w1[0][0] == "Alpha" and w1[1][0] == "Beta")

    # 2) server_workflows sorts by name
    t1b = json.dumps({"data": [
        {"id": "x", "name": "Xoo", "active": True},
        {"id": "a", "name": "Aardvark", "active": True},
    ]})
    w1b = server_workflows(t1b)
    want("server_workflows sorts by name (Aardvark before Xoo)", w1b is not None and w1b[0][0] == "Aardvark" and w1b[1][0] == "Xoo")

    # 3) server_workflows returns None for non-JSON
    want("server_workflows None on invalid JSON", server_workflows("not json") is None)

    # 4) last_started returns first startedAt
    t4 = json.dumps({"data": [{"startedAt": "2026-10-04T12:00:00.000Z"}]})
    want("last_started returns first startedAt", last_started(t4) == "2026-10-04T12:00:00.000Z")

    # 5) last_started returns "" for empty data
    t5 = json.dumps({"data": []})
    want("last_started empty data returns empty string", last_started(t5) == "")

    # 6) last_started returns None for invalid JSON
    want("last_started invalid JSON returns None", last_started("not json") is None)

    # 7) main: all ON workflows have a run -> rc 0
    answers = {
        "/workflows?limit=250": (200, json.dumps({"data": [
            {"id": "a", "name": "Alpha", "active": True},
            {"id": "b", "name": "Beta", "active": False},
        ]})),
        "/executions?workflowId=a&limit=1": (200, json.dumps({"data": [{"startedAt": "2026-10-04T12:00:00.000Z"}], "nextCursor": None})),
        "/executions?workflowId=b&limit=1": (200, json.dumps({"data": [{"startedAt": "2026-10-04T11:00:00.000Z"}], "nextCursor": None})),
    }
    asked = []
    def fake(answers_in: dict[str, tuple[int, str]]):
        def api(path: str):
            asked.append(path)
            return answers_in.get(path, (404, "not found"))
        return api
    rc = main(argv=None, api=fake(answers))
    want("main all-on-run rc == 0", rc == 0)

    # 8) main: ON never ran -> rc 1
    answers2 = {
        "/workflows?limit=250": (200, json.dumps({"data": [
            {"id": "a", "name": "Alpha", "active": True},
            {"id": "b", "name": "Beta", "active": False},
        ]})),
        "/executions?workflowId=a&limit=1": (200, json.dumps({"data": [], "nextCursor": None})),
        "/executions?workflowId=b&limit=1": (200, json.dumps({"data": [], "nextCursor": None})),
    }
    asked2 = []
    rc2 = main(argv=None, api=fake(answers2))
    want("main-on-never-ran rc == 1", rc2 == 1)

    # 9) main: only off never ran -> rc 0
    answers3 = {
        "/workflows?limit=250": (200, json.dumps({"data": [
            {"id": "a", "name": "Alpha", "active": False},
            {"id": "b", "name": "Beta", "active": True},
        ]})),
        "/executions?workflowId=a&limit=1": (200, json.dumps({"data": [], "nextCursor": None})),
        "/executions?workflowId=b&limit=1": (200, json.dumps({"data": [{"startedAt": "2026-10-04T11:00:00.000Z"}], "nextCursor": None})),
    }
    rc3 = main(argv=None, api=fake(answers3))
    want("main-off-then-on-run rc == 0", rc3 == 0)

    # 10) main: list status not 200 -> rc 3
    answers4 = {
        "/workflows?limit=250": (0, "bad"),
    }
    rc4 = main(argv=None, api=fake(answers4))
    want("main-bad-workflow-list rc == 3", rc4 == 3)

    # 11) main: one workflow's executions status 500 -> rc 3
    answers5 = {
        "/workflows?limit=250": (200, json.dumps({"data": [
            {"id": "a", "name": "Alpha", "active": True},
        ]})),
        "/executions?workflowId=a&limit=1": (500, "server error"),
    }
    rc5 = main(argv=None, api=fake(answers5))
    want("main-bad-exec-status rc == 3", rc5 == 3)

    # 12) verify asked for executes by id
    answers6 = {
        "/workflows?limit=250": (200, json.dumps({"data": [
            {"id": "abc", "name": "Cap", "active": True},
        ]})),
        "/executions?workflowId=abc&limit=1": (200, json.dumps({"data": [{"startedAt": "2026-10-04T12:00:00.000Z"}], "nextCursor": None})),
    }
    asked6 = []
    def api6(path: str):
        asked6.append(path)
        return answers6.get(path, (404, "not found"))
    rc6 = main(argv=None, api=api6)
    want("main-asked-run-path contains executions path", any(p.endswith("executions?workflowId=abc&limit=1") for p in asked6))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(argv=None, api=fake(answers2))
    want("a workflow with no runs reads NO RUN ON RECORD, never NEVER RAN",
         "NO RUN ON RECORD Alpha (on)" in out.getvalue()
         and "NEVER RAN" not in out.getvalue())

    print(f"n8n-runs self-test: {passed_checks}/{total_checks} checks passed")
    return 0 if passed_checks == total_checks else 1


def main(argv: list[str] | None = None, api=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--host", default="")
    # A caller that injects `api` is a test: it never reads the real argv.
    args = parser.parse_args([] if api is not None and argv is None else argv)

    if args.self_test:
        return self_test()

    api = api or load_api(args.host)
    status, text = api("/workflows?limit=250")
    if status != 200:
        print(
            f"n8n-runs: CANNOT ASK the automation server ({status}: {text[:200]})"
        )
        return 3

    workflows = server_workflows(text)
    if workflows is None:
        print("n8n-runs: CANNOT READ the workflow list")
        return 3

    never_ran_on = False
    for name, wid, active in workflows:
        s, t = api(f"/executions?workflowId={wid}&limit=1")
        if s != 200:
            print(
                f"n8n-runs: CANNOT ASK when {name} last ran ({s})"
            )
            return 3
        when = last_started(t)
        if when is None:
            print(f"n8n-runs: CANNOT READ when {name} last ran")
            return 3
        state = "on" if active else "off"
        if when == "":
            print(f"n8n-runs: NO RUN ON RECORD {name} ({state})")
        else:
            print(f"n8n-runs: last ran {when} {name} ({state})")
        if active and when == "":
            never_ran_on = True

    return 1 if never_ran_on else 0


if __name__ == "__main__":
    sys.exit(main())
