"""Run the LIVE shell-form guard the way the harness runs it and assert its verdicts.

Exists because the guard is FAIL-OPEN by construction: a crash exits 0 with no
output, which is indistinguishable from "no opinion". On 2026-09-04 an
apostrophe in the guidance string closed the shell quote, so the Python body
never compiled and the guard allowed EVERY form it was written to deny, for an
unknown length of time, while 33 files in this repo cited it as working.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Set SHELL_FORM_GUARD_DIR when the guard scripts are kept outside the repo.
GUARD_DIR = os.environ.get("SHELL_FORM_GUARD_DIR", os.path.join(REPO, "hooks"))
GUARD_SH = os.path.join(GUARD_DIR, "shell-form-guard.sh")
GUARD_PS1 = os.path.join(GUARD_DIR, "shell-form-guard.ps1")

# WHICH GUARD THIS MACHINE ACTUALLY RUNS, and this file could not answer that
# question until 2026-09-16.
#
# It invoked the `.sh` by path, which Windows cannot execute at all: the run
# died with `OSError: [WinError 193] %1 is not a valid Win32 application`
# before a single assertion. So the guard's assertions had only ever run on the
# Mac, while THE PC IS THE EXECUTOR: every driver, every lane and every gate
# runs here, behind `shell-form-guard.ps1`, which nothing had ever tested.
#
# That is the 2026-09-04 outage waiting to happen again on the other half of
# the fleet. The guard is FAIL-OPEN by construction, so a broken Windows guard
# allows every form it exists to deny and looks exactly like a clean session.
# `tools/self-test-all.py` reported NO-TEST for this file, which reads as "no
# test written" rather than "the test cannot run on this machine".
if sys.platform == "win32":
    GUARD = GUARD_PS1
    GUARD_ARGV = ["powershell.exe", "-NoProfile", "-NonInteractive",
                  "-ExecutionPolicy", "Bypass", "-File", GUARD_PS1]
else:
    GUARD = GUARD_SH
    GUARD_ARGV = [GUARD_SH]

# (name, command, must_deny). The deny cases are the Never list in
# docs/command-forms.md; the allow cases are forms a session must be able to run.
CASES: list[tuple[str, str, bool]] = [
    ("cd chain",            "cd /tmp/example-home/dev && wc -c CLAUDE.md", True),
    ("pipeline",            "head -3 /etc/hosts | od -c", True),
    ("semicolon",           "sh -n /tmp/x.sh; echo done", True),
    ("bare python",         "python tools/lint.py", True),
    ("bare pytest",         "pytest -q", True),
    ("bare pip",            "pip install requests", True),
    ("inline -c",           '/usr/bin/python3 -c "print(1)"', True),
    ("powershell wrapper",  'powershell -Command "Get-Process"', True),
    ("escaped whitespace",  "ls /c/Program\\ Files", True),
    ("escaped dollar",      'git commit -m "cost \\$5"', True),
    ("background amp",      "/usr/bin/python3 /abs/x.py &", True),
    ("or chain",            "false || true", True),
    ("brace expansion",     "wc -c /c/example-home/x/memory/{a,b,c}.md", True),
    ("brace in quoted sed", "sed 's/x/{a,b}/' /c/example-home/x/f.md", False),
    ("brace in dq echo",    'echo "{a,b}"', False),

    ("plain git -C",        "git -C /workspace/example-repo status --short", False),
    # git's -C is a PATH flag. The inline-code rule was case-insensitive, so it
    # read -C as python's -c and blocked nearly every command this repo runs.
    # Invisible while the guard never compiled; live the moment it did.
    ("git -C via interpreter", "/usr/bin/python3 /abs/pc.py run git -C /abs/repo log --oneline", False),
    ("absolute -m",         "/usr/bin/python3 -m pytest -q", False),
    ("absolute script",     "/usr/bin/python3 /workspace/example-repo/tools/lint.py", False),
    # A && inside a quoted message is TEXT, not a chain. The guard once blocked
    # its own commit this way, so a false positive here is a real regression.
    ("amp inside quotes",   'git -C /abs commit -m "a && b" -- x', False),
    # Rules 22-24. The deny cases are VERBATIM prompts the owner hit 2026-09-27
    # (tools/prompt-report.py); each allow case is the spelling that replaces it.
    # Rule 29, 2026-09-27: `opencode debug config` printed the Zen key expanded,
    # and the absolute-path spelling prompted the owner and dodges the --auto denies.
    ("opencode debug config", "opencode debug config", True),
    ("opencode abs path",   "/c/example-home/AppData/Roaming/npm/node_modules/opencode-ai/bin/opencode.exe run --help", True),
    ("opencode bare",       "opencode --version", False),
    # Rules 31 and 32, 2026-10-17. Both deny cases prompted the owner 2026-10-16.
    ("format equals percent", "git -C /workspace/example-project branch --format=%(refname:short)", True),
    ("format equals quoted", 'git -C /abs log -1 --format="%h %s"', True),
    ("format mentioned in a message", 'git -C /abs commit -m "drop --format=%h" -- x', False),
    ("plain oneline",       "git -C /abs log -3 --oneline", False),
    ("guessed Python312",   "/c/example-home/AppData/Local/Programs/Python/Python312/python.exe /c/x/tools/fact.py --help", True),
    ("Python313",           "/c/example-home/AppData/Local/Programs/Python/Python313/python.exe /c/x/tools/lint.py", False),
]

# The same, sent as the POWERSHELL tool: these are PowerShell forms, and some
# Bash-only rules (POSIX paths) would deny the allow cases for the wrong reason.
PS_CASES: list[tuple[str, str, bool]] = [
    ("confirm false",       r"Remove-Item -Confirm:$false C:\example-home\state\drivers\x.verdict", True),
    ("paren property",      r"(Get-Item C:\example-home\notes\MEMORY.md).LastWriteTime", True),
    ("comma paths",         r"Test-Path C:\workspace\loader-a, C:\workspace\example-project", True),
    ("plain remove",        r"Remove-Item C:\example-home\state\drivers\x.verdict", False),
    ("mtime via select",    r"Get-Item C:\example-home\x.md | Select-Object LastWriteTime", False),
    ("comma property list", "Get-ChildItem C:\\x | Select-Object Name, Length", False),
    ("ps opencode debug config", "opencode debug config", True),
    ("ps opencode abs path", r"C:\example-home\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe stats", True),
    ("ps guessed Python312", r"C:\example-home\AppData\Local\Programs\Python\Python312\python.exe C:\x\tools\fact.py", True),
    ("ps docker format equals", 'docker inspect --format="{{json .HostConfig}}" example-app', True),
    ("ps docker format space", 'docker inspect --format "{{json .HostConfig}}" example-app', False),
]


def ask(command: str, tool: str = "Bash", argv: list[str] | None = None) -> tuple[int, str, str]:
    """Feed one command to the guard exactly as a PreToolUse hook is fed."""
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    p = subprocess.run(argv or GUARD_ARGV, input=payload, capture_output=True,
                       text=True, timeout=30)
    return p.returncode, p.stdout, p.stderr


def sh_body() -> str | None:
    """The Python the .sh guard hands to `python -c`, byte for byte.

    BOTH guard variants execute on Windows, yet until 2026-10-17 this file
    tested only the .ps1 here, so the .sh body, the one that already failed
    open once on an apostrophe, had never been asserted on the executor. The
    shell passes the body single-quoted, so every backslash arrives literally:
    running the extracted text with `python -c` is what the hook does.
    """
    with open(GUARD_SH, encoding="utf-8") as fh:
        m = re.search(r"\"\$PY\" -c '\n(.*?)\n' \|\| exit 0", fh.read(), re.S)
    return m.group(1) if m else None


def decision_of(stdout: str) -> str:
    """"" means the guard had no opinion, which the harness reads as allow."""
    if not stdout.strip():
        return ""
    try:
        return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return "UNPARSEABLE"


def main() -> int:
    # `--self-test` IS the default behaviour, accepted as an alias rather than
    # implemented twice. This file is already nothing but assertions against
    # the live guard; the flag exists so `tools/self-test-all.py` stops
    # reporting NO-TEST for a tool that is entirely test. A second, mocked
    # self-test here would be the exact fault the guard's own history warns
    # about: the 2026-09-04 outage was invisible precisely because everything
    # except the real guard agreed it worked.
    #
    # THE GUARD IS FAIL-OPEN, so "no findings" and "never ran" look identical
    # from outside. That is why the `denied == 0` check below exists and why
    # this must run the REAL hook script, never a copy of its logic.
    if not os.path.exists(GUARD):
        print(f"FAIL  guard missing: {GUARD}")
        return 1
    print(f"guard under test: {os.path.basename(GUARD)}")

    failures: list[str] = []
    denied = 0

    runs = ([(n, c, d, "Bash", None) for n, c, d in CASES]
            + [(n, c, d, "PowerShell", None) for n, c, d in PS_CASES])
    if sys.platform == "win32":
        body = sh_body()
        if body is None:
            failures.append("the .sh guard body could not be found, so the .sh variant is untested")
        elif "'" in body:
            # The 2026-09-04 outage: an apostrophe closes the single-quoted shell
            # string, the body never compiles under Bash, and the guard allows
            # everything. Running the extracted text would not see it.
            failures.append("the .sh guard body contains an apostrophe, which closes its shell quote: under Bash the guard fails open")
        else:
            # From a FILE, not `-c`: the body is longer than the Windows
            # command-line cap (WinError 206). Same text, same interpreter.
            fd, body_path = tempfile.mkstemp(suffix=".py", prefix="guard-sh-body-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            atexit.register(os.remove, body_path)
            sh_argv = [sys.executable, body_path]
            runs += ([("sh: " + n, c, d, "Bash", sh_argv) for n, c, d in CASES]
                     + [("sh: " + n, c, d, "PowerShell", sh_argv) for n, c, d in PS_CASES])
    for name, command, must_deny, tool, argv in runs:
        rc, out, err = ask(command, tool, argv)

        # A crash is the fail-open signature and must never pass as "allow".
        if "Traceback" in err or "SyntaxError" in err or "not found" in err:
            failures.append(f"{name}: guard CRASHED (fail-open). stderr={err.strip()[:160]!r}")
            continue

        got = decision_of(out)
        if got == "UNPARSEABLE":
            failures.append(f"{name}: guard emitted output the harness cannot parse: {out[:160]!r}")
            continue

        if must_deny:
            if got == "deny":
                denied += 1
            else:
                failures.append(f"{name}: expected deny, got {got or 'allow (no output)'}")
        else:
            if got:
                failures.append(f"{name}: expected allow, got {got}")

    # If nothing was denied the guard is inert, whatever the per-case results say.
    # Empty-because-broken and empty-because-clean must not share a verdict.
    if denied == 0:
        failures.append("guard denied NOTHING across every banned form: it is inert")

    for f in failures:
        print(f"FAIL  {f}")
    # Worded so tools/gatefloor.py can read the count off it.
    print(f"guard: {len(runs) - len(failures)}/{len(runs)} assertions passed ({denied} denials)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
