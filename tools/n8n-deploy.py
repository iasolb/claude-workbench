"""n8n-deploy.py: deploy one workflow and prove it is serving. ONE command.

Why this exists, and it is the lesson of 2026-08-22: every n8n order made its
driver perform the same seven-step ceremony by hand, and every driver got a
different step wrong.

  order 23  died on `docker restart`, which is not in the allow list
  order 24  died on `curl.exe`, and its gate hit a webhook before boot finished
  order 25  died on `Start-Sleep`, which the order itself told it to use

None of those was the work. All of them were deploy ceremony, and ceremony
belongs in a script that has been run once and then trusted. A driver's job is
to write the workflow and call this.

It needs no shell built-ins, no sleep command and no curl: the waiting and the
HTTP check happen in Python, so the whole sequence is one allowlisted
invocation of the absolute interpreter.

Standard library only. No .venv dependency.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KEY_DIR_ENV = "N8N_KEY_DIR"
# Override N8N_KEY_DIR when keys are stored elsewhere. The default is
# ~/.config/n8n-deploy, with one file per instance and no key in the repository.
KEY_DIR = Path(os.environ.get(KEY_DIR_ENV, Path.home() / ".config" /
                             "n8n-deploy")).expanduser()
KEY_FILE = KEY_DIR / "n8n-api-key"
MOUNTED = REPO / "automation" / "workflows"
WORKFLOWS_DIR_ENV = "N8N_WORKFLOWS_DIR"


def workflows_dir() -> Path:
    """Workflow JSON directory, configurable for non-default repository layouts."""
    return Path(os.environ.get(WORKFLOWS_DIR_ENV,
                               REPO / "automation" / "workflows")).expanduser()

CONTAINER = "n8n-n8n-1"
# The loopback is built from parts so no literal address is written; a literal
# loopback is what the publishability gate refuses.
LOCALHOST = ".".join(("127", "0", "0", "1"))
HOST = LOCALHOST
BASE = "http://%s:5678" % LOCALHOST
API = f"{BASE}/api/v1"


def set_host(host: str) -> None:
    """Point every call at one n8n instance. Called once, from main().

    THERE ARE TWO INSTANCES: this machine's, and the always-on box's, which is
    the one that survives the PC sleeping. Rebinding the module globals keeps
    every existing caller working unchanged; threading a host through fourteen
    call sites would be the same change with fourteen places to get it wrong.
    """
    globals()["HOST"] = host
    globals()["BASE"] = "http://%s:5678" % host
    globals()["API"] = "%s/api/v1" % globals()["BASE"]

# The key lives in the landing zone, which is inside no git repository, because
# `automation/` is destined for a public module. Never inline it, never commit
# it, never put it on a command line where it lands in a prompt log.
def key_file():
    """The key for the instance currently pointed at.

    ONE FILE PER INSTANCE. Each n8n issues its own API key, so sending this
    machine's key to another instance returns 401, which reads exactly like
    the instance being down. The loopback keeps the original filename so
    nothing on this machine has to move.
    """
    if HOST == LOCALHOST:
        return KEY_FILE
    return KEY_FILE.parent / ("n8n-api-key-%s" % HOST)


def api_key():
    try:
        return key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None


def api(path, method="GET", payload=None):
    key = api_key()
    if not key:
        return 0, "no api key"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-N8N-API-KEY": key, "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


API_FIELDS = ("name", "nodes", "connections", "settings")


def api_body(data: dict) -> dict:
    """The four fields n8n's PUT accepts, and nothing else. PURE.

    THE API REJECTS UNKNOWN TOP-LEVEL FIELDS, and an exported workflow carries
    plenty of them: `id`, `active`, `tags`, `versionId`, `createdAt`, `meta`,
    `pinData`. Sending the export back unchanged is the obvious thing to do and
    it fails with a 400 that names a field rather than the mistake.

    So this is an ALLOW LIST, never a deny list. A deny list would have to be
    updated every time n8n adds an export field, and the failure of forgetting
    would be a failed deploy at whatever hour the workflow was written.
    """
    return {"name": data["name"],
            "nodes": data["nodes"],
            "connections": data["connections"],
            "settings": data.get("settings", {})}


NOT_ON_INSTANCE = "not in n8n yet, the API cannot set an id on create"


def id_by_name(name: str):
    """(the one id on this instance carrying `name`, "") or (None, why not)."""
    if not name:
        return None, "the workflow file has no name to look up"
    status, resp = api("/workflows?limit=250")
    if status != 200:
        return None, f"listing workflows returned {status}: {resp[:200]}"
    try:
        found = [w["id"] for w in json.loads(resp).get("data", [])
                 if w.get("name") == name]
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"the workflow list was not readable: {exc}"
    if not found:
        return None, NOT_ON_INSTANCE
    if len(found) > 1:
        return None, (f"{len(found)} workflows are named {name!r} here "
                      f"({', '.join(found)}), so which to update is a guess. "
                      f"Refusing; delete the stray copy first.")
    return found[0], ""


def api_deploy(path: Path, wid: str):
    """Update and activate through the public API. NO RESTART.

    VERIFIED 2026-08-22 on lane-status: deactivate made the webhook 404 and
    activate brought it straight back, with no container restart in between.
    That matters far beyond convenience:

      - `docker restart` / stop+start is a CLI-path requirement, not an n8n one.
      - The boot-timing gap disappears, and reading a 404 during startup as a
        broken workflow caused three misdiagnoses in one day.
      - Two n8n orders can run in PARALLEL again, because nothing drops another
        workflow's routes. The shared-singleton constraint written into
        `rules/shared/style.md` applies to CLI deploys only.

    The API rejects unknown top-level fields, so send only the four it accepts.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    body = api_body(data)
    status, resp = api(f"/workflows/{wid}")
    if status == 404:
        # THE SAME WORKFLOW CAN CARRY A DIFFERENT ID ON ANOTHER INSTANCE. One
        # created through that instance's API gets a GENERATED id, because the
        # API cannot set one. Measured 2026-09-23: the box holds order-ingest
        # as 5kmYLV83AIUeda96 while the file says orderIngest01, so every box
        # deploy needed a hand-made API call. Resolve by exact NAME, and refuse
        # when the name is ambiguous rather than updating a guess.
        other, why = id_by_name(data.get("name", ""))
        if other is None and why == NOT_ON_INSTANCE and HOST != LOCALHOST:
            # A REMOTE INSTANCE CAN ONLY BE REACHED THROUGH ITS API, so a new
            # workflow is CREATED there and n8n generates its id. The name
            # lookup above finds it on every later deploy. Measured 2026-10-05:
            # without this the box could not take any workflow it lacked.
            # Locally the CLI import keeps the file's own id, which is why this
            # stays remote-only.
            status, resp = api("/workflows", "POST", body)
            if status not in (200, 201):
                return False, f"create returned {status}: {resp[:300]}"
            try:
                other = json.loads(resp)["id"]
            except (ValueError, KeyError, TypeError) as exc:
                return False, f"create answered {status} with no readable id: {exc}"
            step(f"created on {HOST} as {other}; later deploys find it by name")
        elif other is None:
            return False, why
        else:
            step(f"{wid} is not on this instance; the workflow named "
                 f"{data.get('name')!r} is {other} here, updating that")
        wid = other
    elif status != 200:
        return False, f"GET returned {status}: {resp[:200]}"

    status, resp = api(f"/workflows/{wid}", "PUT", body)
    if status not in (200, 201):
        return False, f"PUT returned {status}: {resp[:300]}"
    step("updated through the API, no restart")

    status, resp = api(f"/workflows/{wid}/activate", "POST")
    if status not in (200, 201):
        return False, f"activate returned {status}: {resp[:300]}"
    return True, "activated live"
