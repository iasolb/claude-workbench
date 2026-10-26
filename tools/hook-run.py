#!/usr/bin/env python3
"""hook-run.py: run one repository hook by name, on the current platform.

    python tools/hook-run.py <name>      run it, print its output, return its rc
    python tools/hook-run.py --list      every hook this machine can run
    python tools/hook-run.py --self-test

Hooks are discovered in the sibling hooks/ directory. Windows prefers a
PowerShell script and falls back to a shell script; other platforms prefer the
shell script and fall back to PowerShell. The runner closes standard input and
times out, so a hook cannot wait forever for interactive input.

WHY (2026-09-22): invoking hooks by path caused repeated permission prompts,
so a named runner keeps platform selection and invocation in one place.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HOOKS = REPO / "hooks"
NOT_A_HOOK = {"run-hook", "portable"}


def is_windows() -> bool:
    return platform.system() == "Windows"


def script_for(name: str) -> Path | None:
    """The script this platform would run for name, or None."""
    order = (".ps1", ".sh") if is_windows() else (".sh", ".ps1")
    for ext in order:
        candidate = HOOKS / f"{name}{ext}"
        if candidate.is_file():
            return candidate
    return None


def known() -> list[str]:
    names = set()
    if not HOOKS.is_dir():
        return []
    for path in HOOKS.iterdir():
        if path.suffix not in (".ps1", ".sh") or not path.is_file():
            continue
        stem = path.name[: -len(path.suffix)]
        if stem in NOT_A_HOOK or stem.endswith(".test"):
            continue
        names.add(stem)
    return sorted(names)


def command_for(script: Path) -> list[str]:
    if script.suffix == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script)]
    return ["bash", str(script)]


def run(name: str, timeout: int = 60) -> int:
    script = script_for(name)
    if script is None:
        print(f"hook-run: no hook named {name!r} on this machine "
              f"({platform.system()}). Known: {', '.join(known())}",
              file=sys.stderr)
        return 2
    try:
        done = subprocess.run(command_for(script), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        print(f"hook-run: cannot run {script.name}: {exc}", file=sys.stderr)
        return 3
    except subprocess.TimeoutExpired:
        print(f"hook-run: {script.name} did NOT finish in {timeout}s, so its "
              "check was not completed.", file=sys.stderr)
        return 124
    if done.stdout:
        print(done.stdout, end="")
    if done.stderr:
        print(done.stderr, end="", file=sys.stderr)
    if done.returncode != 0:
        print(f"hook-run: {script.name} exited {done.returncode}",
              file=sys.stderr)
    return done.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hook-run")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.list:
        for name in known():
            script = script_for(name)
            print(f"  {name:<24} {script.name if script else 'NOT ON THIS MACHINE'}")
        return 0
    if not a.name:
        ap.error("give a hook name, or --list, or --self-test")
    return run(a.name, a.timeout)


def self_test() -> int:
    import tempfile

    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    global HOOKS
    saved = HOOKS
    with tempfile.TemporaryDirectory() as tmp:
        HOOKS = Path(tmp)
        (HOOKS / "sample.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (HOOKS / "sample.ps1").write_text("exit 0\n", encoding="utf-8")
        (HOOKS / "sample.test.sh").write_text("exit 0\n", encoding="utf-8")
        (HOOKS / "run-hook.sh").write_text("exit 0\n", encoding="utf-8")
        want("the hooks directory is discovered", known() == ["sample"])
        chosen = script_for("sample")
        want("the platform's preferred script is selected",
             chosen.suffix == (".ps1" if is_windows() else ".sh"))
        want("PowerShell scripts use powershell -File",
             command_for(HOOKS / "x.ps1")[:2] == ["powershell", "-NoProfile"]
             and command_for(HOOKS / "x.ps1")[-2] == "-File")
        want("shell scripts use bash", command_for(HOOKS / "x.sh")[0] == "bash")
        want("an unknown hook is nonzero, never a quiet success",
             run("missing-hook") != 0)
    HOOKS = saved

    if fails:
        print(f"\nhook-run self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nhook-run self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
