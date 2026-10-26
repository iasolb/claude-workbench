"""Report configuration keys read by hooks but missing from the config file."""

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

KEY_READ = re.compile(r"\^([A-Z][A-Z0-9_]*)=")


def keys_read(text: str) -> set[str]:
    """Return uppercase config keys mentioned in shell-style match patterns."""
    return set(KEY_READ.findall(text))


def keys_present(config_text: str) -> set[str]:
    """Return keys assigned in nonblank, non-comment config lines."""
    present = set()
    for raw in config_text.splitlines():
        line = raw.strip()
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line) if line and not line.startswith("#") else None
        if match:
            present.add(match.group(1))
    return present


def missing(hooks_dir: Path, config_text: str) -> dict[str, list[str]]:
    """Map each missing key to the hook files that read it."""
    present = keys_present(config_text)
    result: dict[str, list[str]] = {}
    if not hooks_dir.exists():
        return result
    for path in sorted(hooks_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.suffix.lower() not in {".sh", ".ps1"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for key in keys_read(content) - present:
            result.setdefault(key, []).append(path.name)
    return {key: sorted(set(names)) for key, names in result.items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--hooks")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    hooks_dir = Path(args.hooks) if args.hooks else Path(__file__).resolve().parents[1] / "hooks"
    config_name = args.config or os.environ.get("WORKBENCH_CONF")
    config_path = Path(config_name) if config_name else Path.home() / ".claude" / "workbench.conf"
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError:
        print(f"conf-keys: CANNOT READ {config_path}")
        return 3
    absent = missing(hooks_dir, config_text)
    if absent:
        for key in sorted(absent):
            print(f"conf-keys: MISSING {key} (read by {', '.join(absent[key])})")
        return 1
    print("conf-keys: every key the hooks read is set")
    return 0


def self_test() -> int:
    total = passed = 0

    def want(label: str, condition: bool) -> None:
        nonlocal total, passed
        total += 1
        if condition:
            passed += 1
        else:
            print(f"self-test FAIL: {label}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        hooks = root / "hooks"
        hooks.mkdir()
        (hooks / "one.sh").write_text('FOO="$(sed -n \'s/^FOO=//p\' \"$CONF\")"\n', encoding="utf-8")
        (hooks / "two.ps1").write_text("if ($line -match '^BAR=(.+)$') { }\n", encoding="utf-8")
        (hooks / "ignored.txt").write_text("^UNUSED=\n", encoding="utf-8")
        want("hook match expressions reveal keys", keys_read("x='^REPO_PATH=(.+)$'\n") == {"REPO_PATH"})
        want("plain assignments are not hook reads", not keys_read("nothing=happens"))
        want("comments do not satisfy a key", keys_present("# FOO=1\nBAR=2\n") == {"BAR"})
        want("missing keys are tied to their reading hook", missing(hooks, "BAR=ready\n") == {"FOO": ["one.sh"]})
        want("present keys are not reported", not missing(hooks, "BAR=ready\nFOO=ready\n"))
        reported = missing(hooks, "")
        want("non-hook files are ignored", set(reported) == {"FOO", "BAR"})
        want("an unreadable config exits distinctly", main(["--hooks", str(hooks), "--config", str(root / "absent")]) == 3)
        config = root / "workbench.conf"
        config.write_text("BAR=ready\nFOO=ready\n", encoding="utf-8")
        want("a complete config succeeds", main(["--hooks", str(hooks), "--config", str(config)]) == 0)
    print(f"conf-keys self-test: {passed}/{total} checks passed")
    return 0 if total == passed else 1


if __name__ == "__main__":
    sys.exit(main())
