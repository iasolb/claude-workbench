#!/usr/bin/env python3
"""machinelib.py: one home for the three machine facts the tools need.

Three functions, nothing else, no side effects at import:

     machine_branch()     the branch THIS machine owns, read from the file
                         named by $WORKBENCH_CONF (default
                         ~/.claude/workbench.conf), "windows" when the file
                         or the key is missing.
    interpreter()        sys.executable, then $PYTHON_EXECUTABLE, then
                         ~/.local/bin/python when sys.executable is empty.
    memory_bank_root()   where the memory bank lives for THIS machine:
                          $MEMORY_BANK_ROOT when set, else the repository
                          containing this file, else the configured default,
                          announced
                          as a fallback rather than returned silently
                          (strict=True turns the fallback into an error).

The default root is ~/.local/share/memory-bank and can be changed with
$MEMORY_BANK_DEFAULT_ROOT. Set $PYTHON_EXECUTABLE to override the interpreter
fallback when sys.executable is empty.

The reason for one home: until 2026-09-01 each tool that needed these carried
its own copy, and the copies drifted on another machine as a
plausible, well-formed finding about something else: close-turn.py ran the
lint with a machine-specific interpreter hardcoded and refused to commit unless the branch
was literally "windows". Both were the same fault, tooling written for one
machine refusing on the other in the wrong direction
(rules/shared/memory-integrity.md rule 3).

The third fact was the most copied of all: measured 2026-09-02, 31 files under
tools/, hooks/, automation/, docs/ and settings.json hardcoded the path
"Documents/ai/memory-bank". Machines have different workspace layouts,
and that literal is why neither can be moved. memory_bank_root() is its one
home (order 25).
"""

import argparse
import os
import sys
import tempfile
import warnings
from pathlib import Path

DEFAULT_MEMORY_BANK_ROOT = Path(
    os.environ.get("MEMORY_BANK_DEFAULT_ROOT") or
    (Path.home() / ".local" / "share" / "memory-bank"))


def machine_branch() -> str:
    """The branch THIS machine owns, from ~/.claude/workbench.conf.

    Each machine lives permanently on its own branch (rules/claude/git-github.md):
    `windows` on one machine, `mac` on another. This was hardcoded to "windows"
    until 2026-09-01, so close-turn.py could not commit from another machine:
    it printed "REFUSED branch is not windows" and exited 0, which reads as a
    guard doing its job rather than a tool that cannot run here.
    """
    conf = Path(os.environ.get("WORKBENCH_CONF") or (Path.home() / ".claude" / "workbench.conf"))
    try:
        for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("MACHINE_BRANCH="):
                val = line.split("=", 1)[1].strip()
                if val:
                    return val
    except OSError:
        pass
    return "windows"


# The branch a machine owns and the name an ORDER calls that machine are
# different tokens, and nothing mapped between them until 2026-09-07.
BRANCH_TO_MACHINE = {"windows": "pc", "mac": "mac"}


def machine_name() -> str:
    """What an order's `machine:` field calls THIS host: `pc` or `mac`.

    ORDERS SAY `machine: pc` WHILE THE BRANCH IS `windows`, so the two
    vocabularies never lined up and no caller bridged them. The consequence was
    not a crash, it was silence: `tools/queue-state.py --machine` defaults to
    the empty string, its filter is guarded by `if o.machine and machine`, and
    an empty machine makes that guard permanently false. So the machine filter
    could be shipped, tested, and green while FILTERING NOTHING, which is the
    exact shape of `rules/shared/memory-integrity.md` rule 2: ask what the tool
    would print if the thing were broken, and here it printed the same thing.

    Measured 2026-09-07: `queue-state.py` reported `machine=unset` on one machine, so
    every `machine: pc` order was equally launchable on another machine, which is how
    three lanes for the wrong machine were provisioned and none launched, three times in
    one afternoon.

    Derived from machine_branch() rather than stored separately, so there is one
    fact with one home and the two can never disagree.
    """
    return BRANCH_TO_MACHINE.get(machine_branch(), machine_branch())


def interpreter() -> str:
    """sys.executable, then its configured fallback only when empty."""
    return (sys.executable or os.environ.get("PYTHON_EXECUTABLE") or
            str(Path.home() / ".local" / "bin" / "python"))


def _root_from(override, start, strict: bool = False) -> Path:
    """The decision behind memory_bank_root(), with the world passed in.

    PURE so all four branches are assertable, including the two that cannot be
    reached from a real checkout: there is always a `.git` above this file here,
    so the fallback and the strict refusal were unreachable code in every test
    that could be written against the wrapper. `start` is the path to search
    upward from; `override` is $MEMORY_BANK_ROOT's value or ''.
    """
    if override:
        return Path(override)
    start = Path(start).resolve()
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    if strict:
        raise RuntimeError(
            "memory bank root unknown: set $MEMORY_BANK_ROOT or run from a "
            "repository checkout")
    warnings.warn(
        "memory_bank_root(): no $MEMORY_BANK_ROOT and no repository above "
        "this script, falling back to the configured default %s; set $MEMORY_BANK_ROOT "
        "to configure another location" % DEFAULT_MEMORY_BANK_ROOT,
        RuntimeWarning, stacklevel=2)
    return DEFAULT_MEMORY_BANK_ROOT


