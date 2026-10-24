#!/usr/bin/env python3
"""run-gate.py: run an order's gate IN ITS LANE, and show the real output.

WHY THIS EXISTS. A gate is only meaningful from the directory the order runs in,
and nothing here could do that. A project configuration module opens its
settings file by RELATIVE path, so the suite dies at
import from anywhere but the repo root. Measured 2026-09-13, a session running
that gate from the wrong place got `FileNotFoundError` and read it as the work
being broken, when the work was fine.

So every gate check tonight was either a scratchpad script written from scratch
or a guess. That is golden rule A3's second occurrence several times over.

WHAT IT REFUSES TO DO. It never edits the order, never lands anything, and never
reports a verdict of its own. It runs the declared command in the declared place
and prints exactly what came back, exit code included. Deciding what a red gate
MEANS is the reader's job (`commands/quarterback.md`, Pass 4).

DECLARED SCOPE, enumerated, never inferred:
  reads   : queue/orders/<stem>.md, and whatever the gate itself reads
  writes  : NOTHING of its own. The gate is someone else's command and may do
            whatever it does.
  decides : NOTHING. It reports an exit code.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):
    """Import a sibling tool whose filename has a hyphen."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


gatelib = _load("gatelib")

REPO = HERE.parent
ORDERS = REPO / "queue" / "orders"
LANES = REPO.parent / "worktrees"
REPO_NAME = os.environ.get("GATE_REPO_NAME", REPO.name)


def configured_repo_roots() -> dict[str, str]:
    """Read optional external checkout mappings from GATE_REPO_ROOTS JSON.

    Example: {"example-owner/example-project": "/workspace/example-project"}.
    The default mapping is empty; the current checkout needs no configuration.
    """
    raw = os.environ.get("GATE_REPO_ROOTS", "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return ({str(slug): str(path) for slug, path in value.items()
             if isinstance(slug, str) and isinstance(path, str)}
            if isinstance(value, dict) else {})


def frontmatter(text: str) -> dict:
    """Flat key: value frontmatter. These files only ever use that."""
    out = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    for line in text[3: end if end > 0 else len(text)].splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def find_order(stem: str):
    """The order file, whether it is live or already archived."""
    for cand in (ORDERS / f"{stem}.md", ORDERS / "done" / f"{stem}.md"):
        if cand.is_file():
            return cand
    matches = sorted(ORDERS.glob(f"{stem}*.md")) + \
        sorted((ORDERS / "done").glob(f"{stem}*.md"))
    return matches[0] if matches else None


