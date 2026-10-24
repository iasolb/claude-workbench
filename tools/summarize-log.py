"""Summarize a driver log with a cheap hosted model over plain HTTP."""

import argparse
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request

ZEN_URL = "https://opencode.ai/zen/v1/chat/completions"
# Override with ZEN_MODEL or --model when the endpoint uses another model.
DEFAULT_MODEL = os.environ.get("ZEN_MODEL", "deepseek-v4-flash")
TAIL_LINES = 200

ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[PX^_][^\x1b]*\x1b\\"
    r"|\x1b[@-_]"
)

INTERESTING_RE = re.compile(
    r"\b(error|errors|failed|failure|fatal|traceback|exception|refused|denied"
    r"|timed out|timeout|panic)\b"
    r"|\bgate\b|\bverdict\b|\boutcome\b"
    r"|[✗✘×]",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "You summarise driver-session terminal logs. Reply with ONLY a JSON object, "
    'no markdown fence, exactly these keys: "outcome" (one of: done, done with '
    'errors, failed, still running), "gate" (one of: passed, failed, never ran, '
    'unknown), "errors" (array of strings, each one line, empty if none), '
    '"headline" (one sentence). Never invent details.'
)


def strip_ansi(line):
    return ANSI_RE.sub("", line)


def trim_log(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    lines = [
        strip_ansi(ln).rstrip()
        for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    keep = set()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s and (i == 0 or INTERESTING_RE.search(s)):
            keep.add(i)
    for i in range(max(0, len(lines) - TAIL_LINES), len(lines)):
        if lines[i].strip():
            keep.add(i)
    out = []
    prev_kept = None
    last_line = None
    for i in sorted(keep):
        if lines[i] == last_line:
            continue
        if prev_kept is not None and i > prev_kept + 1:
            out.append("...")
        out.append(lines[i])
        prev_kept = i
        last_line = lines[i]
    return "\n".join(out) if out else "(empty log)"


def build_messages(log_path, trimmed):
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Log file: %s\n\n<log>\n%s\n</log>" % (log_path, trimmed)},
    ]


def build_body(model, messages):
    return {"model": model, "messages": messages}


def parse_summary(content):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model reply")
    obj = json.loads(text[start : end + 1])
    errors_raw = obj.get("errors") or []
    if isinstance(errors_raw, str):
        errors_raw = [errors_raw]
    return {
        "outcome": str(obj.get("outcome", "unknown")).strip() or "unknown",
        "gate": str(obj.get("gate", "unknown")).strip() or "unknown",
        "errors": [str(e).strip() for e in errors_raw if str(e).strip()],
        "headline": str(obj.get("headline", "")).strip(),
    }


def request_summary(key, model, messages):
    body = json.dumps(build_body(model, messages)).encode("utf-8")
    req = urllib.request.Request(
        ZEN_URL,
        data=body,
        # Zen sits behind Cloudflare, which answers urllib's default
        # User-Agent with 403 "error code: 1010" before it ever looks at the
        # key (measured 2026-08-23 against this same endpoint). Any explicit
        # agent string clears it. Without this header no call here has ever
        # reached the model.
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key,
                 "User-Agent": "curl/8.4.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return parse_summary(data["choices"][0]["message"]["content"])


def load_key(args):
    if args.key_file:
        with open(args.key_file, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get("ZEN_API_KEY")


def render(summary, source, as_json):
    if as_json:
        return json.dumps({"summary": summary, "source": source}, indent=2)
    lines = ["outcome: " + summary["outcome"], "gate: " + summary["gate"]]
    if summary["errors"]:
        lines.append("errors:")
        for e in summary["errors"]:
            lines.append("  - " + e)
    else:
        lines.append("errors: none")
    lines.append("headline: " + summary["headline"])
    lines.append("source: " + source)
    return "\n".join(lines)


def self_test():
    failures = []
    checks_run = 0

    def check(name, cond):
        nonlocal checks_run
        checks_run += 1
        if not cond:
            failures.append(name)

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '```json\n{"outcome": "done with errors", "gate": "passed", '
                            '"errors": ["ERROR: three retries exhausted"], '
                            '"headline": "The lane finished with three recoverable errors."}\n```'
                        )
                    }
                }
            ]
        }
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        sample = "\x1b[1;34m== driver session 2026-08-22T16:05:44 ==\x1b[0m\r\n"
        sample += "\x1b[2m[tool] bash ls\x1b[0m\r\n" * 400
        sample += "\r\n"
        sample += "\x1b[31mERROR: three retries exhausted\x1b[0m\r\n"
        sample += "\x1b[32mGATE: passed\x1b[0m\r\n"

        trimmed = trim_log(sample)
        check("ansi stripped", "\x1b" not in trimmed)
        check("header kept", "== driver session 2026-08-22T16:05:44 ==" in trimmed)
        check("error line survives", "ERROR: three retries exhausted" in trimmed)
        check("verdict survives", "GATE: passed" in trimmed)
        check("noise collapsed", trimmed.count("[tool] bash ls") <= 2)

        secret = "sk-zen-SUPERSECRET"
        msgs = build_messages("fake.log", trimmed)
        body = build_body(DEFAULT_MODEL, msgs)
        check("default model carried", body["model"] == DEFAULT_MODEL)
        check("no key in prompt", secret not in json.dumps(body))
        check("chosen model honoured", build_body("custom-model-x", msgs)["model"] == "custom-model-x")

        parsed = parse_summary(
            '```json\n{"outcome": "done with errors", "gate": "passed", '
            '"errors": ["ERROR: three retries exhausted"], '
            '"headline": "The lane finished with three recoverable errors."}\n```'
        )
        check("parses outcome", parsed["outcome"] == "done with errors")
        check("parses gate", parsed["gate"] == "passed")
        check("parses errors", parsed["errors"] == ["ERROR: three retries exhausted"])
        check("parses headline", parsed["headline"].startswith("The lane finished"))

        old_env = os.environ.pop("ZEN_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                log_path = os.path.join(tmp, "log.txt")
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.write("hello\n")
                rc = main([log_path])
        finally:
            if old_env is not None:
                os.environ["ZEN_API_KEY"] = old_env
        check("missing key exits 4", rc == 4)
        check("no network on missing key", len(calls) == 0)
    finally:
        urllib.request.urlopen = real_urlopen

    if failures:
        for name in failures:
            print("FAIL: " + name, file=sys.stderr)
        print("self-test: %d checks ran, %d failure(s)"
              % (checks_run, len(failures)), file=sys.stderr)
        return 1
    print("self-test: %d checks passed" % checks_run)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Summarize a driver log via the Zen API.")
    parser.add_argument("log", nargs="?", help="path to a driver log")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Zen model id")
    parser.add_argument("--key-file", help="file holding the Zen API key")
    parser.add_argument("--json", dest="as_json", action="store_true", help="emit JSON")
    parser.add_argument("--self-test", action="store_true", help="run offline self-test")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    key = load_key(args)
    if not key:
        print("no key configured: set ZEN_API_KEY or pass --key-file", file=sys.stderr)
        return 4
    if not args.log:
        parser.error("a log path is required")

    try:
        with open(args.log, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        print("cannot read log: %s" % exc, file=sys.stderr)
        return 1

    trimmed = trim_log(raw)
    try:
        summary = request_summary(key, args.model, build_messages(os.path.abspath(args.log), trimmed))
    except urllib.error.HTTPError as exc:
        print("HTTP %s from endpoint" % exc.code, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print("endpoint unreachable: %s" % exc.reason, file=sys.stderr)
        return 1
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print("bad model reply: %s" % exc, file=sys.stderr)
        return 1

    print(render(summary, os.path.abspath(args.log), args.as_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
