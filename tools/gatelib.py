#!/usr/bin/env python3
"""gatelib.py: turn a stored gate string into one that runs HERE.

WHY THIS EXISTS. Measured 2026-09-14 against the live `jobs` table: 183 of the
stored gates name the system interpreter by ABSOLUTE WINDOWS PATH, and many
also name their script by an absolute path inside a worktree that exists on one
machine only. So **no driver pod can run any existing order's gate as written**,
and neither can the Mac. That is the blocker on moving the executor into the
cluster, and it is order DATA rather than cluster work
(`cluster/stored_gates_are_windows_paths`).

THE FIX IS A RESOLVER, NOT 183 HAND EDITS. Rewriting every order file by hand is
the shape golden rule A3 bans: it drifts, it misses the ones nobody opened, and
it has to be redone the next time a path moves. One function that every consumer
calls is a mechanism.

WHAT IT DOES, and nothing more:

  1. The leading INTERPRETER token, in any spelling this repo has actually
     emitted, becomes the interpreter running here (`sys.executable` by
     default, `python3` in a pod).
  2. An absolute path pointing INTO a worktree is cut back to its repo-relative
     tail, because the gate already runs with the lane as its working directory
     (`tools/run-gate.py`).
  3. Backslash separators in the surviving relative paths become forward
     slashes, which both platforms accept.

WHAT IT DELIBERATELY DOES NOT DO. It never rewrites a PROJECT venv
(example-project, example-project, the loader venvs): those interpreters
belong to repos a memory-bank pod does not have, so silently repointing them at
`python3` would produce a gate that runs and asserts nothing, which is worse
than one that refuses. `is_portable()` reports them, and they stay a phase-2
problem.

    reads : nothing
    writes: nothing
"""

from __future__ import annotations

import re
import os
import sys
from pathlib import Path

# Set GATE_REPO_NAME when the repository directory name differs from its slug.
REPO_NAME = os.environ.get(
    "GATE_REPO_NAME", Path(__file__).resolve().parents[1].name)
SYSTEM_PYTHON = ("python", "python3")
SYSTEM_PYTHON_PATH = re.compile(
    r"^(?:[A-Za-z]:[\\/].*[\\/]AppData[\\/]Local[\\/]Programs[\\/]"
    r"Python[\\/]Python\d+[\\/]|/usr/bin/|/usr/local/bin/|"
    r"/opt/homebrew/bin/)python(?:\d+(?:\.\d+)?)?(?:\.exe)?$",
    re.I)

# A venv belonging to ANOTHER repo. Named so `is_portable` can say WHICH kind of
# unportable a gate is, instead of lumping it in with the fixable ones.
PROJECT_VENV = re.compile(
    r"^[A-Za-z]:[\\/]|^/[a-z]/|\.venv[\\/]Scripts|\.pyenv", re.I)

# AN ABSOLUTE PATH INTO THE REPO THE GATE ALREADY RUNS IN. Two shapes, because
# a gate is run with the LANE as its working directory (`tools/run-gate.py`):
# a worktree path up to and including the lane dir, and a path into the main
# checkout. Both collapse to the same repo-relative tail. Nothing else is
# touched: a path into ANOTHER repo is a real dependency, not a spelling.
_IN_REPO = (
    re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s\\/]+[\\/])*worktrees[\\/][^\s\\/]+[\\/]",
               re.I),
    re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s\\/]+[\\/])*"
               + re.escape(REPO_NAME) + r"[\\/]",
               re.I),
)


def _split_leading_interpreter(gate: str) -> tuple[str, str]:
    """(interpreter, rest). Quoting is handled because drivers emit it."""
    g = gate.strip()
    if g[:1] in ('"', "'"):
        quote = g[0]
        end = g.find(quote, 1)
        if end != -1:
            return g[1:end], g[end + 1:].lstrip()
    head, _, rest = g.partition(" ")
    return head, rest


