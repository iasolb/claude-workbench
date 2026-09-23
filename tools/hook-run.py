#!/usr/bin/env python3
"""hook-run.py: run one hook by NAME, on either machine, with no new permission
entry, ever.

    python tools/hook-run.py <name>      run it, print its output, return its rc
    python tools/hook-run.py --list      every hook this machine can run
    python tools/hook-run.py --self-test

WHY THIS EXISTS (Ian, 2026-09-22, after a prompt for a hook written minutes
earlier: "how have we still not fucking fixed this shit ... tons or
permissions").

**THE PERMISSION MODEL CANNOT EXPRESS "THE HOOKS DIRECTORY", AND THAT IS THE
WHOLE PROBLEM.** `rules/claude/permission-loops.md` measured it twice: a `*`
inside a path token matches nothing on the Claude matcher, and an entry ending
at a `/` is dead because `:*` requires a following space. So a directory grant
is impossible and the documented options were one entry per script, or widening
to the command BEFORE the path. For a `.ps1` there is no command before the
path, so scripts got one entry each, forever.

Measured that day: 23 `.ps1` and 23 `.sh` in `hooks/`, and the allow list
covered them almost entirely at the WORKBENCH path
(`Documents/ai/claude/claude-workbench/hooks/`) and the MAC path, not at
`ai-memory-bank/hooks/` where they actually live and run. So testing any hook
from the real path prompted, every time, and the fix on offer was ~90 more
entries of a shape that had already failed.

**THE INTERPRETER IS THE COMMAND BEFORE THE PATH.**
`PowerShell(...python.exe:*)` and `Bash(...python.exe:*)` are both already
allowlisted, and a `:*` really does cover any arguments (verified 2026-08-12).
So routing hooks through a Python runner needs NO new entry for this tool and
NO new entry for any hook added after it. That is the widening the rules
already sanction, applied where it actually pays.

**THIS IS NOT `run-hook.sh` AND DOES NOT REPLACE IT.** That wrapper is what
settings.json REGISTERS: it bounds the run, drains stdin, and exists so a hook
cannot hang session start. It is also a deliberate no-op on Windows while the
double-registration migration finishes. This tool is for a SESSION invoking a
hook by hand, which is a different job with a different failure mode, and it
is the one that was costing clicks.

PLATFORM SELECTION HAPPENS HERE, ONCE (`rules/shared/dev-practices.md`).
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

# Not hooks: the wrapper settings.json registers, and test files.
NOT_A_HOOK = {"run-hook", "portable"}


def is_windows() -> bool:
    return platform.system() == "Windows"


def script_for(name: str) -> Path | None:
    """The script this machine would run for `name`, or None.

    Windows prefers `.ps1` and falls back to `.sh`, because a few hooks are
    POSIX-only and running the wrong one is worse than saying so.
    """
    order = (".ps1", ".sh") if is_windows() else (".sh", ".ps1")
    for ext in order:
        candidate = HOOKS / f"{name}{ext}"
        if candidate.is_file():
            return candidate
    return None


def known() -> list[str]:
    names = set()
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
        # AN UNKNOWN NAME AND A SILENT HOOK MUST NOT LOOK ALIKE. A hook that
        # runs and prints nothing is normal (most print only on a finding); a
        # name nobody has is a typo, and returning 0 for it would read as
        # "checked, all clear" (`rules/shared/memory-integrity.md` rule 2).
        print(f"hook-run: no hook named {name!r} on this machine "
              f"({platform.system()}). Known: {', '.join(known())}",
              file=sys.stderr)
        return 2
    # stdin is CLOSED, not inherited. Twelve hooks do `INPUT="$(cat)"`, and a
    # hook invoked by hand with an open stdin blocks forever on it.
    try:
        done = subprocess.run(command_for(script), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        print(f"hook-run: cannot run {script.name}: {exc}", file=sys.stderr)
        return 3
    except subprocess.TimeoutExpired:
        print(f"hook-run: {script.name} did NOT finish in {timeout}s, so "
              f"whatever it checks was NOT checked. This is the failure mode "
              f"run-hook.sh exists to bound at session start.", file=sys.stderr)
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
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    names = known()
    want("the hooks directory is found and is not empty", len(names) > 5)
    want("the wrapper settings.json registers is NOT offered as a hook",
         "run-hook" not in names)
    want("a .test.sh file is not offered as a hook",
         not any(n.endswith(".test") for n in names))
    want("every listed name resolves to a script on this machine",
         all(script_for(n) is not None for n in names))
    want("the hook this tool was written for is listed",
         "drivers-app-open" in names)

    # PLATFORM SELECTION, the thing this tool centralises.
    chosen = script_for("drivers-app-open")
    want("Windows picks the PowerShell script, POSIX picks the shell one",
         chosen.suffix == (".ps1" if is_windows() else ".sh"))
    want("a .ps1 is invoked through powershell with -File",
         command_for(HOOKS / "x.ps1")[:2] == ["powershell", "-NoProfile"]
         and command_for(HOOKS / "x.ps1")[-2] == "-File")
    want("a .sh is invoked through bash",
         command_for(HOOKS / "x.sh")[0] == "bash")

    # AN UNKNOWN NAME IS NOT A QUIET SUCCESS. Red-prove by returning 0 from the
    # `script is None` branch: this flips.
    want("an unknown hook name returns nonzero, never 0",
         run("no-such-hook-anywhere") != 0)
    want("and a known name resolves rather than being refused",
         script_for("time-print") is not None)

    if fails:
        print(f"\nhook-run self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nhook-run self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
