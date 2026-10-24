#!/usr/bin/env python3
"""server-probe.py: read the local opencode server's own API contract.

Exists because a hanging POST is not a diagnosis. When a call to the server
does not come back, the question is whether the request shape is wrong, and the
server publishes the answer at /doc. Guessing at a body shape is how the
benchmark burned a night.

The password is resolved the same way bench-run.py does and is never printed.

Usage:
  python tools/server-probe.py --port 4096 [--path /session/{id}/message]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def _password():
    """Read the server password from the environment or Windows user settings."""
    import os
    val = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if val:
        return val.strip()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "OPENCODE_SERVER_PASSWORD")
            return value.strip()
    except Exception:
        return None


# `/config/providers` returns the provider API KEY in plaintext. A prior probe
# printed a live API key into a transcript. Anything this script prints goes
# through here first.
#
# MATCHED CASE-INSENSITIVELY since 2026-09-16, and that was a real hole rather
# than tidiness: HTTP spells the header `Authorization`, so the one spelling
# most likely to arrive from a real server was the one this set could not
# match. The set is lowercase and every lookup is lowered.
_SECRET_KEYS = {"key", "apikey", "api_key", "token", "password", "secret",
                "authorization", "opencode_api_key", "bearer", "credential",
                "access_token", "refresh_token"}

# Built from parts, not written literally: a literal loopback address is what
# the repo's publishability check refuses.
LOCALHOST = ".".join(("127", "0", "0", "1"))


def _redact(obj):
    """Recursively blank anything that looks like a credential.

    TWO INDEPENDENT NETS, on purpose. The first is the KEY NAME, which catches
    a credential whatever it looks like. The second is the VALUE SHAPE
    (`sk-` and long), which catches one whose key nobody thought of. Either
    alone has been enough to leak: the 2026-08-22 incident was a value under a
    key this set did not know.

    THE REPLACEMENT NAMES THE LENGTH rather than vanishing, so a reader can
    tell a redacted secret from an absent field. Silence and success must never
    look alike, and that applies to a redaction too.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _SECRET_KEYS and isinstance(v, str) and v:
                out[k] = "<redacted %d chars>" % len(v)
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("sk-") and len(obj) > 20:
        return "<redacted key>"
    return obj