def resolve(gate: str, python: str | None = None, repo: str | None = None) -> str:
    """The gate as it should be RUN on this machine.

    `python` defaults to the interpreter running this process. `repo` is the
    order's `repo:` field. The current repository slug is recognized by its
    final path component, configured by GATE_REPO_NAME or the repository's
    directory name. Paths into other repositories remain dependencies.
    """
    if not gate or not gate.strip():
        return ""
    interp, rest = _split_leading_interpreter(gate)
    runner = python or sys.executable
    in_repo = not repo or repo.rstrip("/\\").split("/")[-1].split("\\")[-1] == REPO_NAME

    is_system_python = (interp in SYSTEM_PYTHON
                        or (SYSTEM_PYTHON_PATH.match(interp)
                            and not re.search(r"\.venv[\\/]|\.pyenv", interp,
                                              re.I)))
    if is_system_python:
        for pattern in _IN_REPO if in_repo else _IN_REPO[:1]:
            rest = pattern.sub("", rest)
        rest = _relative_slashes(rest)
        return f"{runner} {rest}".strip()

    # NOT OURS TO REWRITE. Returned untouched so the caller sees the real
    # command and can refuse it, rather than a silently repointed one.
    return gate.strip()


def _relative_slashes(rest: str) -> str:
    """Backslashes in a RELATIVE path become forward slashes.

    Only the relative ones: an absolute Windows path that survived rule 2 is
    left alone, because mangling it would hide the fact that it is unportable.
    """
    out = []
    for token in rest.split(" "):
        if "\\" in token and not re.match(r"^[A-Za-z]:", token):
            token = token.replace("\\", "/")
        out.append(token)
    return " ".join(out)


def is_portable(gate: str) -> tuple[bool, str, str]:
    """(portable, code, why). A gate is portable when running it needs nothing
    that exists on one machine only.

    THE CODE IS THE MACHINE-READABLE HALF and the prose is for a person. They
    are returned separately on purpose: the first version of `audit()` grouped
    by splitting this sentence on its first colon, which cut every Windows path
    at `C` and produced the category "the gate runs C". A tool must never scrape
    prose, including its own (`rules/shared/memory-integrity.md` rule 2).
    """
    if not gate or not gate.strip():
        return False, "no-gate", ("there is no gate, and a missing gate is not "
                                  "a pass")
    # RESOLVE AGAINST A CANONICAL INTERPRETER, never this machine's. On the PC
    # `sys.executable` IS an absolute Windows path, so resolving with the
    # default made every gate look unportable, including the 183 that are the
    # whole point of this function. The question is whether the gate's SHAPE
    # survives a move, not what this machine happens to be running.
    resolved = resolve(gate, python="python3")
    interp, _rest = _split_leading_interpreter(resolved)
    if PROJECT_VENV.match(interp):
        return False, "project-venv", (
            f"the gate runs {interp}, an interpreter that belongs to another "
            f"repo and does not exist in a pod or on the Mac")
    for token in resolved.split(" "):
        if re.match(r"^[A-Za-z]:[\\/]", token) or token.startswith("/"):
            return False, "absolute-path", (
                f"the gate names an absolute machine path: {token}")
    return True, "", ""


