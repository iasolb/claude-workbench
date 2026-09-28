"""Check which environment hooks read workbench.conf keys and what this
machine lacks."""

import argparse
import os
import re
import sys
from pathlib import Path
import tempfile

# The exact pattern hooks search for in shell/PS1 files
KEY_READ = __import__("re").compile(r"\^([A-Z][A-Z0-9_]*)=")


def keys_read(text: str) -> set[str]:
    """Return the set of key names read from the given text.

    A key name is detected by the pattern in KEY_READ.
    """
    return set(KEY_READ.findall(text))


def keys_present(conf_text: str) -> set[str]:
    """Return the set of key names that are present in the config text.

    A line is considered present if it starts with NAME= where NAME matches
    [A-Z][A-Z0-9_]* and the line is not blank or a comment.
    """
    present: set[str] = set()
    for raw in conf_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        m = __import__("re").match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m:
            present.add(m.group(1))
    return present


def missing(hooks_dir: Path, conf_text: str) -> dict[str, list[str]]:
    """Return keys read by hooks but not present in conf_text.

    Inspect every *.sh and *.ps1 file directly inside hooks_dir (no subdirs),
    collect which keys they read, and report only those keys not present in the
    config.
    The return value maps key -> sorted list of hook file names that read it.
    """
    present = keys_present(conf_text)
    result: dict[str, list[str]] = {}
    if not hooks_dir.exists():
        return result
    for p in sorted(hooks_dir.iterdir(), key=lambda x: x.name):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".sh", ".ps1"}:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for key in keys_read(content):
            if key not in present:
                result.setdefault(key, []).append(p.name)
    # sort hook names for deterministic output
    for k in list(result.keys()):
        result[k] = sorted(set(result[k]))
    return result


def _read_file(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--hooks", type=str, default=None)
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    # Resolve hooks directory (default path if not overridden)
    if args.hooks:
        hooks_dir = Path(args.hooks)
    else:
        hooks_dir = Path(__file__).resolve().parents[1] / "hooks"

    # Determine config path
    if "WORKBENCH_CONF" in os.environ and os.environ["WORKBENCH_CONF"]:
        conf_path = Path(os.environ["WORKBENCH_CONF"])
    else:
        conf_path = Path.home() / ".claude" / "workbench.conf"

    # Read config (fail if unreadable)
    try:
        conf_text = _read_file(conf_path)
    except OSError:
        print(f"conf-keys: CANNOT READ {conf_path}")
        return 3

    missing_map = missing(hooks_dir, conf_text)
    if missing_map:
        for name in sorted(missing_map.keys()):
            hooks_list = ", ".join(sorted(set(missing_map[name])))
            print(f"conf-keys: MISSING {name} (read by {hooks_list})")
        return 1

    print("conf-keys: every key the hooks read is set")
    return 0


def _self_test() -> int:
    import textwrap
    total = 0
    passed = 0

    def want(label: str, cond: bool):
        nonlocal total, passed
        total += 1
        if cond:
            passed += 1
        else:
            print(f"self-test fail: {label}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        hooks = tmp_path / "hooks"
        hooks.mkdir()

        # Hook that reads FOO and BAR
        (hooks / "hook1.sh").write_text(
            "FOO=\"$(sed -n 's/^FOO=//p' \"$CONF\")\"\n", encoding="utf-8")
        (hooks / "hook2.ps1").write_text(
            "if ($line -match '^BAR=(.+)$') { $bar = $Matches[1] }\n", encoding="utf-8")
        # Non-hook file should be ignored
        (hooks / "ignore.txt").write_text("sed -n 's/^IGNORED=//p'\n", encoding="utf-8")

        conf_path = tmp_path / "workbench.conf"
        conf_path.write_text("BAR=exists\nFOO=set\n", encoding="utf-8")

        # 1) keys_read sed shape finds BAR (via sed-like line)
        want("keys_read sed shape", "BAR" in keys_read("x=\"$(sed -n 's/^BAR=//p' \\\"$CONF\\\")\""))
        # 2) keys_read dash-match shape
        want("keys_read dash-match shape", "REPO_PATH" in keys_read("if ($line -match '^REPO_PATH=(.+)$') { }"))
        # 3) keys_read nothing when no NAME present
        want("keys_read nothing", len(keys_read("nothing=happens")) == 0)
        # 4) keys_present ignores # comments
        want("keys_present ignores comments", keys_present("# FOO=1\nBAR=2") == {"BAR"})

        # 5-7: missing behavior
        missing_map = missing(hooks, "BAR=exists\n")
        want("missing reports missing key", "FOO" in missing_map)
        want("missing reports correct hook", missing_map.get("FOO") == ["hook1.sh"])
        # 6) missing does NOT report a key the fixture config has
        missing_map2 = missing(hooks, "BAR=exists\nFOO=val\n")
        want("missing hides present keys", "BAR" not in missing_map2 and "FOO" not in missing_map2)
        # 7) missing ignores non-hook file (ignore.txt)
        want("missing ignores a non-hook file", "IGNORED" not in missing(hooks, ""))

        # 8) main returns 3 when WORKBENCH_CONF points to non-existent
        old = os.environ.get("WORKBENCH_CONF")
        os.environ["WORKBENCH_CONF"] = "/path/does/not/exist.conf"
        code = main(["--hooks", str(hooks)])
        want("main returns 3 on unreadable conf", code == 3)
        if old is None:
            os.environ.pop("WORKBENCH_CONF", None)
        else:
            os.environ["WORKBENCH_CONF"] = old

        # 9) main returns 0 when nothing is missing (override to fixture)
        os.environ["WORKBENCH_CONF"] = str(conf_path)
        code = main(["--hooks", str(hooks)])
        want("main returns 0 when nothing missing", code == 0)

        print(f"conf-keys self-test: {passed}/{total} checks passed")
        return 0 if total == passed else 1


if __name__ == "__main__":
    sys.exit(main())