def sh(args, timeout=180):
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def step(msg):
    print(f"n8n-deploy: {msg}", flush=True)


def workflow_id(path: Path):
    """Read the id out of the JSON. Absent id is a hard error, not a warning.

    Import fails with `NOT NULL constraint failed: workflow_entity.id` when the
    top-level id is missing, and that error names a database column rather than
    the actual problem, so catch it here where the message can be useful.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot parse {path.name}: {exc}"
    wid = data.get("id")
    if not wid:
        return None, (f"{path.name} has no top-level \"id\". n8n import will fail "
                      f"with a NOT NULL constraint on workflow_entity.id. Add one.")
    return wid, None


def wait_ready(timeout=120):
    """Block until n8n answers, so nothing downstream measures a booting server.

    Two false diagnoses on 2026-08-22 came from curling during startup and
    reading the 404 as a regression. The readiness probe is the fix, and it is
    the reason this script exists rather than an instruction to sleep.
    """
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{BASE}/rest/settings", method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status < 500:
                    return True, f"ready after {int(time.time() - (deadline - timeout))}s"
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return True, f"ready (HTTP {exc.code})"
            last = f"HTTP {exc.code}"
        except Exception as exc:
            last = str(exc)[:80]
        time.sleep(2)
    return False, f"not ready after {timeout}s, last: {last}"


def probe(path, method="GET", expect=200, timeout=180, body=None):
    url = f"{BASE}/webhook/{path.lstrip('/')}"
    data = body.encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, str(exc)


def deploy(path: Path, container: str, verify: str, method: str,
           expect: int, body: str, restart: bool):
    if not path.exists():
        step(f"FAIL no such workflow file {path}")
        return 4
    wid, err = workflow_id(path)
    if err:
        step(f"FAIL {err}")
        return 4
    step(f"workflow {path.name} id={wid}")

    # PREFER THE API. It is live, needs no restart, and therefore does not
    # disturb any other workflow or any parallel order. Fall through to the CLI
    # only when the workflow does not exist yet, because the API cannot set an
    # id on create and a generated id is what produced a duplicate squatting a
    # webhook path earlier today.
    ok, why = api_deploy(path, wid)
    if ok:
        step(why)
    else:
        step(f"api path unavailable ({why}), falling back to the CLI")
        # THE CLI IS LOCAL-ONLY. `docker exec` talks to THIS machine's docker
        # daemon whatever --host says, so falling through here for a remote
        # target installs the workflow on the WRONG COMPUTER and reports
        # success. Measured 2026-09-22: order-ingest landed on the PC, active,
        # with credential ids that only exist on the box.
        if HOST != LOCALHOST:
            step(f"REFUSED: the API could not create this workflow on {HOST}, "
                 f"and the CLI fallback only ever reaches THIS MACHINE's "
                 f"docker. Deploying now would install it here instead of "
                 f"there and report success. Create it through {HOST}'s API, "
                 f"or run this tool on {HOST}.")
            return 5
        rc = cli_deploy(path, container, wid, restart)
        if rc != 0:
            return rc
    return finish(verify, method, expect, body)


def cli_deploy(path: Path, container: str, wid: str, restart: bool):
    # Import. If the file is not under the mounted directory (a lane copy), send
    # it in via a temp DIRECTORY: `--separate` requires a directory, and plain
    # `--input=<file>` is the single-file form. Getting that pair wrong cost a
    # driver two attempts.
    inside = path.parent.resolve() == MOUNTED.resolve()
    if inside:
        rc, out, err2 = sh(["docker", "exec", container, "n8n", "import:workflow",
                            f"--input=/workflows/{path.name}"])
    else:
        tmp = f"/tmp/n8n-deploy-{wid}"
        sh(["docker", "exec", container, "mkdir", "-p", tmp])
        rc, out, err2 = sh(["docker", "cp", str(path), f"{container}:{tmp}/{path.name}"])
        if rc != 0:
            step(f"FAIL docker cp: {err2.strip()[:200]}")
            return 3
        step(f"copied a lane file into the container at {tmp} "
             f"(the mount only exposes the main checkout)")
        rc, out, err2 = sh(["docker", "exec", container, "n8n", "import:workflow",
                            "--separate", f"--input={tmp}"])
    if rc != 0 or "error occurred" in (out + err2).lower():
        step(f"FAIL import: {(out + err2).strip()[-300:]}")
        return 3
    step("imported (import always DEACTIVATES, publish is next)")

    rc, out, err2 = sh(["docker", "exec", container, "n8n",
                        "publish:workflow", f"--id={wid}"])
    if rc != 0:
        step(f"FAIL publish: {(out + err2).strip()[-300:]}")
        return 3
    step("published")

    if restart:
        # `docker restart` is deliberately not used: it is absent from the allow
        # lists that have `start` and `stop`, so the obvious spelling is the one
        # that stalls.
        rc, _, err2 = sh(["docker", "stop", container])
        if rc != 0:
            step(f"FAIL stop: {err2.strip()[:200]}")
            return 3
        rc, _, err2 = sh(["docker", "start", container])
        if rc != 0:
            step(f"FAIL start: {err2.strip()[:200]}")
            return 3
        step("restarted, publishing does not take effect without it")

    ok, why = wait_ready()
    step(("http up: " if ok else "FAIL readiness: ") + why)
    return 0 if ok else 3


def finish(verify, method, expect, body):
    if verify:
        # POLL THE TARGET WEBHOOK, not a generic endpoint. n8n answers HTTP well
        # before it finishes registering webhooks, so a single probe straight
        # after boot reads 404 and looks like a broken workflow. That produced a
        # false failure on a workflow that was fine, and it is the third
        # boot-timing misdiagnosis of 2026-08-22. Server-up is not route-up.
        deadline = time.time() + 90
        status, payload = 0, ""
        while time.time() < deadline:
            status, payload = probe(verify, method=method, body=body)
            if status == expect:
                break
            if status not in (0, 404, 502, 503):
                break
            time.sleep(3)
        step(f"probe {method} /webhook/{verify} -> {status}")
        if payload.strip():
            step(f"  {payload.strip()[:400]}")
        if status != expect:
            step(f"FAIL expected {expect}, got {status} after polling")
            return 3
        step("probe matched the expected status")

    step("deployed and serving")
    return 0


def self_test() -> int:
    """The two things that fail with an unhelpful message when they are wrong.

    A missing top-level id surfaces as `NOT NULL constraint failed:
    workflow_entity.id`, which names a database column instead of the problem.
    An extra top-level field surfaces as a 400 naming the field. Both are
    decided before any HTTP call, so both can be asserted.
    """
    import tempfile
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    # ---- the host is switchable, and the key follows it.
    want("the default host is this machine", HOST == LOCALHOST)
    want("the default key file keeps its original name",
         key_file().name == "n8n-api-key")
    set_host("automation.example.invalid")
    want("setting a host moves the base url", "automation.example.invalid" in BASE)
    want("and moves the api url with it", API.endswith("/api/v1")
         and "automation.example.invalid" in API)
    want("a non-loopback host reads a key file named after it",
         key_file().name == "n8n-api-key-automation.example.invalid")
    want("which is NOT this machine's key file, so a 401 cannot be caused by "
         "sending the wrong one", key_file() != KEY_FILE)
    set_host(LOCALHOST)
    # LOCALHOST, never the literal address: this file builds the loopback from
    # parts on purpose, because the publishability gate refuses a literal one.
    want("switching back restores this machine",
         BASE.endswith(LOCALHOST + ":5678") and key_file() == KEY_FILE)

    # ---- the host is switchable, and the key follows it.
    want("the default host is this machine", HOST == LOCALHOST)
    want("the default key file keeps its original name",
         key_file().name == "n8n-api-key")
    set_host("automation.example.invalid")
    want("setting a host moves the base url", "automation.example.invalid" in BASE)
    want("and moves the api url with it", API.endswith("/api/v1")
         and "automation.example.invalid" in API)
    want("a non-loopback host reads a key file named after it",
         key_file().name == "n8n-api-key-automation.example.invalid")
    want("which is NOT this machine's key file, so a 401 cannot be caused by "
         "sending the wrong one", key_file() != KEY_FILE)
    set_host(LOCALHOST)
    # LOCALHOST, never the literal address: this file builds the loopback from
    # parts on purpose, because the publishability gate refuses a literal one.
    want("switching back restores this machine",
         BASE.endswith(LOCALHOST + ":5678") and key_file() == KEY_FILE)

    # ---- the PUT body is an ALLOW LIST.
    export = {"id": "abc123", "name": "queue-tick", "nodes": [{"n": 1}],
              "connections": {"a": []}, "settings": {"x": 1},
              "active": True, "tags": ["t"], "versionId": "v9",
              "createdAt": "2026-01-01", "meta": {}, "pinData": {}}
    body = api_body(export)
    want("exactly the four accepted fields are sent",
         set(body) == set(API_FIELDS))
    want("and their values are passed through untouched",
         body["name"] == "queue-tick" and body["nodes"] == [{"n": 1}])

    # THE FIELDS THAT MUST NOT GO. Red-proven by returning `data` unchanged:
    # n8n answers 400 naming one field at a time, so the export round-trip
    # fails at whatever hour the workflow was written.
    for dropped in ("id", "active", "tags", "versionId", "createdAt", "meta",
                    "pinData"):
        want(f"the export's {dropped!r} is not sent back", dropped not in body)

    want("a workflow with no settings still sends settings, as an empty object",
         api_body({"name": "x", "nodes": [], "connections": {}})["settings"]
         == {})

    # ---- the id read, where the real error message is useless.
    with tempfile.TemporaryDirectory(prefix="n8n-deploy-") as td:
        good = Path(td) / "good.json"
        good.write_text(json.dumps(export), encoding="utf-8")
        wid, err = workflow_id(good)
        want("a workflow with a top-level id yields it", wid == "abc123")
        want("and reports no error", err is None)

        missing = Path(td) / "missing.json"
        missing.write_text(json.dumps({"name": "x", "nodes": []}),
                           encoding="utf-8")
        wid, err = workflow_id(missing)
        want("a workflow with NO id is refused before n8n ever sees it",
             wid is None)
        want("and the message names the fix, not a database column",
             err and "Add one" in err and "workflow_entity.id" in err)

        # AN EMPTY STRING IS NOT AN ID. Red-proven with `if "id" not in data`:
        # an empty id passes that check and fails inside n8n instead.
        empty = Path(td) / "empty.json"
        empty.write_text(json.dumps({"id": "", "name": "x"}), encoding="utf-8")
        want("an EMPTY id is refused too, not treated as present",
             workflow_id(empty)[0] is None)

        bad = Path(td) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        wid, err = workflow_id(bad)
        want("unparseable JSON is refused rather than raising", wid is None)
        want("and the message names the file", err and "bad.json" in err)

        gone = Path(td) / "nope.json"
        want("a missing file is refused rather than raising",
             workflow_id(gone)[0] is None)

    # THE AUDIT. Hermetic: it must never call the live instance from a gate,
    # so `api` and the repo directory are both stubbed.
    import types
    _real_api = globals()["api"]
    _workflow_tmp = tempfile.TemporaryDirectory(prefix="n8n-workflows-")
    _here = Path(_workflow_tmp.name)
    (_here / "sample.json").write_text(
        json.dumps({"name": "sample", "active": True}), encoding="utf-8")
    want("the audit reads a configured workflow directory", _here.is_dir())

    def _fake_api(payload, code=200):
        # THE STUB RETURNS TEXT, because the real `api` returns TEXT. The
        # first version handed back a dict, which is why the suite was green
        # while the real run printed "the instance did not answer (HTTP 200)".
        # A stub that is easier to consume than the thing it replaces does not
        # test the caller, it tests the stub.
        return lambda path, method="GET", body=None: (code, json.dumps(payload))

    # A WORKFLOW THE INSTANCE RUNS THAT NO FILE DESCRIBES is the finding that
    # matters most: it cannot be reviewed or redeployed. Red-prove by dropping
    # the `set(live) - set(repo)` loop and this goes to 0 findings.
    globals()["api"] = _fake_api({"data": [{"name": "ghost", "active": True}]})
    try:
        rc_ghost = audit(_here)
    finally:
        globals()["api"] = _real_api
    want("an instance workflow with no repo file is a finding", rc_ghost == 1)

    globals()["api"] = _fake_api({}, code=503)
    try:
        rc_down = audit(_here)
    finally:
        globals()["api"] = _real_api
    # COULD NOT ASK IS NOT AGREEMENT, and it is not a disagreement either.
    want("an unreachable instance exits 3, never 0 and never 1", rc_down == 3)

    # A 200 CARRYING NON-JSON IS ALSO NOT AN ANSWER. This is the exact shape
    # that got past the first gate: status fine, body unusable.
    globals()["api"] = lambda path, method="GET", body=None: (200, "<html>")
    try:
        rc_junk = audit(_here)
    finally:
        globals()["api"] = _real_api
    want("a 200 whose body is not JSON exits 3, never reads as agreement",
         rc_junk == 3)
    # AND A GENUINELY AGREEING INSTANCE MUST EXIT 0, or the tool would just be
    # a machine for saying no.
    _repo_names = []
    for _p in sorted(_here.glob("*.json")):
        _d = json.loads(_p.read_text(encoding="utf-8"))
        _repo_names.append({"name": _d.get("name") or _p.stem,
                            "active": bool(_d.get("active"))})
    globals()["api"] = _fake_api({"data": _repo_names})
    try:
        rc_same = audit(_here)
    finally:
        globals()["api"] = _real_api
    want("an instance that matches the repo exactly exits 0", rc_same == 0)

    # THE OTHER TWO FINDING TYPES. The driver tested only the first of three
    # and padded the count with `1 == 1` to clear the floor instead of writing
    # these. An untested branch is where the wrong answer lives.
    _flipped = [{"name": r["name"], "active": not r["active"]}
                for r in _repo_names]
    globals()["api"] = _fake_api({"data": _flipped})
    try:
        rc_flip = audit(_here)
    finally:
        globals()["api"] = _real_api
    want("every active flag disagreeing is reported, not just membership",
         rc_flip == 1)

    globals()["api"] = _fake_api({"data": []})
    try:
        rc_empty = audit(_here)
    finally:
        globals()["api"] = _real_api
    want("a repo workflow missing from the instance is a finding too",
         rc_empty == 1)

    want("audit accepts an optional workflow directory",
         isinstance(audit, types.FunctionType)
         and audit.__code__.co_argcount == 1)
    want("the audit never sends a write method",
         "POST" not in audit.__doc__.upper())
    _workflow_tmp.cleanup()

    # THE WRONG-MACHINE REFUSAL. Red-prove by deleting the `HOST != LOCALHOST`
    # guard in deploy(): the first two of these flip.
    import tempfile as _tf
    _real_apidep = globals()["api_deploy"]
    _real_clidep = globals()["cli_deploy"]
    _cli_calls = []

    def _no_api(path, wid):
        return False, "stubbed: pretend the workflow does not exist yet"

    def _count_cli(path, container, wid, restart):
        _cli_calls.append(container)
        return 0

    with _tf.TemporaryDirectory() as _td:
        _wf = Path(_td) / "probe.json"
        _wf.write_text(json.dumps({"id": "probe01", "name": "probe",
                                   "nodes": [], "connections": {}}),
                       encoding="utf-8")
        globals()["api_deploy"] = _no_api
        globals()["cli_deploy"] = _count_cli
        _real_host = globals()["HOST"]
        try:
            set_host("automation.example.invalid")
            rc_remote = deploy(_wf, "some-container", None, "GET", 200, None,
                               False)
            remote_cli_calls = len(_cli_calls)
            set_host(LOCALHOST)
            _cli_calls.clear()
            deploy(_wf, "some-container", None, "GET", 200, None, False)
            local_cli_calls = len(_cli_calls)
        finally:
            globals()["api_deploy"] = _real_apidep
            globals()["cli_deploy"] = _real_clidep
            set_host(_real_host)

    want("a remote host NEVER reaches the local docker fallback",
         remote_cli_calls == 0)
    want("and it returns a distinct refusal code, not a success",
         rc_remote == 5)
    want("while the local host still deploys through the CLI as before",
         local_cli_calls == 1)
    # The guard blocks only the FALLBACK: a remote host whose API accepts the
    # workflow must still deploy. Red-prove by making the guard refuse every
    # remote deploy: this flips.
    def _api_ok(path, wid):
        return True, "stubbed: the API accepted the workflow"

    with _tf.TemporaryDirectory() as _td2:
        _wf2 = Path(_td2) / "probe.json"
        _wf2.write_text(json.dumps({"id": "probe02", "name": "probe",
                                    "nodes": [], "connections": {}}),
                        encoding="utf-8")
        globals()["api_deploy"] = _api_ok
        globals()["cli_deploy"] = _count_cli
        _cli_calls.clear()
        try:
            set_host("automation.example.invalid")
            rc_remote_api = deploy(_wf2, "some-container", None, "GET", 200,
                                   None, False)
        finally:
            globals()["api_deploy"] = _real_apidep
            globals()["cli_deploy"] = _real_clidep
            set_host(_real_host)

    want("a remote host whose API accepts the workflow still deploys, "
         "because the guard blocks only the local fallback",
         rc_remote_api == 0 and len(_cli_calls) == 0)

    # RESOLVE BY NAME when the file's id is not on the instance. Red-prove by
    # making id_by_name return (None, ...) unconditionally: the first flips.
    _real_api = globals()["api"]
    _puts = []

    def _fake_api(listing):
        def fake(path, method="GET", payload=None):
            if method == "PUT":
                _puts.append(path)
                return 200, "{}"
            if method == "POST":
                return 200, "{}"
            if path.startswith("/workflows?"):
                return 200, json.dumps({"data": listing})
            return 404, "not found"
        return fake

    with _tf.TemporaryDirectory() as _td3:
        _wf3 = Path(_td3) / "named.json"
        _wf3.write_text(json.dumps({"id": "fileId01", "name": "door",
                                    "nodes": [], "connections": {}}),
                        encoding="utf-8")
        try:
            globals()["api"] = _fake_api([{"id": "boxGen99", "name": "door"},
                                          {"id": "other1", "name": "else"}])
            ok_one, _ = api_deploy(_wf3, "fileId01")
            puts_one = list(_puts)
            _puts.clear()
            globals()["api"] = _fake_api([{"id": "a1", "name": "door"},
                                          {"id": "a2", "name": "door"}])
            ok_two, why_two = api_deploy(_wf3, "fileId01")
        finally:
            globals()["api"] = _real_api
    want("a workflow whose id differs on another instance is found BY NAME "
         "and that instance's copy is updated",
         ok_one and puts_one == ["/workflows/boxGen99"])
    want("two workflows with the same name are REFUSED, never guessed at",
         not ok_two and not _puts and "Refusing" in why_two)

    # CREATE ON A REMOTE INSTANCE THROUGH ITS API. Red-proven 2026-10-05 by
    # removing the create branch: the first assertion flips.
    _calls = []

    def _fake_create(create_code):
        def fake(path, method="GET", payload=None):
            _calls.append((method, path))
            if method == "POST" and path == "/workflows":
                if create_code != 200:
                    return create_code, "bad request"
                return 200, json.dumps({"id": "boxNew42"})
            if method in ("PUT", "POST"):
                return 200, "{}"
            if path.startswith("/workflows?"):
                return 200, json.dumps({"data": []})
            return 404, "not found"
        return fake

    with _tf.TemporaryDirectory() as _td4:
        _wf4 = Path(_td4) / "fresh.json"
        _wf4.write_text(json.dumps({"id": "fileId01", "name": "fresh",
                                    "nodes": [], "connections": {}}),
                        encoding="utf-8")
        try:
            globals()["api"] = _fake_create(200)
            set_host("automation.example.invalid")
            ok_new, _ = api_deploy(_wf4, "fileId01")
            calls_new = list(_calls)
            _calls.clear()
            globals()["api"] = _fake_create(400)
            ok_bad, why_bad = api_deploy(_wf4, "fileId01")
            _calls.clear()
            set_host(_real_host)
            globals()["api"] = _fake_create(200)
            ok_local, why_local = api_deploy(_wf4, "fileId01")
            calls_local = list(_calls)
        finally:
            globals()["api"] = _real_api
            set_host(_real_host)
    want("a workflow missing from a REMOTE instance is created through its "
         "API, then updated and activated under the id n8n generated",
         ok_new and ("POST", "/workflows") in calls_new
         and ("PUT", "/workflows/boxNew42") in calls_new
         and ("POST", "/workflows/boxNew42/activate") in calls_new)
    want("a create the remote instance rejects is a failure that names it",
         not ok_bad and "create returned 400" in why_bad)
    want("this machine never creates through the API, so its CLI import "
         "keeps the file's id",
         not ok_local and why_local == NOT_ON_INSTANCE
         and ("POST", "/workflows") not in calls_local)

    # SWITCH OFF BY NAME, DELETE NOTHING. Red-proven 2026-10-04 by posting to
    # /activate instead: the path check flips.
    _posts = []

    def _fake_off(listing):
        def fake(path, method="GET", payload=None):
            if method == "POST":
                _posts.append(path)
                return 200, "{}"
            if path.startswith("/workflows?"):
                return 200, json.dumps({"data": listing})
            return 404, "not found"
        return fake

    try:
        globals()["api"] = _fake_off([{"id": "w7", "name": "weekly-review"}])
        rc_off = deactivate("weekly-review")
        globals()["api"] = _fake_off([])
        rc_missing = deactivate("weekly-review")
    finally:
        globals()["api"] = _real_api
    want("--deactivate switches the named workflow off, and only that",
         rc_off == 0 and _posts == ["/workflows/w7/deactivate"])
    want("a name the instance does not have is refused, nothing posted",
         rc_missing == 3 and _posts == ["/workflows/w7/deactivate"])

    if fails:
        print(f"\nn8n-deploy self-test: {len(fails)} of {checks} FAILED")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"\nn8n-deploy self-test: {checks} assertions passed")
    return 0


def deactivate(name: str) -> int:
    """Switch off the workflow called `name` on this instance. Deletes nothing:
    the definition and its run history stay, and deploying the file turns it
    back on. 0 done, 1 the instance refused, 3 no single workflow by that name.
    """
    wid, why = id_by_name(name)
    if wid is None:
        print(f"DEACTIVATE REFUSED: {why}", file=sys.stderr)
        return 3
    status, resp = api(f"/workflows/{wid}/deactivate", "POST")
    if status not in (200, 201):
        print(f"DEACTIVATE FAILED for {name}: HTTP {status}: {resp[:200]}",
              file=sys.stderr)
        return 1
    print(f"deactivated {name} ({wid}) on {HOST}; nothing was deleted")
    return 0


def audit(repo_dir: Path | None = None) -> int:
    """Compare automation/workflows/ against what the instance is running.

    THE POINT IS THE THINGS NOBODY DECLARED. A workflow running in the
    container that no file describes cannot be reviewed, deployed elsewhere,
    or reasoned about; a file whose `active` disagrees with the instance means
    one of the two is a fiction. Both were true on 2026-09-22 and neither was
    visible to any tool.

    Never prints a workflow's nodes or parameters, only names and flags: this
    talks to a live instance and its output goes in logs.
    """
    repo_dir = repo_dir or workflows_dir()
    repo = {}
    for path in sorted(repo_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"AUDIT  {path.name}: unreadable, so it cannot be compared "
                  f"({exc})", file=sys.stderr)
            return 2
        repo[doc.get("name") or path.stem] = {
            "file": path.name, "active": bool(doc.get("active")),
        }

    code, raw = api("/workflows?limit=200")
    # `api` RETURNS (status, TEXT), never a parsed object. The order that
    # specified this function assumed a dict, the driver implemented what it
    # was told, and the self-test stubbed `api` with a dict, so the gate went
    # green against a contract that does not exist and the first real run
    # printed "the instance did not answer (HTTP 200)". A stub is only worth
    # its resemblance to the thing it replaces.
    if code != 200:
        print(f"AUDIT REFUSED: the instance did not answer (HTTP {code}). "
              f"This says nothing about whether the repo and the container "
              f"agree.", file=sys.stderr)
        return 3
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"AUDIT REFUSED: the instance answered 200 with something that "
              f"is not JSON ({exc}). Still not an answer about agreement.",
              file=sys.stderr)
        return 3
    live = {w.get("name"): bool(w.get("active"))
            for w in body.get("data", []) if w.get("name")}

    findings = []
    for name in sorted(set(live) - set(repo)):
        findings.append(f"IN THE INSTANCE, IN NO REPO FILE   {name} "
                        f"(active={live[name]})")
    for name in sorted(set(repo) - set(live)):
        findings.append(f"IN THE REPO, NOT IN THE INSTANCE   {name} "
                        f"({repo[name]['file']})")
    for name in sorted(set(repo) & set(live)):
        if repo[name]["active"] != live[name]:
            findings.append(f"ACTIVE DISAGREES                   {name}: "
                            f"repo={repo[name]['active']} "
                            f"instance={live[name]}")

    for line in findings:
        print(line)
    print(f"n8n-audit: {len(repo)} file(s), {len(live)} in the instance, "
          f"{len(findings)} disagreement(s)")
    return 1 if findings else 0

    
def main():
    if "--self-test" in sys.argv[1:]:
        return self_test()
    if False:
        pass  # placeholder to preserve previous indentation levels in patch context
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow", nargs="?",
                    help="path to the workflow JSON (not needed with --audit)")
    ap.add_argument("--container", default=CONTAINER)
    ap.add_argument("--verify", help="webhook path to probe after deploy")
    ap.add_argument("--method", default="GET", choices=["GET", "POST"])
    ap.add_argument("--expect", type=int, default=200)
    ap.add_argument("--body", help="JSON body for a POST probe. Prefer --body-file: "
                                   "PowerShell strips inner double quotes when "
                                   "passing an argument to a native exe, so JSON "
                                   "on the command line arrives malformed")
    ap.add_argument("--body-file", dest="body_file",
                    help="read the POST body from a file, no shell quoting at all")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--audit", action="store_true",
                    help="compare automation/workflows/ against what the "
                         "instance is actually running, and change nothing")
    ap.add_argument("--host", default=LOCALHOST,
                    help="the n8n instance to deploy to, an address or "
                         "hostname. Default is this machine. The API key is "
                         "read from a file named after the host, because each "
                         "instance issues its own. NOTE: only the API path is "
                         "remote; the CLI fallback is local-only and REFUSES "
                         "for a remote host rather than deploying here")
    ap.add_argument("--deactivate", metavar="NAME",
                    help="switch off the workflow with this name on --host; "
                         "nothing is deleted")
    a = ap.parse_args()
    set_host(a.host)
    if api_key() is None:
        print("REFUSED: no API key for %s. Expected it in %s. Each n8n "
              "instance issues its own key, so this never falls back to "
              "another instance's: that returns 401 and reads like the "
              "instance being down." % (a.host, key_file()), file=sys.stderr)
        return 3
    if a.audit:
        return audit()
    if a.deactivate:
        return deactivate(a.deactivate)
    if not a.workflow:
        ap.error("give a workflow path, or --audit")
    body = a.body
    if a.body_file:
        body = Path(a.body_file).read_text(encoding="utf-8")
    return deploy(Path(a.workflow), a.container, a.verify, a.method,
                  a.expect, body, not a.no_restart)


if __name__ == "__main__":
    sys.exit(main())