def self_test() -> int:
    fails: list[str] = []
    n = 0

    def want(cond: bool, label: str, got=None) -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{label}  [got {got!r}]")

    # Generic examples cover drive and POSIX spellings without a real account
    # directory or repository slug.
    windows_python = (r"C:\example\AppData\Local\Programs\Python"
                      r"\Python313\python.exe")
    got = resolve(
        windows_python + " C:/workspace/worktrees/sample-lane/tools/check.py "
        "--self-test", python="python3")
    want(got == "python3 tools/check.py --self-test",
         "an absolute interpreter and lane path collapse", got)

    got = resolve(
        windows_python + r" C:\workspace\worktrees\sample-lane\tools\check.py "
        r"--self-test", python="python3")
    want(got == "python3 tools/check.py --self-test",
         "the backslash spelling collapses identically", got)

    got = resolve(windows_python + r" tools\jobsync.py --self-test",
                  python="python3")
    want(got == "python3 tools/jobsync.py --self-test",
         "a relative script keeps its arguments and gains slashes", got)

    got = resolve(windows_python +
                  " tools/gate-selftest.py tools/check-types.py --min-checks 4",
                  python="python3")
    want(got == "python3 tools/gate-selftest.py tools/check-types.py "
                "--min-checks 4",
         "a gate that takes another script as an argument is untouched", got)

    # A wrapper in another repository is a real dependency and must survive.
    farm = (windows_python + f" C:/workspace/{REPO_NAME}/tools/gatefloor.py "
            "--min 10 -- C:/workspace/example-project/.venv/Scripts/"
            "python.exe -m pytest tests/test_sample.py -q")
    got = resolve(farm, python="python3", repo="example-owner/example-project")
    want(f"{REPO_NAME}/tools/gatefloor.py" in got,
         "an external order keeps this repository's wrapper path", got)
    got = resolve(farm, python="python3")
    want(got.startswith("python3 tools/gatefloor.py"),
         "an order in this repository collapses its wrapper path", got)

    got = resolve(windows_python +
                  f" C:/workspace/{REPO_NAME}/tools/check.py --run",
                  python="python3")
    want(got == "python3 tools/check.py --run",
         "an absolute path into this checkout collapses", got)

    other = windows_python + " C:/workspace/example-project/run.py"
    want("example-project/run.py" in resolve(other, python="python3")
         and "C:/workspace/example-project" in resolve(other, python="python3"),
         "a path into another repository stays absolute",
         resolve(other, python="python3"))

    got = resolve(f'"{windows_python}" tools/check.py', python="python3")
    want(got == "python3 tools/check.py",
         "a quoted interpreter is recognized", got)

    project_python = (r"C:\workspace\example-project\.venv\Scripts\python.exe "
                      "-m pytest tests -q")
    want(resolve(project_python, python="python3") == project_python.strip(),
         "a project venv is returned untouched", resolve(project_python))
    ok, code, why = is_portable(project_python)
    want(ok is False and code == "project-venv" and "another repo" in why,
         "a project venv is reported with a code and reason", (ok, code, why))

    ok, code, why = is_portable(windows_python + r" tools\jobsync.py --self-test")
    want(ok is True and code == "",
         "a system-python gate is portable once resolved", (ok, code, why))
    ok, code, why = is_portable("/usr/bin/python3 tools/jobsync.py --self-test")
    want(ok is True and code == "",
         "a POSIX system-python gate is portable", (ok, code, why))

    ok, code, why = is_portable("")
    want(ok is False and code == "no-gate",
         "an empty gate is unportable", (ok, code, why))

    ok, code, _why = is_portable(
        windows_python + " C:/workspace/example-project/run.py")
    want(code == "absolute-path",
         "a Windows absolute path is recognized", code)
    ok, code, why = is_portable(
        windows_python + " /opt/example-project/run.py")
    want(ok is False and code == "absolute-path"
         and "absolute machine path" in why,
         "an absolute path outside a worktree is not rewritten", (ok, why))

    for f in fails:
        print(f"FAIL {f}")
    print(f"gatelib self-test: {n - len(fails)}/{n} checks passed")
    return 1 if fails else 0


def audit() -> int:
    """Classify every stored gate: which ones a pod could run, and why not.

    A6: this question gets asked every time the cluster move is touched, so it
    is a command that RESOLVES from the store rather than a number anyone
    remembers. It reads and writes nothing.
    """
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "memdb", pathlib.Path(__file__).resolve().parent / "memdb.py")
    memdb = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(memdb)

    conn = memdb._connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, gate FROM jobs WHERE gate IS NOT NULL "
                    "AND gate <> '' ORDER BY id")
        rows = cur.fetchall()
    finally:
        conn.close()

    portable, blocked, sample = [], {}, {}
    for job_id, gate in rows:
        ok, code, why = is_portable(gate)
        if ok:
            portable.append(job_id)
        else:
            blocked.setdefault(code, []).append(job_id)
            sample.setdefault(code, why)

    total = len(rows)
    print(f"{len(portable)} of {total} stored gate(s) can run in a pod "
          f"once resolved ({100 * len(portable) // max(total, 1)}%)")
    for code, ids in sorted(blocked.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(ids):>3} {code}: {sample[code]}")
        print(f"      e.g. {', '.join(ids[:3])}")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--audit" in sys.argv:
        sys.exit(audit())
    print(__doc__.strip().splitlines()[0])
    print("pass --audit to classify every stored gate, or --self-test")
    sys.exit(0)
