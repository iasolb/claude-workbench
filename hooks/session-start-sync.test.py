"""Test for the Windows session-start hook's row-file auto-commit.

The .sh test (session-start-sync.test.sh) cannot exercise the Windows side:
the .sh exits at once under MINGW, so the .ps1 that actually runs on the PC had
no test at all. This drives the real .ps1 against a throwaway repo and never
touches the real one.

Every assertion is paired with its discrimination case: a real leftover alone
must NOT be committed, and a staged unrelated file must NOT ride along with the
row commit (rules/shared/memory-integrity.md rule 2).

Run it:
    python hooks/session-start-sync.test.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# `--hook <path>` points it at another copy, which is how it was proven red
# against the pre-2026-10-11 hook (4 of 10 failed).
HOOK = (Path(sys.argv[sys.argv.index("--hook") + 1]) if "--hook" in sys.argv
        else Path(__file__).resolve().parent / "session-start-sync.ps1")
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


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def run_hook(conf: Path) -> str:
    env = dict(os.environ, WORKBENCH_CONF=str(conf))
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOK)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return out.stdout + out.stderr


def main() -> int:
    if not shutil.which("powershell.exe"):
        print("SKIP  no powershell.exe here; the .sh test covers this machine")
        return 0
    tmp = Path(tempfile.mkdtemp())
    try:
        origin = tmp / "origin.git"
        repo = tmp / "repo"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True,
                       capture_output=True)
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        (repo / "automation").mkdir()
        (repo / "reports" / "go").mkdir(parents=True)
        (repo / "automation" / "driver-errors.csv").write_text("a,b\n")
        (repo / "reports" / "go" / "old.md").write_text("old\n")
        (repo / "notes.md").write_text("notes\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "seed")
        git(repo, "branch", "-M", "windows")
        git(repo, "push", "-q", "-u", "origin", "windows")
        conf = tmp / "conf"
        conf.write_text(
            f"REPO_PATH={repo}\nMACHINE_BRANCH=windows\nSYNC_BRANCHES=windows\n"
        )

        print("=== 1. DISCRIMINATION: a real leftover alone is reported, never committed ===")
        (repo / "notes.md").write_text("notes\nhuman edit\n")
        head = git(repo, "rev-parse", "HEAD")
        out = run_hook(conf)
        want("no auto-commit for a real leftover", "auto-committed" not in out, out)
        want("HEAD unchanged", git(repo, "rev-parse", "HEAD") == head)
        want("the leftover is still reported", "left from a previous session" in out, out)

        print("=== 2. row files are committed by pathspec, beside a staged real leftover ===")
        with open(repo / "automation" / "driver-errors.csv", "a") as f:
            f.write("c,d\n")
        (repo / "reports" / "go" / "new.md").write_text("new\n")
        git(repo, "add", "notes.md")
        head = git(repo, "rev-parse", "HEAD")
        out = run_hook(conf)
        want("says it auto-committed 2 files",
             "auto-committed 2 machine-written row file(s)" in out, out)
        want("a new commit exists", git(repo, "rev-parse", "HEAD") != head)
        changed = set(git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
        want("commit holds the modified row file", "automation/driver-errors.csv" in changed,
             str(changed))
        want("commit holds the untracked go report", "reports/go/new.md" in changed,
             str(changed))
        want("staged notes.md did NOT ride along", "notes.md" not in changed, str(changed))
        want("the real leftover is still reported", "left from a previous session" in out, out)
        want("the row commit was pushed",
             git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/windows"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nassertions: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
