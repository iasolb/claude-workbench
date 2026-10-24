#!/usr/bin/env python3
"""Publishability scan for n8n workflow JSON in automation/workflows/.

tools/check-publishable.py scans a fixed list of Python tools line by line,
and a workflow is JSON with the values it must find buried in node parameters,
so a clean run from it said nothing about workflows while looking exactly like
a clean bill of health (docs/n8n-portability-audit.md, 2026-08-31). This tool
parses every *.json in automation/workflows/ and walks the PARSED structure at
any depth, flagging values that must not reach a public repo: an email
address, an absolute Windows or /c/-style POSIX path, the machine account
name, a docker-compose container name, host.docker.internal, a loopback
address, an explicit port, a hardcoded github.com owner/repo, and a long
high-entropy literal that looks like a key or token.

The offending VALUE is never printed, only its length, so a scan cannot move
the leak into a log or a CI transcript. A file that is not valid JSON is
reported loudly and makes the run fail: empty-because-broken must not look
like empty-because-clean. Exit 0 when every file is valid and clean, 1 on any
finding or parse failure, so the tool can gate.

--self-test builds its own fixtures in a temporary directory and proves every
detector against a value it must flag and a value it must not flag. It is the
whole gate: a real run depends on today's workflow contents and is never the
proof.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WIN_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\\/]")
_POSIX_PATH_RE = re.compile(r"(?<![/A-Za-z0-9])//?[A-Za-z]/[A-Za-z0-9._-]+/")
_LOOPBACK_RE = re.compile(r"\b127\.\d{1,3}(?:\.\d{1,3}){2}\b")
_DOCKER_HOST_RE = re.compile(r"\bhost\.docker\.internal\b")
_CONTAINER_RE = re.compile(r"\b[a-z][a-z0-9]*[-_][a-z][a-z0-9]*[-_][0-9]+\b")
# An explicit port as it actually appears in a connection: after a scheme
# (`https://host:8443/...`) or after a dotted host (`api.example.com:8443`).
# CSS units and property values (`padding:12px`, `font-weight:700`) share the
# word:digits shape and are excluded by requiring a scheme or a dot in the
# host, and a bare service name (`n8n:5678`) is not strong enough to flag.
_PORT_RE = re.compile(
    r"://[^/\s:]+:\d{2,5}"
    r"|[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9_-]+:\d{2,5}")
_PORT_KEY_RE = re.compile(r"(?i)(?:^|_|-)port$")
_GH_REPO_RE = re.compile(
    r"(?<!api\.)github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"|api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_TOKEN_ALPHABET_RE = re.compile(r"[A-Za-z0-9_\-+=]+")
_TOKEN_PREFIX_RE = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), "github-token-shaped value"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "openai-key-shaped value"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "jwt-shaped value"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-key-shaped value"),
    (re.compile(r"\bn8n_api_[A-Za-z0-9]{10,}\b"), "n8n-api-key-shaped value"),
]

# The account this machine runs under, read at runtime and matched by shape so
# no user is named in this source (same trick as check-publishable.py).
_ACCOUNT_RE = re.compile(r"\b" + re.escape(Path.home().name) + r"\b")


def _token_reason(value: str) -> str | None:
    """A whole-string high-entropy literal, as opposed to an embedded token.

    A real key stored as a node-parameter value is one long alphanumeric run
    with no spaces or separators. Prose and code contain spaces and
    punctuation, and are never mistaken for one; URLs and paths contain `/`
    and `.` and are never either. UUIDs (node ids, webhook ids) and pure hex
    (a git SHA) are high-entropy but are not secrets. Two distinct character
    classes among lower, upper and digit is the entropy bar.
    """
    if len(value) < 32 or any(ch.isspace() for ch in value):
        return None
    if not _TOKEN_ALPHABET_RE.fullmatch(value):
        return None
    if _UUID_RE.fullmatch(value) or _HEX_RE.fullmatch(value):
        return None
    classes = [any(c.islower() for c in value),
               any(c.isupper() for c in value),
               any(c.isdigit() for c in value)]
    if sum(classes) < 2:
        return None
    return "high-entropy literal (token-shaped)"


def _check_string(value, keypath, in_creds, node, findings):
    checks = []
    if _EMAIL_RE.search(value):
        checks.append("email address")
    if _WIN_PATH_RE.search(value):
        checks.append("absolute Windows path")
    if _POSIX_PATH_RE.search(value):
        checks.append("/c/-style POSIX path")
    if _ACCOUNT_RE.search(value):
        checks.append("machine account name")
    if _CONTAINER_RE.search(value):
        checks.append("docker-compose container name")
    if _DOCKER_HOST_RE.search(value):
        checks.append("host.docker.internal")
    if _LOOPBACK_RE.search(value):
        checks.append("loopback address")
    if _PORT_RE.search(value):
        checks.append("explicit port")
    if _GH_REPO_RE.search(value):
        checks.append("hardcoded github.com owner/repo")
    for rx, reason in _TOKEN_PREFIX_RE:
        if rx.search(value):
            checks.append(reason)
    if not in_creds:
        reason = _token_reason(value)
        if reason:
            checks.append(reason)
    for reason in checks:
        findings.append({"file": "", "node": node, "key": keypath,
                         "reason": reason, "length": len(value)})


def _walk(val, keypath, in_creds, node, findings):
    if isinstance(val, dict):
        for k, v in val.items():
            base = "%s.%s" % (keypath, k) if keypath else k
            if k == "nodes" and isinstance(v, list):
                for i, item in enumerate(v):
                    name = item.get("name", "") if isinstance(item, dict) else ""
                    _walk(item, "nodes[%d]" % i, False, name, findings)
            elif isinstance(v, int) and _PORT_KEY_RE.search(k) and 0 < v < 65536:
                findings.append({"file": "", "node": node, "key": base,
                                 "reason": "explicit port",
                                 "length": len(str(v))})
                _walk(v, base, in_creds or k == "credentials", node, findings)
            else:
                _walk(v, base, in_creds or k == "credentials", node, findings)
    elif isinstance(val, list):
        for i, v in enumerate(val):
            _walk(v, "%s[%d]" % (keypath, i), in_creds, node, findings)
    elif isinstance(val, str):
        _check_string(val, keypath, in_creds, node, findings)


def scan_file(path: Path):
    """Returns (parsed_ok, findings). parsed_ok False means the file is not
    valid JSON, reported as a finding rather than skipped silently."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, [{"file": path.name, "node": "", "key": "",
                        "reason": "not valid JSON: %s" % exc, "length": 0}]
    except OSError as exc:
        return False, [{"file": path.name, "node": "", "key": "",
                        "reason": "cannot read: %s" % exc, "length": 0}]
    findings = []
    _walk(data, "", False, "", findings)
    for f in findings:
        f["file"] = path.name
    return True, findings


