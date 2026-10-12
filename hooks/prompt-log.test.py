"""Test that the prompt log never writes a secret into git.

prompts/<env>.tsv is committed. On 2026-10-11 the Mac's copy was found holding a
billing app's client secret verbatim, because a command set it inline and the
hook logged the command as typed. Both hook variants now redact before writing:

- prompt-log.ps1 is driven end to end against a throwaway repo;
- prompt-log.sh exits at once under MINGW, so its perl block is lifted out of
  the file and run on the same samples, which tests the exact text the Mac runs.

Each redaction is paired with a discrimination case that must come through
unchanged (rules/shared/memory-integrity.md rule 2).

Run it:
    python hooks/prompt-log.test.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE_B64 = "+Fk" + "Q9zT" * 9 + "="
FAKE_GH = "gh" + "p_" + "FakeFake" * 3
FAKE_BEARER = "abc" + "DEF123" * 3

REDACT = [
    (f'python3 set-env.py "PRIMARY_APPLICATION_SECRET={FAKE_B64}"', FAKE_B64,
     "PRIMARY_APPLICATION_SECRET=[REDACTED]"),
    (f"gh auth login --with-token {FAKE_GH}", FAKE_GH, "[REDACTED]"),
    (f'curl -H "Authorization: Bearer {FAKE_BEARER}" http://x', FAKE_BEARER,
     "Bearer [REDACTED]"),
]
KEEP = [
    "git -C /c/repo status --short",
    'curl -H "Authorization: Bearer $ZEN_API_KEY" http://x',
]

PASS = 0
FAIL = 0


def want(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {detail}")


def ps1_rows(cmds: list) -> str:
    tmp = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["git", "init", "-q", str(tmp / "repo")], check=True)
        conf = tmp / "conf"
        conf.write_text(f"REPO_PATH={tmp / 'repo'}\nMACHINE_BRANCH=testenv\n")
        env = dict(os.environ, WORKBENCH_CONF=str(conf))
        for c in cmds:
            payload = {"hook_event_name": "PermissionRequest", "tool_name": "Bash",
                       "tool_input": {"command": c}}
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(HERE / "prompt-log.ps1")],
                input=json.dumps(payload), capture_output=True, text=True,
                env=env, timeout=60)
        log = tmp / "repo" / "prompts" / "testenv.tsv"
        return log.read_text(encoding="utf-8-sig") if log.is_file() else ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sh_perl(cmd: str) -> str:
    text = (HERE / "prompt-log.sh").read_text(encoding="utf-8")
    block = re.search(r"perl -pe '(.*?)'\)", text, re.S)
    if not block:
        return "<no perl block found in prompt-log.sh>"
    out = subprocess.run(["perl", "-pe", block.group(1)], input=cmd,
                         capture_output=True, text=True, timeout=30)
    return out.stdout


def main() -> int:
    if shutil.which("powershell.exe"):
        print("=== prompt-log.ps1, end to end ===")
        rows = ps1_rows([c for c, _, _ in REDACT] + KEEP)
        want("the log was written at all", rows.count("\n") >= 1 + len(REDACT) + len(KEEP),
             repr(rows[:200]))
        for c, secret, shown in REDACT:
            want(f"ps1 hides the value in: {shown}", secret not in rows and shown in rows)
        for c in KEEP:
            want(f"ps1 keeps unchanged: {c}", c in rows, repr(rows[-300:]))
    else:
        print("SKIP  ps1 half: no powershell.exe here")
    if shutil.which("perl"):
        print("=== prompt-log.sh's perl block, same samples ===")
        for c, secret, shown in REDACT:
            got = sh_perl(c)
            want(f"sh hides the value in: {shown}", secret not in got and shown in got, got)
        for c in KEEP:
            got = sh_perl(c)
            want(f"sh keeps unchanged: {c}", got == c, got)
    else:
        print("SKIP  sh half: no perl here")
    print(f"\nassertions: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 and PASS > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