def where(fm: dict, here: bool = False,
          repo_roots: dict[str, str] | None = None) -> Path | None:
    """The directory the gate must run in: the lane worktree.

    NEVER the caller's cwd, which is the whole bug this tool exists for.

    `here` is the ONE exception and it is declared, not inferred: in a driver
    pod the fresh clone IS the lane, there is no `../worktrees` tree beside it,
    and the lane path recorded in the order names a directory on somebody
    else's machine. The caller passes --here to say so out loud rather than
    this function guessing from the absence of a worktree, which would silently
    fall back to the repo on the PC too and reintroduce the original bug.
    """
    if here:
        return REPO
    lane = fm.get("lane", "").strip()
    if lane:
        name = Path(lane).name
        cand = LANES / name
        if cand.is_dir():
            return cand
    slug = fm.get("repo", "").strip()
    repo_name = slug.rstrip("/\\").split("/")[-1].split("\\")[-1]
    if not slug or repo_name == REPO_NAME:
        return REPO
    configured = repo_roots if repo_roots is not None else configured_repo_roots()
    target = configured.get(slug)
    if target:
        path = Path(target).expanduser()
        return path if path.is_dir() else None
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run-gate")
    ap.add_argument("stem", nargs="?", help="order stem, or its number")
    ap.add_argument("--here", action="store_true",
                    help="this checkout IS the lane (a driver pod); do not "
                         "look for a worktree beside it")
    ap.add_argument("--in", dest="in_dir",
                    help="run the gate in THIS directory, e.g. the scratch copy "
                         "an order's spec was generated from: an order is not "
                         "launched until its exact gate is green there (order "
                         "173 asked for 10 passes from ids selecting 9)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if not a.stem:
        ap.error("which order?")

    path = find_order(a.stem)
    if path is None:
        print(f"run-gate: no order matching {a.stem!r}", file=sys.stderr)
        return 1

    fm = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    gate = fm.get("gate", "").strip()
    if not gate:
        # A MISSING GATE IS NOT A PASS. Order 73 has no gate field and the queue
        # correctly refuses to launch it; saying "green" here would be the
        # silence-and-success fault.
        print(f"run-gate: {path.stem} declares NO gate, so nothing can decide "
              f"whether it is done.", file=sys.stderr)
        return 2

    cwd = Path(a.in_dir) if a.in_dir else where(fm, here=a.here)
    if a.in_dir and not cwd.is_dir():
        print(f"run-gate: --in {a.in_dir} is not a directory", file=sys.stderr)
        return 2
    if cwd is None:
        print(f"run-gate: {path.stem} names repo {fm.get('repo')!r}, which is "
              f"not the memory bank checkout. Pass --in with the correct "
              f"checkout rather than guessing.",
              file=sys.stderr)
        return 2
    # RESOLVE THE GATE FOR THIS MACHINE BEFORE RUNNING IT. 183 stored gates name
    # the system interpreter by absolute Windows path and many name their script
    # by an absolute worktree path, so as written they run on exactly one
    # machine: not the Mac, and not a driver pod
    # (`cluster/stored_gates_are_windows_paths`). The resolver rewrites only
    # what is a SPELLING of this repo and leaves another repo's venv alone, so a
    # gate it cannot honestly fix still fails loudly instead of running and
    # asserting nothing.
    runnable = gatelib.resolve(gate, repo=fm.get("repo"))
    print(f"order {path.stem}")
    print(f"  in   {cwd}")
    print(f"  run  {runnable}")
    if runnable != gate.strip():
        print(f"  (stored as {gate.strip()})")
    print("-" * 70)

    proc = subprocess.run(runnable, shell=True, cwd=str(cwd),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800)
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines()[-40:]:
        print(line)
    print("-" * 70)
    rc = proc.returncode
    if rc == 0 and all_skipped(out):
        # A GATE WHOSE TESTS ALL SKIPPED IS NOT GREEN, IT IS UNPROVEN.
        # pytest exits 0 when every test skips, so a gate can report success
        # having asserted nothing at all. Measured 2026-09-14 on order 120: its
        # gate ran, printed "1 skipped in 0.04s", exited 0, and the driver had
        # not done the work. The farm integration suite is the same shape at
        # scale: 318 tests, 318 skips, exit 0, read as green for weeks.
        # This is golden rule 3, silence and success must never look alike.
        print("EXIT 0 but EVERY TEST SKIPPED, so the gate proved NOTHING and is "
              "reported RED. A skip is not a pass: whatever the gate needed "
              "(a database, a fixture, an import) was not there, and the run "
              "asserted nothing. Fix the precondition or the gate.")
        print(f"EXIT {rc} -> treated as 1 (RED: all skipped)")
        return 1
    print(f"EXIT {rc}  ({'green' if rc == 0 else 'RED'})")
    return rc


SKIP_SUMMARY = re.compile(r"\b(\d+)\s+skipped\b")
PASS_SUMMARY = re.compile(r"\b(\d+)\s+(passed|failed|error|errors|xpassed)\b")


def all_skipped(out: str) -> bool:
    """True when a pytest run skipped everything and asserted nothing.

    NARROW ON PURPOSE. It fires only when the output says something WAS skipped
    and says nothing passed, failed or errored. A run with no pytest summary at
    all (a plain script gate, a linter) is NOT this, and must keep its own exit
    code: guessing about a tool whose output shape is unknown would be the same
    class of fault this catches.
    """
    if not SKIP_SUMMARY.search(out):
        return False
    for m in PASS_SUMMARY.finditer(out):
        if int(m.group(1)) > 0:
            return False
    return True


def self_test() -> int:
    import tempfile
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    want("frontmatter reads a gate",
         frontmatter("---\ngate: python x.py\nlane: ../worktrees/l\n---\nbody")
         .get("gate") == "python x.py")
    want("a file with no frontmatter yields nothing, rather than guessing",
         frontmatter("just a body") == {})
    want("a colon inside the value survives",
         frontmatter("---\ngate: C:\\py.exe -m pytest\n---")
         .get("gate") == "C:\\py.exe -m pytest")
    # WHERE IT RUNS is the whole point, so it is asserted directly.
    tmp = Path(tempfile.mkdtemp(prefix="run-gate-selftest-"))
    try:
        want("a lane that does not exist falls back to the repo, never to the "
             "caller's cwd", where({"lane": "../worktrees/nope"}) == REPO)
        farm = tmp / "external-project"
        farm.mkdir()
        repo_roots = {"example-owner/example-project": str(farm)}
        want("an order naming ANOTHER repo, with no lane on disk, runs in THAT "
             "configured checkout, never this repository",
             where({"lane": "../worktrees/nope",
                    "repo": "example-owner/example-project"},
                   repo_roots=repo_roots)
             == farm)
        want("a repo nobody registered is None, so the caller refuses instead "
             "of running somewhere plausible",
             where({"repo": "example-owner/not-registered"},
                   repo_roots=repo_roots) is None)
        want("--here ignores a lane that DOES exist, because in a pod the "
             "clone is the lane and the recorded path names another machine",
             where({"lane": "../worktrees/anything"}, here=True) == REPO)
        want("and no lane at all also means the repo", where({}) == REPO)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    # A SKIP IS NOT A PASS (2026-09-14). Order 120's gate printed
    # "1 skipped in 0.04s", exited 0, and the driver had not done the work.
    # These fixtures are REAL pytest summary lines, not invented ones.
    want("a run where everything skipped is NOT green",
         all_skipped("s                             [100%]\n1 skipped in 0.04s"))
    want("318 skipped and nothing else is NOT green, which is the farm "
         "integration suite exactly",
         all_skipped("318 skipped, 2 warnings in 41.02s"))
    want("but a run with real passes beside skips IS green, or every suite "
         "with one skipped test would go red",
         not all_skipped("1879 passed, 4 skipped in 63.11s"))
    want("and a FAILING run is left alone to report its own exit code",
         not all_skipped("2 failed, 3 skipped in 1.20s"))
    want("a gate with NO pytest summary at all is never guessed about: a "
         "linter or a plain script keeps its own exit code",
         not all_skipped("check-repo-proposal: 20 rows, all good"))
    want("zero skipped is not 'all skipped'",
         not all_skipped("0 skipped, 5 passed in 0.10s"))
    want("an order with no gate is not silently treated as passing",
         frontmatter("---\norder: 73\n---").get("gate") is None)

    print("self-test: %d check(s), %d failure(s)" % (checks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
