#!/usr/bin/env python3
"""Copy one checked Python tool into a public checkout's tools directory.

    python tools/publish-tool.py example.py --target ~/dev/project
    python tools/publish-tool.py example.py --target ~/dev/project --dry-run
    python tools/publish-tool.py --self-test

The target defaults to PUBLIC_WORKBENCH, then ~/dev/claude-workbench. The
source is this file's tools directory. Email and IP-address patterns are
refused before copying; review other project-specific information separately.

(2026-10-21: per-tool review avoids an all-at-once publication.)
"""
from __future__ import annotations

import argparse
import ast
import ipaddress
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
DEFAULT_TARGET = Path.home() / "dev" / "claude-workbench"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+[\x40][A-Za-z0-9.-]+\.[A-Za-z]{2,}")
OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
IP_RE = re.compile(rf"(?<![\w.]){OCTET}(?:\.{OCTET}){{3}}(?![\w.])")
IPV6_CANDIDATE = re.compile(r"(?<![\w:])\[?([A-Fa-f0-9:]{2,})(?:%[A-Za-z0-9_.-]+)?\]?(?![\w:])")


def personal_pattern(path: Path) -> str | None:
    """Return a generic PII category when a source contains a known pattern."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "unreadable source"
    if EMAIL_RE.search(text):
        return "email-shaped value"
    if IP_RE.search(text):
        return "IP-address-shaped value"
    for match in IPV6_CANDIDATE.finditer(text):
        try:
            if ipaddress.ip_address(match.group(1)).version == 6:
                return "IP-address-shaped value"
        except ValueError:
            continue
    return None


def publish(name: str, target: Path, dry_run: bool = False,
            tools: Path = TOOLS) -> tuple[int, str]:
    if "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".py"):
        return 2, f"give a bare Python filename, not {name}"
    source = tools / name
    if not source.is_file():
        return 2, f"no such tool: {source}"
    try:
        ast.parse(source.read_text(encoding="utf-8"), filename=name)
    except (OSError, UnicodeError, SyntaxError) as error:
        return 2, f"cannot parse {name}: {error}"
    issue = personal_pattern(source)
    if issue:
        return 1, f"REFUSED {name}: contains {issue}; nothing was copied"
    if not target.is_dir():
        return 2, f"public checkout is unavailable at {target}; nothing was copied"
    destination = target / "tools" / name
    if dry_run:
        return 0, f"WOULD publish {name} to {destination} (source is clean)"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    except OSError as error:
        return 2, f"could not copy {name} to {destination}: {error}"
    return 0, f"published {name} to {destination}; review and commit the target checkout"


def self_test() -> int:
    failures = []
    checks = 0

    def want(condition: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        tools, target = root / "source", root / "target"
        tools.mkdir()
        (target / "tools").mkdir(parents=True)
        clean = tools / "clean.py"
        clean.write_text("VALUE = 1\n", encoding="utf-8")
        code, message = publish("clean.py", target, tools=tools)
        want(code == 0 and (target / "tools" / "clean.py").read_text() == "VALUE = 1\n",
             "a clean Python tool is copied")
        want("review and commit" in message, "the result names the review step")
        code, _message = publish("clean.py", target, dry_run=True, tools=tools)
        want(code == 0, "a dry run succeeds")
        want(publish("../outside.py", target, tools=tools)[0] == 2,
             "a path instead of a bare filename is refused")
        want(publish("missing.py", target, tools=tools)[0] == 2,
             "a missing source is refused")
        dirty = tools / "dirty.py"
        sample_ip = ".".join(("192", "0", "2", "4"))
        dirty.write_text(f"VALUE = {sample_ip!r}\n", encoding="utf-8")
        want(personal_pattern(dirty) == "IP-address-shaped value",
             "an IP-address pattern is detected")
        sample_ipv6 = ":".join(("2001", "db8", "", "1"))
        dirty.write_text(f"VALUE = {sample_ipv6!r}\n", encoding="utf-8")
        want(personal_pattern(dirty) == "IP-address-shaped value",
             "an IPv6-address pattern is detected")
        address = "person" + chr(64) + "example" + "." + "invalid"
        dirty.write_text(f"VALUE = {address!r}\n", encoding="utf-8")
        want(personal_pattern(dirty) == "email-shaped value",
             "an email-shaped value is detected")
        want(publish("dirty.py", target, tools=tools)[0] == 1,
             "a source with personal data is refused")
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"publish-tool self-test: {checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="publish-tool")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--target", default=None, help="public checkout (default: PUBLIC_WORKBENCH or ~/dev/claude-workbench)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.name:
        parser.error("name a Python tool or pass --self-test")
    configured = args.target or os.environ.get("PUBLIC_WORKBENCH")
    target = Path(configured).expanduser() if configured else DEFAULT_TARGET
    code, message = publish(args.name, target, args.dry_run)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