def memory_bank_root(strict: bool = False) -> Path:
    """The memory bank's root for THIS machine, never a silent guess.

    Preference order: $MEMORY_BANK_ROOT when set, else the repository
    containing this file (the nearest ancestor that has a .git), else the
    configured default. The default is a FALLBACK, not a silent guess: it emits a
    RuntimeWarning so the caller sees it, and strict=True turns the fallback
    into a RuntimeError so a caller that must not guess cannot get one by
    accident.
    """
    return _root_from(os.environ.get("MEMORY_BANK_ROOT"), Path(__file__), strict)


# THE THREE SIBLINGS OF THE BANK, AND WHY THEY LIVE HERE (2026-09-07)
#
# `state/`, `worktrees/` and `state/landings.csv` sit BESIDE the memory bank on
# every machine, at a different absolute path on each. Ten tools had each
# hardcoded their own copy of one machine's spelling as a module-level constant, and
# a Windows literal is not an absolute path on other operating systems, it is a RELATIVE one.
# The measured consequence, same day: drive-lane.py silently created two
# directory trees with machine-specific absolute paths under the working
# directory and wrote every driver verdict into them, and one of those trees
# was staged for commit.
#
# So the resolution has ONE home, here, and the tools point at it. That is
# memory-integrity rule 3 applied to a path, and it is the sweep the personas
# log asked for: "a principle established in one tool and not swept through
# its siblings is half a fix".

def main_checkout(root: Path) -> Path:
    """The MAIN checkout a driver's lane belongs to, or `root` itself.

    A lane is a linked worktree: its `.git` is a FILE reading
    `gitdir: <main>/.git/worktrees/<lane>`. The bank's siblings sit beside the
    MAIN checkout, so inside a lane `root.parent / "state"` named
    `worktrees/state`, which does not exist, and every tool a driver's gate ran
    looked for its state there (2026-10-04: order 223's gate could never go
    green). A submodule's `.git` file points at `.git/modules/...` instead, and
    is its own checkout, so it is left alone.
    """
    marker = root / ".git"
    if not marker.is_file():
        return root
    text = marker.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        return root
    gitdir = Path(text.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (root / gitdir).resolve()
    parts = gitdir.parts
    for i in range(len(parts) - 1):
        if parts[i] == ".git" and parts[i + 1] == "worktrees":
            return Path(*parts[:i])
    return root


def state_dir() -> Path:
    """`state/`, beside the bank's main checkout. Verdicts, timings, logs."""
    return main_checkout(memory_bank_root()).parent / "state"


def worktrees_root() -> Path:
    """`worktrees/`, beside the bank's main checkout. One directory per lane."""
    return main_checkout(memory_bank_root()).parent / "worktrees"


def driver_logs_dir() -> Path:
    """`state/drivers/`, where a run's verdict and timing land."""
    return state_dir() / "drivers"


def landings_csv() -> Path:
    """`state/landings.csv`, the append-only record of landed lanes."""
    return state_dir() / "landings.csv"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="machinelib",
        description="the three machine facts the tools share")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--show", action="store_true",
                    help="print what this machine resolves to")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.show:
        print(f"branch      {machine_branch()}")
        print(f"machine     {machine_name()}")
        print(f"interpreter {interpreter()}")
        print(f"bank        {memory_bank_root()}")
        print(f"state       {state_dir()}")
        print(f"worktrees   {worktrees_root()}")
        return 0
    ap.error("nothing to do: --self-test or --show")
    return 2