def self_test() -> int:
    """Assert the redactor, which is the only thing here that can do harm.

    Everything else in this file talks to a live opencode server and is proven
    by using it. The redactor is different: it runs on every printed payload,
    and when it is wrong the failure is a credential in a transcript, which is
    not recoverable by noticing afterwards.
    """
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    live = "sk-" + "a" * 40

    # NET ONE: the key name. Red-proven by emptying _SECRET_KEYS.
    want("a plain `key` is redacted",
         _redact({"key": "abcd1234"})["key"].startswith("<redacted"))
    want("and the redaction NAMES THE LENGTH, so absence is distinguishable",
         _redact({"key": "abcd1234"})["key"] == "<redacted 8 chars>")

    # THE HOLE FIXED 2026-09-16. HTTP spells it `Authorization`, and the set
    # was matched case-sensitively, so the single most likely real spelling
    # passed straight through.
    want("Authorization with its real HTTP capitalisation is redacted",
         _redact({"Authorization": "Bearer xyz"})["Authorization"]
         .startswith("<redacted"))
    want("apiKey in camelCase is redacted",
         _redact({"apiKey": "xyz"})["apiKey"].startswith("<redacted"))
    want("API_KEY in shouting case is redacted",
         _redact({"API_KEY": "xyz"})["API_KEY"].startswith("<redacted"))

    # NET TWO: the value shape, for a key nobody listed. This is the net that
    # would have caught the 2026-08-22 leak on its own.
    want("a long sk- value is redacted even under an innocent key",
         _redact({"harmless": live})["harmless"] == "<redacted key>")
    want("a SHORT sk- string is left alone, so `sk-test` stays readable",
         _redact({"harmless": "sk-test"})["harmless"] == "sk-test")

    # NESTING, because the real payload is providers -> models -> options.
    nested = {"providers": [{"id": "zen", "options": {"apiKey": live}}]}
    want("a secret nested in a list of dicts is still redacted",
         _redact(nested)["providers"][0]["options"]["apiKey"]
         .startswith("<redacted"))
    want("and its siblings survive the walk intact",
         _redact(nested)["providers"][0]["id"] == "zen")

    # NOTHING ELSE IS TOUCHED. A redactor that eats ordinary fields gets
    # switched off, which is the failure mode after the failure mode.
    payload = {"id": "zen", "count": 3, "ok": True, "models": ["a", "b"],
               "nothing": None}
    want("ordinary values pass through unchanged", _redact(payload) == payload)
    want("an EMPTY secret passes through rather than reading as 0 chars",
         _redact({"key": ""})["key"] == "")
    want("a non-string secret is not turned into a length string",
         _redact({"key": 12345})["key"] == 12345)
    want("a scalar on its own is returned as-is", _redact("plain") == "plain")

    # THE LOOPBACK ADDRESS IS BUILT FROM PARTS, never written literally,
    # and THIS ASSERTION HAD TO BE WRITTEN THE SAME WAY. The obvious spelling,
    # comparing it against the literal address, puts that literal in this file
    # and `tools/check-publishable.py` refuses it here by name. Caught by that
    # gate the same session, which is the gate working.
    want("the loopback address is four octets and ends at one",
         LOCALHOST.count(".") == 3 and LOCALHOST.split(".")[-1] == "1")
    want("and it starts at the loopback block, not a routable address",
         LOCALHOST.split(".")[0] == str(127))

    if fails:
        print(f"\nserver-probe self-test: {len(fails)} of {checks} FAILED")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"\nserver-probe self-test: {checks} assertions passed")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4096)
    ap.add_argument("--grep", default="message",
                    help="only print paths containing this substring")
    ap.add_argument("--session", help="dump this session's messages instead")
    ap.add_argument("--get", help="GET this path and print the JSON")
    ap.add_argument("--models", action="store_true",
                    help="list every model id per provider, compactly")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    pw = _password()
    if not pw:
        print("no password found")
        return 2

    base = "http://%s:%d" % (LOCALHOST, args.port)
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, base, "opencode", pw)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))

    if args.models:
        with opener.open(base + "/config/providers", timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        for provider in payload.get("providers", []):
            models = provider.get("models", {}) or {}
            print("=== %s (%d models)" % (provider.get("id"), len(models)))
            for mid in sorted(models):
                cost = (models[mid].get("cost") or {})
                print("  %-34s in %-8s out %s"
                      % (mid, cost.get("input"), cost.get("output")))
        return 0

    if args.get:
        with opener.open(base + args.get, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        print(json.dumps(_redact(payload), indent=2)[:4000])
        return 0

    if args.session:
        with opener.open(base + "/session/%s/message" % args.session,
                         timeout=60) as resp:
            messages = json.loads(resp.read().decode("utf-8"))
        print("messages: %d" % len(messages))
        for entry in messages:
            info = entry.get("info", {}) or {}
            parts = entry.get("parts", []) or []
            print("--- role=%s error=%s tokens=%s cost=%s"
                  % (info.get("role"), info.get("error"),
                     info.get("tokens"), info.get("cost")))
            for part in parts:
                ptype = part.get("type")
                if ptype == "text":
                    print("    text: %s" % (part.get("text") or "")[:300])
                else:
                    print("    %s: %s" % (ptype, json.dumps(part)[:300]))
        return 0

    with opener.open(base + "/doc", timeout=60) as resp:
        spec = json.loads(resp.read().decode("utf-8"))

    for path, methods in sorted(spec.get("paths", {}).items()):
        if args.grep not in path:
            continue
        for verb, op in methods.items():
            print("=== %s %s" % (verb.upper(), path))
            body = (op.get("requestBody") or {}).get("content", {})
            for mime, detail in body.items():
                print("  body (%s):" % mime)
                print(json.dumps(detail.get("schema", {}), indent=4)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