def render(finding) -> str:
    if not finding["key"] and not finding["node"]:
        return "FAIL  %s  %s" % (finding["file"], finding["reason"])
    return "FAIL  %s  node %s  key %s  %s  <%s, %d chars>" % (
        finding["file"], finding["node"] or "-", finding["key"],
        finding["reason"], "redacted", finding["length"])


def _wf(name, nodes):
    return {"id": name, "name": name, "active": True, "nodes": nodes,
            "connections": {}, "settings": {"executionOrder": "v1"}}


def _node(node_name, parameters, node_type="n8n-nodes-base.code", **extra):
    node = {"parameters": parameters,
            "id": "7d000000-0000-4000-8000-000000000001",
            "name": node_name, "type": node_type, "typeVersion": 2,
            "position": [0, 0]}
    node.update(extra)
    return node


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="check-workflows-selftest-"))
    failures = []
    checks = 0
    rendered = []

    def expect(cond, label):
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(label)

    def scan(name):
        ok, findings = scan_file(tmp / name)
        rendered.extend(render(f) for f in findings)
        return ok, findings

    account = Path.home().name
    email_value = "first.last" + chr(64) + "example.invalid"
    token_value = "Qz7" * 14
    ghp_value = "ghp_AbCdefgh1234567890"
    win_value = r"C:\Home\someone\Desktop\secrets.txt"
    posix_value = "/c/Home/someone/Desktop/secrets.txt"

    fixtures = {
        "email.json": _wf("email", [
            _node("Build artifact",
                  {"jsCode": "const account = '%s';\nreturn [account];"
                             % email_value})]),
        "winpath.json": _wf("winpath", [
            _node("Read file", {"fileSelector": win_value},
                  "n8n-nodes-base.readWriteFile")]),
        "posix.json": _wf("posix", [
            _node("Read file", {"fileSelector": posix_value},
                  "n8n-nodes-base.readWriteFile")]),
        "account.json": _wf("account", [
            _node("Host check", {"nodeName": account})]),
        "container.json": _wf("container", [
            _node("Container check", {"host": "n8n-n8n-1"})]),
        "dockerhost.json": _wf("dockerhost", [
            _node("Host check", {"host": "host.docker.internal"})]),
        "loopback.json": _wf("loopback", [
            _node("Host check", {"host": ".".join(("127", "0", "0", "1"))})]),
        "port.json": _wf("port", [
            _node("Call service", {"url": "https://api.example.com:8443/v1",
                                   "port": 5678})]),
        "github.json": _wf("github", [
            _node("Open issue",
                  {"url": "https://api.github.com/repos/someowner/somerepo/issues"},
                  "n8n-nodes-base.httpRequest")]),
        "secret.json": _wf("secret", [
            _node("Auth", {"apiKey": token_value})]),
        "tokenprefix.json": _wf("tokenprefix", [
            _node("Auth", {"authorization": ghp_value})]),
        "clean.json": _wf("clean", [
            _node("Webhook", {"httpMethod": "POST", "path": "clean",
                              "responseMode": "responseNode", "options": {}},
                  "n8n-nodes-base.webhook"),
            _node("Read TOON", {"fileSelector": "/state/routines/clean.toon",
                                "options": {}},
                  "n8n-nodes-base.readWriteFile"),
            _node("Parse TOON",
                  {"jsCode": "const raw = $('Read TOON').first();\n"
                             "return [{ json: { count: raw.json.count || 0 } }];"}),
            _node("Open issue",
                  {"method": "POST", "url": "https://api.github.com/user/repos",
                   "authentication": "predefinedCredentialType",
                   "nodeCredentialType": "githubApi",
                   "sendBody": True, "specifyBody": "json",
                   "jsonBody": "={{ { title: $('Parse TOON').first().json.count } }}",
                   "options": {}},
                  "n8n-nodes-base.httpRequest",
                  extra={"credentials": {
                      "githubApi": {"id": "L0ngCr3d3nt14l1dF0rTh3S4feSh4pe12345",
                                    "name": "GitHub account"}}}),
            _node("Respond",
                  {"respondWith": "json",
                   "responseBody": "={{ { ok: true } }}",
                   "options": {"responseCode": 200}},
                  "n8n-nodes-base.respondToWebhook"),
        ]),
    }

    # The clean fixture needs a webhookId to carry a second UUID, and the
    # time/timestamp/contact values that must never read as a port or email.
    clean = fixtures["clean.json"]
    clean["nodes"][0]["webhookId"] = "7d000000-0000-4000-8000-0000000000ff"
    clean["nodes"][3]["parameters"]["time"] = "12:34"
    clean["nodes"][3]["parameters"]["ts"] = "2026-08-31T10:30:00Z"
    clean["nodes"][3]["parameters"]["version"] = "v1.2.3"
    clean["nodes"][3]["parameters"]["contact"] = "hello@world"
    clean["nodes"][3]["parameters"]["style"] = ("padding:12px;font-weight:700;"
                                                "width:100%;text-align:center")

    for name, doc in fixtures.items():
        (tmp / name).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    (tmp / "broken.json").write_text('{ "name": "broken", "nodes": [',
                                     encoding="utf-8")

    # Positive fixtures: every planted value must be flagged.
    ok, findings = scan("email.json")
    expect(ok, "email.json should parse")
    expect(any(f["reason"] == "email address" for f in findings),
           "email value not flagged")
    expect(any(f["node"] == "Build artifact" and "jsCode" in f["key"]
               for f in findings), "email finding lost node or key context")

    ok, findings = scan("winpath.json")
    expect(ok, "winpath.json should parse")
    expect(any(f["reason"] == "absolute Windows path" for f in findings),
           "windows path not flagged")

    ok, findings = scan("posix.json")
    expect(ok, "posix.json should parse")
    expect(any(f["reason"] == "/c/-style POSIX path" for f in findings),
           "/c/ path not flagged")

    ok, findings = scan("account.json")
    expect(ok, "account.json should parse")
    expect(any(f["reason"] == "machine account name" for f in findings),
           "machine account name not flagged")

    ok, findings = scan("container.json")
    expect(ok, "container.json should parse")
    expect(any(f["reason"] == "docker-compose container name"
               for f in findings), "container name not flagged")

    ok, findings = scan("dockerhost.json")
    expect(ok, "dockerhost.json should parse")
    expect(any(f["reason"] == "host.docker.internal" for f in findings),
           "host.docker.internal not flagged")

    ok, findings = scan("loopback.json")
    expect(ok, "loopback.json should parse")
    expect(any(f["reason"] == "loopback address" for f in findings),
           "loopback address not flagged")

    ok, findings = scan("port.json")
    expect(ok, "port.json should parse")
    expect(sum(1 for f in findings if f["reason"] == "explicit port") >= 2,
           "port not flagged in both host:port and integer forms")

    ok, findings = scan("github.json")
    expect(ok, "github.json should parse")
    expect(any(f["reason"] == "hardcoded github.com owner/repo"
               for f in findings), "hardcoded github repo not flagged")

    ok, findings = scan("secret.json")
    expect(ok, "secret.json should parse")
    expect(any(f["reason"] == "high-entropy literal (token-shaped)"
               for f in findings), "high-entropy token not flagged")
    expect(any("<redacted, 42 chars>" in render(f) for f in findings),
           "redacted shape not rendered with the right length")

    ok, findings = scan("tokenprefix.json")
    expect(ok, "tokenprefix.json should parse")
    expect(any("github-token-shaped" in f["reason"] for f in findings),
           "ghp_ token not flagged")

    # Negative fixture: the normal, safe n8n shape must come back clean.
    ok, findings = scan("clean.json")
    expect(ok, "clean.json should parse")
    expect(not findings, "clean workflow flagged: %r" % findings)

    # Malformed JSON must be reported, never skipped.
    ok, findings = scan("broken.json")
    expect(not ok, "broken.json should report a parse failure")
    expect(any("not valid JSON" in f["reason"] for f in findings),
           "broken.json not reported as a parse failure")

    # The offending values must never be printed anywhere.
    for secret in (email_value, token_value, ghp_value, win_value,
                   posix_value, account):
        expect(all(secret not in line for line in rendered),
               "offending value leaked into scan output: %s" % secret)

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("self-test PASS: %d assertions, %d fixtures, "
          "offending values never surfaced"
          % (checks, len(fixtures) + 1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", help="workflow directory to scan (default: "
                    "automation/workflows under this repo)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the throwaway-fixture self-test and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    base = Path(args.dir).resolve() if args.dir else \
        Path(__file__).resolve().parents[1] / "automation" / "workflows"
    if not base.is_dir():
        print("check-workflows: workflow directory missing: %s" % base)
        return 2
    files = sorted(base.glob("*.json"))
    if not files:
        print("check-workflows: no *.json files under %s" % base)
        return 2
    total = 0
    for path in files:
        ok, findings = scan_file(path)
        if findings:
            for f in findings:
                print(render(f))
                total += 1
        else:
            print("OK  %s" % path.name)
    print("check-workflows: %d file(s), %d finding(s)" % (len(files), total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
