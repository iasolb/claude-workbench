#!/usr/bin/env python3
"""Resolve named secrets from the operating system keystore.

On macOS, values are stored in Keychain. Configure WORKBENCH_CONF to override
the default ~/.claude/workbench.conf, and SECRET_STAGING_DIR to override the
default ~/.claude/secret-staging. SECRET_NAMES is a comma-separated allowlist;
the defaults are API_KEY, ACCESS_TOKEN, DATABASE_URL, and SERVICE_TOKEN.

SECRET_REMOTES is an optional JSON object mapping an SSH alias to an object
with "python" and "tool" paths, for example
{"build-host":{"python":"python3","tool":"~/tools/secret.py"}}.
No remote can be used unless it is explicitly configured. Received values are
staged with restrictive file permissions until a login session can import them.

    python secret.py get NAME
    python secret.py set NAME                 # reads the value from stdin
    python secret.py list
    python secret.py migrate
    python secret.py send NAME SSH_ALIAS
    python secret.py receive NAME             # reads value from stdin
    python secret.py exec <absolute-command> ...
    python secret.py --self-test

Never pass a value as a command-line argument. A missing or inaccessible
keystore is reported distinctly from an absent item.

WHY (2026-09-05: printing a configuration file during troubleshooting exposed
credentials; resolving references from the keystore keeps values out of it).
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

CONF = Path(os.environ.get("WORKBENCH_CONF", str(Path.home() / ".claude" / "workbench.conf"))).expanduser()
STAGING = Path(os.environ.get("SECRET_STAGING_DIR", str(Path.home() / ".claude" / "secret-staging"))).expanduser()
REF_PREFIX = "keychain:"
NOT_FOUND_RC = 44
DEFAULT_SECRET_NAMES = ("API_KEY", "ACCESS_TOKEN", "DATABASE_URL", "SERVICE_TOKEN")
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REMOTE_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def secret_names() -> tuple[str, ...]:
    raw = os.environ.get("SECRET_NAMES")
    names = tuple(value.strip() for value in raw.split(",") if value.strip()) if raw else DEFAULT_SECRET_NAMES
    if not all(NAME_PATTERN.fullmatch(name) for name in names):
        raise ValueError("SECRET_NAMES must contain comma-separated environment variable names")
    return names


def remote_config() -> dict[str, dict[str, str]]:
    raw = os.environ.get("SECRET_REMOTES", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SECRET_REMOTES must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("SECRET_REMOTES must be a JSON object")
    for alias, remote in parsed.items():
        if (not isinstance(alias, str) or not REMOTE_ALIAS_PATTERN.fullmatch(alias)
                or not isinstance(remote, dict)
                or not isinstance(remote.get("python"), str)
                or not isinstance(remote.get("tool"), str)):
            raise ValueError("each SECRET_REMOTES entry needs a safe alias, python, and tool path")
    return parsed


def parse_conf(path: Path = CONF) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return result
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def keystore_service(reference: str) -> str:
    if not reference.startswith(REF_PREFIX):
        raise ValueError(f"not a keystore reference: {reference!r}")
    return reference[len(REF_PREFIX):]


class KeystoreUnavailable(RuntimeError):
    """The keystore could not be queried; this does not mean an item is absent."""


def classify_lookup(returncode: int, stdout: str, stderr: str) -> str | None:
    if returncode == 0:
        return stdout.rstrip("\n")
    if returncode == NOT_FOUND_RC:
        return None
    detail = f": {stderr.strip()}" if stderr.strip() else ""
    raise KeystoreUnavailable(f"keystore could not be read (exit {returncode}){detail}")


def keystore_get(service: str) -> str | None:
    if platform.system() != "Darwin":
        raise NotImplementedError(f"no keystore backend for {platform.system()}; configure one before use")
    process = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True)
    return classify_lookup(process.returncode, process.stdout, process.stderr)


def keystore_set(service: str, value: str) -> None:
    if platform.system() != "Darwin":
        raise NotImplementedError(f"no keystore backend for {platform.system()}")
    process = subprocess.run(
        ["/usr/bin/security", "add-generic-password", "-s", service,
         "-a", os.environ.get("USER", "owner"), "-w", value, "-U"],
        capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(f"keystore write failed for {service}: {process.stderr.strip()}")


def ensure_ref(name: str, service: str, conf_path: Path = CONF) -> None:
    if parse_conf(conf_path).get(f"{name}_REF") != f"{REF_PREFIX}{service}":
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        with conf_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{name}_REF={REF_PREFIX}{service}\n")


def receive(name: str, value: str, staging: Path = STAGING) -> Path:
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        staging.chmod(0o700)
    except OSError:
        pass
    path = staging / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)
    return path


def import_staged(staging: Path = STAGING) -> list[str]:
    try:
        names = sorted(path.name for path in staging.iterdir())
    except FileNotFoundError:
        return []
    moved = []
    for name in names:
        if name not in secret_names():
            continue
        path = staging / name
        value = path.read_text(encoding="utf-8").strip()
        service = name.lower().replace("_", "-")
        keystore_set(service, value)
        if keystore_get(service) != value:
            raise RuntimeError(f"{name} did not read back from the keystore; staged file is retained")
        ensure_ref(name, service)
        path.unlink()
        moved.append(name)
    return moved


def secret_env(conf: dict[str, str], environ: dict[str, str], getter=None) -> tuple[dict[str, str] | None, str]:
    getter = getter or keystore_get
    result = dict(environ)
    for key in sorted(conf):
        if not key.endswith("_REF"):
            continue
        name = key[:-4]
        if result.get(name):
            continue
        try:
            value = getter(keystore_service(conf[key]))
        except (KeystoreUnavailable, ValueError) as exc:
            return None, f"{name}: {exc}"
        if value is None:
            return None, f"{name}: referenced keystore item does not exist"
        result[name] = value
    return result, ""


def resolve(name: str, conf: dict[str, str] | None = None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    conf = parse_conf() if conf is None else conf
    reference = conf.get(f"{name}_REF", "")
    if reference:
        value = keystore_get(keystore_service(reference))
        if value:
            return value
        print(f"[secret] {name} reference has no keystore item; set it with secret.py set {name}",
              file=sys.stderr)
        return None
    value = conf.get(name)
    if value:
        print(f"[secret] {name} remains plaintext in the config; run secret.py migrate", file=sys.stderr)
    return value


def migrate() -> int:
    conf = parse_conf()
    moved = []
    for name in secret_names():
        value = conf.get(name)
        if not value:
            continue
        service = name.lower().replace("_", "-")
        keystore_set(service, value)
        if keystore_get(service) != value:
            print(f"[secret] {name} did not read back; config left unchanged", file=sys.stderr)
            return 1
        moved.append(name)
    if not moved:
        print("[secret] no plaintext values to migrate")
        return 0
    output = []
    for raw in CONF.read_text(encoding="utf-8").splitlines(keepends=True):
        line = raw.strip()
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in moved:
            output.append(f"{key}_REF={REF_PREFIX}{key.lower().replace('_', '-') }\n")
        else:
            output.append(raw)
    CONF.write_text("".join(output), encoding="utf-8")
    print(f"[secret] migrated {len(moved)} value(s) to the keystore")
    return 0


def cmd_list() -> int:
    conf = parse_conf()
    for name in secret_names():
        reference = conf.get(f"{name}_REF")
        if reference:
            try:
                state = "present" if keystore_get(keystore_service(reference)) is not None else "MISSING"
            except (KeystoreUnavailable, NotImplementedError) as exc:
                state = f"UNREADABLE, {exc}"
            print(f"{name}: {reference} [{state}]")
        elif conf.get(name):
            print(f"{name}: plaintext in config [migrate it]")
        else:
            print(f"{name}: not configured")
    return 0


def send(name: str, alias: str) -> int:
    try:
        remotes = remote_config()
    except ValueError as exc:
        print(f"[secret] {exc}", file=sys.stderr)
        return 2
    if alias not in remotes:
        print(f"[secret] no configured remote for alias {alias!r}", file=sys.stderr)
        return 2
    if name not in secret_names():
        print(f"[secret] {name} is not in SECRET_NAMES", file=sys.stderr)
        return 2
    try:
        value = resolve(name)
    except (KeystoreUnavailable, NotImplementedError) as exc:
        print(f"[secret] {name}: {exc}", file=sys.stderr)
        return 3
    if not value:
        print(f"[secret] {name} is not set; nothing sent", file=sys.stderr)
        return 1
    remote = remotes[alias]
    process = subprocess.run(
        ["ssh", alias, remote["python"], remote["tool"], "receive", name],
        input=value, capture_output=True, text=True, timeout=120)
    sys.stdout.write(process.stdout)
    sys.stderr.write(process.stderr)
    return process.returncode


def exec_with_secrets(argv: list[str]) -> int:
    if not argv or not os.path.isabs(argv[0]):
        print("[secret] exec needs an absolute command path", file=sys.stderr)
        return 2
    try:
        for name in import_staged():
            print(f"[secret] imported staged value {name}", file=sys.stderr)
    except (KeystoreUnavailable, RuntimeError, NotImplementedError) as exc:
        print(f"[secret] staged value could not be imported: {exc}", file=sys.stderr)
        return 3
    environ, reason = secret_env(parse_conf(), dict(os.environ))
    if environ is None:
        print(f"[secret] refusing to start command: {reason}", file=sys.stderr)
        return 3
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(argv[0], argv, environ)
    return 0


def self_test() -> int:
    import tempfile

    failures: list[str] = []
    checks = 0

    def want(label: str, condition: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config = root / "config"
        config.write_text("# comment\nNAME=value\nexport ACCESS_TOKEN_REF='keychain:access-token'\nignored\n",
                          encoding="utf-8")
        parsed = parse_conf(config)
        want("key-value config parses", parsed.get("NAME") == "value")
        want("export prefix and quotes are removed", parsed.get("ACCESS_TOKEN_REF") == "keychain:access-token")
        want("comment and invalid lines are ignored", "ignored" not in parsed)
        want("missing config is empty", parse_conf(root / "missing") == {})
    want("success returns keychain value", classify_lookup(0, "value\n", "") == "value")
    want("not-found is distinct from a read error", classify_lookup(NOT_FOUND_RC, "", "") is None)
    try:
        classify_lookup(36, "", "")
        want("keychain failure raises", False)
    except KeystoreUnavailable:
        want("keychain failure raises", True)
    want("keychain reference is unwrapped", keystore_service("keychain:service") == "service")
    try:
        keystore_service("raw-value")
        want("bare value is refused as a reference", False)
    except ValueError:
        want("bare value is refused as a reference", True)
    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp) / "staging"
        staged = receive("ACCESS_TOKEN", "sample-value", staging)
        want("receive stages the value exactly", staged.read_text(encoding="utf-8") == "sample-value")
        if os.name == "posix":
            want("staged file and directory are private", staged.stat().st_mode & 0o777 == 0o600 and
                 staging.stat().st_mode & 0o777 == 0o700)
        want("empty staging directory imports no values", import_staged(Path(temp) / "empty") == [])
    conf = {"ACCESS_TOKEN_REF": "keychain:access-token"}
    environment, error = secret_env(conf, {"PATH": "/bin"}, lambda service: "sample-value")
    want("reference becomes an environment value", not error and environment is not None and
         environment.get("ACCESS_TOKEN") == "sample-value")
    environment, error = secret_env(conf, {"ACCESS_TOKEN": "already-set"}, lambda _service: None)
    want("existing environment value takes precedence", environment is not None and
         environment.get("ACCESS_TOKEN") == "already-set")
    environment, error = secret_env(conf, {}, lambda _service: None)
    want("missing keystore value refuses", environment is None and "ACCESS_TOKEN" in error)
    want("unconfigured remote alias is refused", "build-host" not in remote_config())
    previous_remotes = os.environ.get("SECRET_REMOTES")
    try:
        os.environ["SECRET_REMOTES"] = json.dumps({
            "build-host": {"python": "python3", "tool": "~/tools/secret.py"}})
        want("a configured hyphenated SSH alias is accepted",
             "build-host" in remote_config())
    finally:
        if previous_remotes is None:
            os.environ.pop("SECRET_REMOTES", None)
        else:
            os.environ["SECRET_REMOTES"] = previous_remotes
    print(f"secret self-test: {checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "--self-test":
        return self_test()
    try:
        names = secret_names()
    except ValueError as exc:
        print(f"secret: {exc}", file=sys.stderr)
        return 2
    if args[0] == "send" and len(args) == 3:
        return send(args[1], args[2])
    if args[0] == "receive" and len(args) == 2:
        if sys.stdin.isatty():
            print("secret: receive reads the value from stdin", file=sys.stderr)
            return 2
        if args[1] not in names:
            print(f"secret: {args[1]} is not in SECRET_NAMES", file=sys.stderr)
            return 2
        value = sys.stdin.read().strip()
        if not value:
            print("secret: refusing to stage an empty value", file=sys.stderr)
            return 2
        receive(args[1], value)
        print(f"secret: staged {args[1]} for the next configured keystore import")
        return 0
    if args[0] == "exec" and len(args) >= 2:
        return exec_with_secrets(args[1:])
    if args[0] == "list" and len(args) == 1:
        return cmd_list()
    if args[0] == "migrate" and len(args) == 1:
        return migrate()
    if args[0] == "get" and len(args) == 2:
        try:
            value = resolve(args[1])
        except (KeystoreUnavailable, NotImplementedError) as exc:
            print(f"secret: {args[1]}: {exc}", file=sys.stderr)
            return 3
        if value is None:
            print(f"secret: {args[1]} is not configured", file=sys.stderr)
            return 1
        print(value)
        return 0
    if args[0] == "set" and len(args) == 2:
        if args[1] not in names:
            print(f"secret: {args[1]} is not in SECRET_NAMES", file=sys.stderr)
            return 2
        if sys.stdin.isatty():
            print("secret: pipe the value through stdin; do not pass it as an argument", file=sys.stderr)
            return 2
        value = sys.stdin.read().strip()
        if not value:
            print("secret: refusing to store an empty value", file=sys.stderr)
            return 2
        service = args[1].lower().replace("_", "-")
        try:
            keystore_set(service, value)
            ensure_ref(args[1], service)
        except (NotImplementedError, RuntimeError) as exc:
            print(f"secret: {exc}", file=sys.stderr)
            return 3
        print(f"secret: stored {args[1]} in the keystore and referenced it from config")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
