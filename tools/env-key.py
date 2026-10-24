#!/usr/bin/env python3
"""Copy one named setting from a remote environment file without displaying it.

Configure ENV_KEY_HOSTS as a JSON object mapping public aliases to SSH targets,
for example {"build-host":"ssh-config-alias"}. The tool only uses aliases in
that mapping. Local write roots default to ~/projects and ~/Documents; override
them with ENV_KEY_LOCAL_ROOTS, separated by the platform path separator.

    python env-key.py find KEY --under build-host:/workspace
    python env-key.py merge KEY --from build-host:/workspace/.env --into ~/projects/app/.env
    python env-key.py --self-test

Remote reads are restricted to configured hosts and safe absolute paths. Local
writes are restricted to configured roots and .env or .env.local files that are
ignored by git. Values are never printed.

WHY (2026-10-17: moving a setting by hand involved failed shell commands and
temporary copies; this tool keeps the transfer bounded and reports no value).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REMOTE_PATH = re.compile(r"^/[A-Za-z0-9_./+-]+$")
ASSIGN = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")
QUOTED = re.compile(r"""^(?P<quote>[\"'])(?P<inner>.*)(?P=quote)(?:\s+#.*)?$""")
BARE_VALUE = re.compile(r"^[A-Za-z0-9_./:+=,-]*$")
LOCAL_NAMES = (".env", ".env.local")
Runner = Callable[[list[str]], subprocess.CompletedProcess]


def hosts() -> dict[str, str]:
    """Configured alias-to-SSH-target map; invalid configuration is refused."""
    raw = os.environ.get("ENV_KEY_HOSTS", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ENV_KEY_HOSTS must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and key and value
            for key, value in parsed.items()):
        raise ValueError("ENV_KEY_HOSTS must map non-empty aliases to SSH targets")
    return parsed


def local_roots() -> tuple[Path, ...]:
    raw = os.environ.get("ENV_KEY_LOCAL_ROOTS")
    values = raw.split(os.pathsep) if raw else [
        str(Path.home() / "projects"), str(Path.home() / "Documents")]
    return tuple(Path(value).expanduser().resolve() for value in values if value)


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def say(message: str) -> None:
    print(f"env-key: {message}")


def tail(text: str | None) -> str:
    return " | ".join((text or "").strip().splitlines()[-3:]) or "(no message)"


def parse_value(raw: str) -> str:
    match = QUOTED.match(raw)
    if match:
        inner = match["inner"]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if match["quote"] == '"' else inner
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def read_key(data: bytes, key: str) -> tuple[str | None, int]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    value, hits = None, 0
    for line in text.splitlines():
        match = ASSIGN.match(line)
        if match and match["key"] == key:
            value, hits = parse_value(match["value"]), hits + 1
    return value, hits


def render_line(key: str, value: str) -> str:
    if BARE_VALUE.match(value):
        return f"{key}={value}"
    return f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'


def merged(text: str, key: str, value: str) -> tuple[str, str, int | None]:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    hits = [index for index, line in enumerate(lines)
            if (match := ASSIGN.match(line)) and match["key"] == key]
    if not hits:
        return newline.join(lines + [render_line(key, value)]) + newline, "appended", None
    old = parse_value(ASSIGN.match(lines[hits[-1]])["value"])
    if len(hits) == 1 and old == value:
        return text, "unchanged", len(old)
    lines[hits[0]] = render_line(key, value)
    for index in reversed(hits[1:]):
        del lines[index]
    return newline.join(lines) + newline, "replaced", len(old)


def local_refusal(local: Path, roots: tuple[Path, ...]) -> str | None:
    path = Path(os.path.abspath(local))
    if path.name not in LOCAL_NAMES:
        return f"local file must be named {' or '.join(LOCAL_NAMES)}, not {path.name}"
    resolved_roots = tuple(root.resolve() for root in roots)
    resolved_path = path.resolve(strict=False)
    if not any(resolved_path.is_relative_to(root) for root in resolved_roots):
        return f"{path} is outside configured local roots: {', '.join(map(str, resolved_roots))}"
    if path.parent.is_dir():
        result = subprocess.run(["git", "-C", str(path.parent), "check-ignore", "-q", path.name],
                                capture_output=True)
        if result.returncode == 1:
            return f"{path} is not ignored by git, so a secret could be committed"
    return None


def remote_refusal(host: str, path: str, configured: dict[str, str]) -> str | None:
    if host not in configured:
        return f"host alias {host!r} is not configured (ENV_KEY_HOSTS)"
    if not REMOTE_PATH.fullmatch(path) or ".." in path.split("/"):
        return f"remote path {path!r} must be absolute and contain only safe path characters"
    return None


def find_command(key: str, host: str, under: str, configured: dict[str, str]) -> list[str]:
    return ["ssh", *SSH_OPTS, configured[host],
            f"grep -rlsIw '--include=.env*' -e {key} {under}"]


def find(key: str, target: str, runner: Runner = run,
         configured: dict[str, str] | None = None) -> int:
    configured = hosts() if configured is None else configured
    host, _, under = target.partition(":")
    why = None if KEY.fullmatch(key) else f"{key!r} is not a key name"
    why = why or remote_refusal(host, under, configured)
    if why:
        say(f"REFUSED, {why}")
        return 2
    say(f"searching configured host alias {host}")
    result = runner(find_command(key, host, under, configured))
    names = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if names:
        for name in names:
            print(name)
        if result.returncode == 2:
            say("some paths could not be read, so the list may be incomplete")
        return 0
    if result.returncode == 1 and not (result.stderr or "").strip():
        say(f"no .env file under {under} names {key}")
        return 3
    say(f"remote search failed (exit {result.returncode}): {tail(result.stderr)}")
    return 1


def merge(key: str, source: str, local: Path, runner: Runner = run,
          scratch: str | None = None, roots: tuple[Path, ...] | None = None,
          configured: dict[str, str] | None = None) -> int:
    configured = hosts() if configured is None else configured
    roots = local_roots() if roots is None else roots
    host, _, remote_path = source.partition(":")
    why = None if KEY.fullmatch(key) else f"{key!r} is not a key name"
    why = why or remote_refusal(host, remote_path, configured) or local_refusal(local, roots)
    if why:
        say(f"REFUSED, {why}")
        return 2
    say(f"copying from configured host alias {host}")
    with tempfile.TemporaryDirectory(prefix="env-key-", dir=scratch) as temp:
        copy = Path(temp) / "remote.env"
        result = runner(["scp", *SSH_OPTS, "-q", f"{configured[host]}:{remote_path}", str(copy)])
        if result.returncode != 0 or not copy.is_file():
            say(f"remote copy failed (exit {result.returncode}): {tail(result.stderr)}")
            code = 1
        else:
            value, hits = read_key(copy.read_bytes(), key)
            code = _write(key, value, hits, source, local)
    if Path(temp).exists():
        say(f"temporary copy was not deleted: {temp}")
        return 1
    say("temporary copy deleted")
    return code


def _write(key: str, value: str | None, hits: int, source: str, local: Path) -> int:
    if not value:
        say(f"{key} is {'empty' if hits else 'not set'} in {source}; nothing merged")
        return 3
    times = "once" if hits == 1 else f"{hits} times, last assignment used"
    say(f"{key}: {len(value)} characters, from {source} (assigned {times})")
    before = local.read_bytes().decode("utf-8") if local.is_file() else ""
    text, result, old_length = merged(before, key, value)
    if result == "unchanged":
        say(f"{local} unchanged")
        return 0
    with open(local, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
    suffix = f" (old value was {old_length} characters)" if old_length is not None else ""
    say(f"{local} {result}{suffix}")
    return 0


def self_test() -> int:
    checks = [0, 0]
    printed: list[str] = []
    sentinel = "SAMPLE-secret-93ad"

    def want(label: str, condition: bool) -> None:
        checks[0] += 1
        checks[1] += bool(condition)
        print(f"  {'ok' if condition else 'FAIL'}  {label}")

    def quiet(function: Callable[[], int]) -> int:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = function()
        printed.append(stdout.getvalue() + stderr.getvalue())
        return result

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        local_root = root / "projects"
        project = local_root / "sample-app"
        project.mkdir(parents=True)
        scratch = root / "scratch"
        scratch.mkdir()
        remote = root / "source.env"
        remote.write_text(f"SAMPLE_KEY = {sentinel}\nOTHER_KEY=private-value\n", encoding="utf-8")
        local = project / ".env"
        alias_map = {"sample": "configured-host"}

        def runner_for(source: Path | None, returncode: int = 0) -> Runner:
            def runner(command: list[str]) -> subprocess.CompletedProcess:
                if source is not None and returncode == 0:
                    Path(command[-1]).write_bytes(source.read_bytes())
                return subprocess.CompletedProcess(command, returncode, "", "" if returncode == 0 else "copy failed")
            return runner

        def do(key: str = "SAMPLE_KEY", source: str = "sample:/workspace/.env",
               destination: Path = local, runner: Runner | None = None) -> int:
            return quiet(lambda: merge(key, source, destination, runner or runner_for(remote),
                                       str(scratch), (local_root,), alias_map))

        want("one value is written to a new ignored file", do() == 0 and local.read_text() == f"SAMPLE_KEY={sentinel}\n")
        want("other remote values are not copied", "OTHER_KEY" not in local.read_text())
        want("the report includes a length", f"{len(sentinel)} characters" in printed[-1])
        want("the temporary file is removed", "temporary copy deleted" in printed[-1] and not any(scratch.iterdir()))
        prior = local.read_bytes()
        want("an identical second run changes nothing", do() == 0 and local.read_bytes() == prior)
        local.write_bytes(b"KEEP=1\r\nexport SAMPLE_KEY=old\r\nSAMPLE_KEY=older\r\n")
        want("duplicate settings collapse to one replacement", do() == 0 and
             local.read_bytes() == f"KEEP=1\r\nSAMPLE_KEY={sentinel}\r\n".encode())
        want("quoted and unquoted values parse", read_key(b'K="a b"\n', "K")[0] == "a b" and
             read_key(b"K=v # note\n", "K")[0] == "v")
        want("missing setting is reported distinctly", do(key="MISSING") == 3)
        want("unknown host alias is refused", do(source="unknown:/file") == 2)
        want("unsafe remote path is refused", do(source="sample:/a/../b") == 2)
        want("non-env local target is refused", do(destination=project / ".env.example") == 2)
        outside = local_root / "outside"
        outside.mkdir()
        subprocess.run(["git", "-C", str(outside), "init", "-q"], check=True)
        want("unignored local file is refused", do(destination=outside / ".env") == 2)
        (outside / ".gitignore").write_text(".env\n", encoding="utf-8")
        want("git-ignored local file is accepted", do(destination=outside / ".env") == 0)

        def find_runner(result: subprocess.CompletedProcess) -> Runner:
            return lambda command: result

        want("find returns paths, not file contents",
             quiet(lambda: find("SAMPLE_KEY", "sample:/workspace",
                                find_runner(subprocess.CompletedProcess([], 0, "/workspace/app/.env\n", "")),
                                alias_map)) == 0 and "/workspace/app/.env" in printed[-1])
        want("a clean no-match is exit 3", quiet(lambda: find(
            "SAMPLE_KEY", "sample:/workspace", find_runner(
                subprocess.CompletedProcess([], 1, "", "")), alias_map)) == 3)
        want("a failed remote search is not reported as no match", quiet(lambda: find(
            "SAMPLE_KEY", "sample:/workspace", find_runner(
                subprocess.CompletedProcess([], 1, "", "remote failure")), alias_map)) == 1)
    want("the secret value never appears in output", not any(sentinel in text for text in printed))
    print(f"env-key self-test: {checks[1]}/{checks[0]} checks passed")
    return 0 if checks[0] == checks[1] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="env-key")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("verb", nargs="?", choices=("find", "merge"))
    parser.add_argument("key", nargs="?")
    parser.add_argument("--under", help="find: alias:absolute-directory")
    parser.add_argument("--from", dest="source", help="merge: alias:absolute-env-file")
    parser.add_argument("--into", help="local .env file to update")
    parser.add_argument("--scratch", help="temporary directory for the downloaded file")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.verb or not args.key:
        parser.error("provide find or merge and a setting name")
    try:
        configured = hosts()
    except ValueError as exc:
        print(f"env-key: {exc}", file=sys.stderr)
        return 2
    if args.verb == "find":
        if not args.under:
            parser.error("find needs --under alias:absolute-directory")
        return find(args.key, args.under, configured=configured)
    if not (args.source and args.into):
        parser.error("merge needs --from alias:path and --into local-file")
    return merge(args.key, args.source, Path(args.into).expanduser(), scratch=args.scratch,
                 configured=configured)


if __name__ == "__main__":
    raise SystemExit(main())