def self_test() -> int:
    """Every branch of the three facts, with the world passed in.

    THIS FILE HAD NO TEST UNTIL 2026-09-16, which is the worst place in the
    repo for that gap: eleven tools import it, and each fact here exists
    BECAUSE a hardcoded copy of it was wrong on the other machine. An untested
    shared dependency is the same fault one layer down.
    """
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    real_conf = os.environ.get("WORKBENCH_CONF")
    real_root = os.environ.get("MEMORY_BANK_ROOT")
    try:
        with tempfile.TemporaryDirectory(prefix="machinelib-") as td:
            d = Path(td)

            # ---- machine_branch: read the file, never assume the machine.
            conf = d / "workbench.conf"
            os.environ["WORKBENCH_CONF"] = str(conf)
            want("a missing conf file falls back to windows, not a crash",
                 machine_branch() == "windows")
            conf.write_text("MACHINE_BRANCH=mac\n", encoding="utf-8")
            want("the conf file's branch is read",
                 machine_branch() == "mac")

            # THE HARDCODED "windows" IS THE BUG THIS REPLACED. close-turn.py
            # printed "REFUSED branch is not windows" and exited 0 on another machine,
            # which reads as a guard working rather than a tool that cannot run.
            conf.write_text("OTHER=1\nMACHINE_BRANCH=mac\nTRAILING=2\n",
                            encoding="utf-8")
            want("the key is found among other keys",
                 machine_branch() == "mac")
            conf.write_text("MACHINE_BRANCH=\n", encoding="utf-8")
            want("an EMPTY value falls back rather than returning ''",
                 machine_branch() == "windows")
            conf.write_text("MACHINE_BRANCH=  mac  \n", encoding="utf-8")
            want("surrounding whitespace is stripped",
                 machine_branch() == "mac")

            # ---- machine_name: the bridge between two vocabularies.
            # IT MUST NEVER RETURN EMPTY. An empty machine made queue-state's
            # filter `if o.machine and machine` permanently false, so the filter
            # shipped green while filtering NOTHING, and the Mac provisioned
            # three PC lanes three times in one afternoon.
            conf.write_text("MACHINE_BRANCH=windows\n", encoding="utf-8")
            want("the windows branch is the machine an order calls pc",
                 machine_name() == "pc")
            conf.write_text("MACHINE_BRANCH=mac\n", encoding="utf-8")
            want("the mac branch is the machine an order calls mac",
                 machine_name() == "mac")
            conf.write_text("MACHINE_BRANCH=laptop3\n", encoding="utf-8")
            want("an UNKNOWN branch passes through and is never empty",
                 machine_name() == "laptop3")
            conf.unlink()
            want("and no conf file at all still names a machine",
                 bool(machine_name()))

            # ---- memory_bank_root: the fallback is never silent.
            want("an explicit override wins outright",
                 _root_from("/somewhere/else", d) == Path("/somewhere/else"))
            repo = d / "repo"
            (repo / "tools").mkdir(parents=True)
            (repo / ".git").mkdir()
            want("the repository containing the file is found by walking up",
                 _root_from("", repo / "tools" / "x.py") == repo.resolve())

            # A `.git` FILE, NOT A DIRECTORY, IS THE SUBMODULE AND WORKTREE CASE
            # and it is the one that has broken hooks here before.
            sub = d / "sub"
            (sub / "tools").mkdir(parents=True)
            (sub / ".git").write_text("gitdir: ../real/.git", encoding="utf-8")
            want("a .git FILE counts too, so a submodule or worktree resolves",
                 _root_from("", sub / "tools" / "x.py") == sub.resolve())

            # ---- the siblings sit beside the MAIN checkout, even from a lane.
            # Red-proven 2026-10-04 by making main_checkout return root.
            main = d / "workspace" / "memory-bank"
            (main / ".git" / "worktrees" / "tools-x.py").mkdir(parents=True)
            lane = d / "workspace" / "worktrees" / "tools-x.py"
            lane.mkdir(parents=True)
            (lane / ".git").write_text(
                "gitdir: %s\n" % (main / ".git" / "worktrees" / "tools-x.py").as_posix(),
                encoding="utf-8")
            want("a driver's lane resolves to the main checkout it belongs to",
                 main_checkout(lane) == main)
            want("the main checkout resolves to itself",
                 main_checkout(main) == main)
            want("a SUBMODULE (.git/modules) is its own checkout, never redirected",
                 main_checkout(sub) == sub)

            # THE TWO BRANCHES A REAL CHECKOUT CAN NEVER REACH, which is why
            # the decision was pulled out as a pure function at all.
            bare = d / "bare" / "deep"
            bare.mkdir(parents=True)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                got = _root_from("", bare / "x.py")
            want("with no repo and no override it falls back to the configured path",
                 got == DEFAULT_MEMORY_BANK_ROOT)
            want("and the fallback WARNS, so it is never a silent guess",
                 any(issubclass(w.category, RuntimeWarning) for w in caught))
            try:
                _root_from("", bare / "x.py", strict=True)
                raised = False
            except RuntimeError:
                raised = True
            want("strict=True REFUSES instead of guessing", raised)

            # ---- the three siblings sit beside the bank, wherever it is.
            # BESIDE the bank, not inside it: `state/` and `worktrees/` are
            # siblings of the checkout on every machine. Writing these the
            # obvious way round is how this test first went red.
            os.environ["MEMORY_BANK_ROOT"] = str(repo)
            beside = repo.parent
            want("state/ sits BESIDE the bank, never inside it",
                 state_dir() == beside / "state")
            want("worktrees/ sits beside the bank too",
                 worktrees_root() == beside / "worktrees")
            want("driver logs sit under state/",
                 driver_logs_dir() == beside / "state" / "drivers")
            want("landings.csv sits under state/",
                 landings_csv() == beside / "state" / "landings.csv")
            want("and all four MOVE when the bank moves, which is the point",
                 str(state_dir()).startswith(str(beside)))

            # ---- interpreter: this python, not a hardcoded one.
            want("the interpreter is the one actually running",
                 interpreter() == sys.executable)
            want("and it is an absolute path, so no bare-python call is made",
                 Path(interpreter()).is_absolute())
    finally:
        for key, val in (("WORKBENCH_CONF", real_conf),
                         ("MEMORY_BANK_ROOT", real_root)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    if fails:
        print(f"\nmachinelib self-test: {len(fails)} of {checks} FAILED")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"\nmachinelib self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
