#!/usr/bin/env python3
"""litter-check.py: find files that were meant to be temporary and outlived it.

    python3 tools/litter-check.py              report litter, exit 1 if any
    python3 tools/litter-check.py --quiet      one summary line, for a hook
    python3 tools/litter-check.py --self-test

WHY THIS EXISTS (2026-09-01): "at the start of the session, qb will run a
datetime.now() check, and then based on the result locate files that are stale.
We do this by tagging all files we intend to be temporary with a datetime. If any
are identified, a litter cleanup job is folded into cleanup crew."

The measurement that prompted it, the same day: EIGHT reentry and handoff briefs
had accumulated in `reports/`, 1,395 lines, of which one was current. Worse than
the volume, the queue pointed at the 2026-08-21 brief while four newer ones
existed, so a session following the pointer read a ten-day-old picture and
believed it. **Litter is not just clutter here, it is a source of confidently
wrong context.**

TWO WAYS A FILE DECLARES ITSELF TEMPORARY:

1. `expires: YYYY-MM-DD` in YAML frontmatter, or an `<!-- expires: YYYY-MM-DD -->`
   comment anywhere in the first 40 lines. Stale once today is past it. This is
   the general mechanism and any file may use it.
2. CONSUMED-ON-READ classes, listed in CONSUMED_ON_READ below. A reentry brief is
   written for the NEXT session and is spent the moment that session reads it, so
   its expiry is not a date at all: every one but the newest is litter by
   definition. Encoding that as a date would just be a date nobody updates.

It REPORTS and never deletes. Deleting is the cleanup-crew pass's job, and it
folds anything still load-bearing into a permanent home first. A tool that
deleted on its own would eventually delete the one brief someone still needed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Override these defaults when the worktree or driver-state directories live
# elsewhere. Defaults are relative to the repository, not a particular machine.
DRIVER_LOGS = Path(os.environ.get(
    "LITTER_DRIVER_LOGS", REPO.parent / "state" / "drivers")).expanduser()
WORKTREES = Path(os.environ.get(
    "LITTER_WORKTREES", REPO.parent / "worktrees")).expanduser()

# Classes spent on being read: newest is live, every older one is litter.
# (glob, human name)
CONSUMED_ON_READ = [
    ("reports/*qb-reentry*.md", "quarterback reentry brief"),
]

# EVERY PYTHON FILE IN THIS REPO LIVES IN `tools/` OR `hooks/`, so one at the
# ROOT is misplaced by definition and needs no expiry date to prove it.
#
# MEASURED 2026-09-16: `dbg_parse.py`, `gen_fixture.py`, `show_001107.py` and
# `verify_fixture.py` had sat at the root for sixteen days, committed by "order
# 52: driver deliverable". One of them hardcoded an absolute path to a driver
# log from the night it was written. Nothing flagged them: this tool looked only
# for `expires:` tags and consumed-on-read classes, so scratch that never
# declared itself temporary was invisible forever.
#
# It is a SECTIONING rule, which was stated on 2026-09-16: "the thing
# at the end of the pointer must also be stored in the most efficient means
# possible such that we can section ourselves off well." A file with no section
# has no owner, and nobody deletes what nobody owns.
ROOT_SCRIPT_GLOB = "*.py"

# FILES IN THE DRIVER-STATE DIRECTORY THAT ARE NOT PER-LANE STATE (2026-09-11).
# The dead-lane check derives a lane name from every filename in
# `state/drivers/`, but `lanelib_liveness.py` writes the SWEEP's own report
# into that same directory. So `sweep-report.json` read as a dead lane called
# "sweep-report", which no worktree can ever match, and it was reported as
# litter at every single session start. The standing rule is to sweep at every
# lane check, so the sweep recreated it immediately: a permanent false positive
# in the instrument whose whole job is telling a session what to trust.
# `--prune-dead-state` would also have deleted it, and the warning text
# misattributes it as a verdict that trips the launcher, which it is not.
#
# The name is MIRRORED rather than imported so this tool keeps no dependency on
# the lane library; lanelib_liveness.py's self-test asserts the two agree, so
# drift goes red instead of silently reopening this.
NOT_LANE_STATE = frozenset({"sweep-report.json"})

# DRIVER-STATE SUFFIXES, stripped WHOLE so a dotted lane name survives.
STATE_SUFFIXES = (".verdict", ".timing", ".pid", ".claim", ".json")


def state_lane_name(name: str, live: frozenset[str] | set[str]) -> str:
    """The lane a driver-state filename belongs to, dots preserved.

    NEVER SPLIT ON THE FIRST DOT (2026-09-12). A lane is named for the files its
    order writes, so an order writing `tools/why-failed.py` gets the lane
    `tools-why-failed.py`, whose state is `tools-why-failed.py.verdict`,
    `tools-why-failed.py.timing` and `tools-why-failed.py-<stamp>.log`. The old
    `name.split(".")[0]` read those as the lanes `tools-why-failed` and
    `tools-why`, neither of which is a worktree, so this tool reported a LIVE
    lane as dead and `--prune-dead-state` DELETED its verdict.

    Measured the day it happened: a quarterback ran the documented session-start
    command and destroyed order 77's verdict while
    `worktrees/tools-why-failed.py` was sitting in `git worktree list`. Three of the nine lanes in the queue that morning had a
    dotted name, so this was not an edge case.

    `live` is consulted before the log stamp is stripped, so a lane whose own
    name ends in `-<something>` is not truncated into a phantom.
    """
    if name.endswith(".log"):
        base = name[: -len(".log")]
        if base in live:
            return base
        return base.rsplit("-", 1)[0] if "-" in base else base
    for suffix in STATE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name

EXPIRES_RE = re.compile(r"^\s*(?:#\s*|<!--\s*)?expires:\s*(\d{4}-\d{2}-\d{2})", re.M)
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}


def today() -> dt.date:
    """The datetime.now() check the owner asked for, in one place so the self-test
    can pin it."""
    return dt.date.today()


def read_head(p: Path, lines: int = 40) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return "".join(next(fh, "") for _ in range(lines))
    except OSError:
        return ""


def find_expired(root: Path, now: dt.date) -> list[tuple[Path, dt.date, int]]:
    out = []
    for p in root.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        m = EXPIRES_RE.search(read_head(p))
        if not m:
            continue
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if now > d:
            out.append((p, d, (now - d).days))
    return sorted(out, key=lambda t: t[2], reverse=True)


def live_lane_names(worktrees: Path) -> set[str]:
    """The lanes that still have a worktree. One home for the liveness test,
    which three functions below had each spelled out for themselves."""
    return {p.name for p in worktrees.iterdir()} if worktrees.is_dir() else set()


def find_dead_driver_state(logs: Path, worktrees: Path) -> list[tuple[str, int]]:
    """Driver state whose lane no longer exists, newest-first by count.

    THE FULL PICTURE, LOGS INCLUDED. What the REPORT counts is
    `actionable_dead_state` below, because the prune keeps logs on purpose.

    THE MEASUREMENT THAT ADDED THIS, 2026-09-07: this tool printed "clean,
    nothing stale" at session start while `state/drivers/` held 161 verdict
    files against 24 worktrees, the oldest from 22 August. That is not a
    tidiness miss, it is the fault this tool exists to catch, in the class that
    costs the most: `tools/go.py` ADOPTS any verdict with no live runner as "a
    lane that finished and needs landing", so it spent a run trying to land
    fifteen lanes deleted weeks earlier, failed every one, tripped its
    consecutive-failure limit, and killed the run before it could land the work
    that was actually in flight.

    `docs/model-routing.md` had already written the rule down: "A stale verdict
    file outlives its lane, so clear it when the lane is landed." Nothing
    enforced it, and the instrument that should have noticed was looking only
    at `*.md` inside the repo.

    A lane is dead when `worktrees/<lane>/` is gone. Its WORK is not in these
    files, it is in the lane's branch, so this reports state only and the
    branches are a separate decision (2026-09-07: tell him what a lane
    holds before anything is deleted).
    """
    if not logs.is_dir():
        return []
    live = live_lane_names(worktrees)
    dead: dict[str, int] = {}
    for p in logs.iterdir():
        if not p.is_file():
            continue
        if p.name in NOT_LANE_STATE:
            continue
        # `<lane>.verdict`, `<lane>.timing`, `<lane>.pid`, `<lane>-<stamp>.log`
        stem = state_lane_name(p.name, live)
        if stem and stem not in live:
            dead[stem] = dead.get(stem, 0) + 1
    return sorted(dead.items(), key=lambda kv: kv[1], reverse=True)


def dead_driver_state_paths(logs: Path, worktrees: Path) -> list[Path]:
    """The actual files behind find_dead_driver_state, for --prune-dead-state.

    Same liveness test as that function, deliberately: one rule, computed once.

    THE `.log` IS EVIDENCE AND IS NEVER PRUNED (2026-09-12). The docstring below
    says deleting is safe because "a lane's WORK lives in its branch, never in
    these files", which is true of the work and false of the RECORD. The log is
    the only place a run's death reason is written down, and `why-failed.py`
    exists solely to read it: "READS THE LOG THE LAUNCHER WROTE, which is a state
    file, not another tool's stdout."

    Measured the hour this changed: a quarterback ran `--prune-dead-state`, then
    ran `why-failed.py --since 2026-09-12` to diagnose four dead runs and got
    "No failed lane since 2026-09-12". The logs it needed had just been deleted
    by the sanctioned cleanup tool, and the answer it gave instead was
    indistinguishable from a healthy day. The diagnosis survived only because
    `automation/driver-errors.csv` is tracked and had the same rows.

    What actually makes an orphan dangerous is the VERDICT: `go.py` adopts any
    verdict with no live runner and tries to land it. A log adopts nothing.
    """
    if not logs.is_dir():
        return []
    live = live_lane_names(worktrees)
    out: list[Path] = []
    for p in logs.iterdir():
        if not p.is_file():
            continue
        if p.name in NOT_LANE_STATE:
            continue
        if p.name.endswith(".log"):
            continue
        stem = state_lane_name(p.name, live)
        if stem and stem not in live:
            out.append(p)
    return sorted(out)


def actionable_dead_state(logs: Path, worktrees: Path) -> list[tuple[str, int]]:
    """Dead-lane state the prune will actually remove, grouped by lane.

    THE REPORT MUST COUNT WHAT THE REMEDY REMOVES, or the finding can never be
    cleared. Measured 2026-09-16: `--prune-dead-state` deleted 2 files and the
    very next run of this tool reported 8 files across 8 lanes and exited 1,
    because the report counted the LOGS the prune deliberately keeps. Nothing a
    session is allowed to do could clear it, so the session-start hook printed
    an unclearable finding every session, which is an instrument that has
    stopped being one.

    It also made the warning text LIE. Every remaining file was a log, and the
    report said `go.py` adopts them as verdicts and will trip its failure limit.
    A log adopts nothing (golden rule 2: a logger can never lie).

    SECOND INSTANCE of exactly this shape in exactly this function: the
    `sweep-report.json` false positive above was the first, and it was fixed by
    excluding one filename. So this one gets the general mechanism instead
    (golden rule A3): the counted class is DEFINED as the prune's own output, so
    the two can no longer disagree.
    """
    live = live_lane_names(worktrees)
    counts: dict[str, int] = {}
    for p in dead_driver_state_paths(logs, worktrees):
        stem = state_lane_name(p.name, live)
        if stem:
            counts[stem] = counts.get(stem, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def retained_dead_logs(logs: Path, worktrees: Path) -> list[Path]:
    """A dead lane's LOG, kept on purpose and never counted as litter.

    It is the only record of why that run died, and `why-failed.py` exists to
    read it (see `dead_driver_state_paths`). Reported as a count so the disk
    growth stays visible, never as a finding, because there is no action.
    """
    if not logs.is_dir():
        return []
    live = live_lane_names(worktrees)
    out: list[Path] = []
    for p in logs.iterdir():
        if not p.is_file() or p.name in NOT_LANE_STATE:
            continue
        if not p.name.endswith(".log"):
            continue
        stem = state_lane_name(p.name, live)
        if stem and stem not in live:
            out.append(p)
    return sorted(out)


def prune_dead_driver_state(logs: Path, worktrees: Path) -> tuple[int, list[str]]:
    """Delete driver state for lanes that no longer exist. (count, problems).

    WHY DELETING IS SAFE HERE, and it is the narrow case: a lane's WORK lives in
    its branch, never in these files. What is left behind is a verdict, a pid, a
    timing and a log for a worktree that is gone. `tools/go.py` ADOPTS any
    verdict with no live runner and tries to land it, so each orphan is a
    guaranteed landing failure that counts toward `--failure-trip`. They are not
    clutter, they are a queue of scheduled failures.

    ADDED 2026-09-10, after 810 files across 219 dead lanes were found at
    session start. litter-check had reported them correctly since 2026-09-07 and
    nothing could act on it, so the count grew from 161 to 810 in three days:
    a detector with no remedy is a detector nobody can obey (golden rule A2, and
    A3, since the alternative was a session hand-deleting them and hiding the
    gap).
    """
    problems: list[str] = []
    removed = 0
    for p in dead_driver_state_paths(logs, worktrees):
        try:
            p.unlink()
            removed += 1
        except OSError as exc:
            problems.append(f"{p.name}: {exc}")
    return removed, problems


def find_root_scripts(root: Path) -> list[Path]:
    """Python files sitting at the repo root, which is no file's section."""
    return sorted(p for p in root.glob(ROOT_SCRIPT_GLOB) if p.is_file())


def find_superseded(root: Path) -> list[tuple[str, Path, list[Path]]]:
    """Newest by filename date wins. Filenames here are YYYY-MM-DD prefixed, so
    a lexical sort IS a chronological one; mtime would lie after any checkout."""
    out = []
    for pattern, label in CONSUMED_ON_READ:
        hits = sorted(root.glob(pattern))
        if len(hits) > 1:
            out.append((label, hits[-1], hits[:-1]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quiet", action="store_true", help="one summary line")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--prune-dead-state", action="store_true",
                    help="delete driver state whose lane no longer exists "
                         "(safe: the work is in the branch, not these files)")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    if a.prune_dead_state:
        try:
            removed, problems = prune_dead_driver_state(
                DRIVER_LOGS, WORKTREES)
        except Exception as exc:
            print(f"litter-check: could NOT prune driver state ({exc}). "
                  f"Nothing was deleted.", file=sys.stderr)
            return 2
        print(f"removed {removed} driver-state file(s) for lanes that no "
              f"longer exist")
        for p in problems:
            print(f"  COULD NOT REMOVE {p}", file=sys.stderr)
        return 1 if problems else 0

    root = Path(a.root).resolve()
    now = today()
    expired = find_expired(root, now)
    superseded = find_superseded(root)
    root_scripts = find_root_scripts(root)

    # Driver state lives beside the repo by default. The environment variables
    # above allow installations with a different layout to configure both roots.
    dead_state: list[tuple[str, int]] = []
    kept_logs: list[Path] = []
    try:
        dead_state = actionable_dead_state(DRIVER_LOGS, WORKTREES)
        kept_logs = retained_dead_logs(DRIVER_LOGS, WORKTREES)
    except Exception as exc:  # never let a missing sibling dir hide real litter
        print(f"litter-check: could NOT check driver state ({exc}). "
              f"That is unchecked, not clean.", file=sys.stderr)

    dead_files = sum(n for _, n in dead_state)
    total = (len(expired) + sum(len(old) for _, _, old in superseded)
             + dead_files + len(root_scripts))

    if a.quiet:
        if total:
            print(f"[litter] {total} stale file(s) as of {now}. "
                  f"Run tools/litter-check.py and fold a cleanup into the cleanup-crew pass.")
        return 1 if total else 0

    print(f"litter-check, as of {now}")
    if expired:
        print("\nPAST ITS DECLARED EXPIRY:")
        for p, d, age in expired:
            print(f"  {p.relative_to(root)}  expired {d} ({age} day(s) ago)")
    if root_scripts:
        print("\nPYTHON AT THE REPO ROOT, which belongs to no section:")
        for p in root_scripts:
            print(f"  {p.relative_to(root)}  (every .py here lives in tools/ "
                  f"or hooks/)")
    if superseded:
        for label, newest, old in superseded:
            print(f"\nSUPERSEDED {label}(s), spent on being read:")
            print(f"  KEEP  {newest.relative_to(root)}  (newest)")
            for p in old:
                print(f"  LITTER {p.relative_to(root)}")
    if dead_state:
        print(f"\nDRIVER STATE FOR LANES THAT NO LONGER EXIST "
              f"({dead_files} file(s), {len(dead_state)} lane(s)):")
        print("  tools/go.py ADOPTS a verdict with no live runner and tries to "
              "land it, so each of these is a guaranteed landing failure and "
              "they trip its consecutive-failure limit.")
        for lane, n in dead_state:
            print(f"  {lane}  ({n} file(s))")
        print("  CLEAR THEM: python3 tools/litter-check.py --prune-dead-state")
    if kept_logs:
        print(f"\n{len(kept_logs)} dead lane(s) keep only their LOG. That is "
              f"deliberate, not litter: it is the only record of why that run "
              f"died and why-failed.py reads it. No action.")
    if not total:
        print("  clean, nothing stale")
        return 0
    print(f"\n{total} stale file(s). This tool never deletes: fold anything still "
          f"load-bearing into its permanent home, then delete in the same commit.")
    return 1


def self_test() -> int:
    import tempfile
    fails: list[str] = []
    checks = 0

    def check(ok: bool, why: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(why)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "reports").mkdir()
        now = dt.date(2026, 9, 1)

        # An expiry in the past is litter; today or later is not.
        (root / "old.md").write_text("---\nexpires: 2026-08-01\n---\nbody\n")
        (root / "future.md").write_text("---\nexpires: 2027-01-01\n---\nbody\n")
        (root / "boundary.md").write_text("---\nexpires: 2026-09-01\n---\nbody\n")
        (root / "comment.md").write_text("# t\n\n<!-- expires: 2026-08-20 -->\n")
        (root / "untagged.md").write_text("no tag here\n")
        (root / "malformed.md").write_text("---\nexpires: not-a-date\n---\n")

        exp = {p.name for p, _, _ in find_expired(root, now)}
        check(exp == {"old.md", "comment.md"},
              f"expired set was {exp}, wanted old.md + comment.md")
        check("boundary.md" not in exp,
              "a file expiring TODAY was called stale; it is not")
        check("malformed.md" not in exp,
              "a malformed date was treated as an expiry")

        # Newest reentry survives, older ones are litter.
        for d in ("2026-08-21", "2026-08-30", "2026-09-01"):
            (root / "reports" / f"{d}-qb-reentry.md").write_text("brief\n")
        sup = find_superseded(root)
        check(bool(sup), "superseded briefs not detected at all")
        if sup:
            _, newest, old = sup[0]
            check(newest.name == "2026-09-01-qb-reentry.md",
                  f"kept {newest.name}, wanted the 09-01 one")
            check({p.name for p in old}
                  == {"2026-08-21-qb-reentry.md", "2026-08-30-qb-reentry.md"},
                  f"litter set wrong: {[p.name for p in old]}")

        # PYTHON AT THE ROOT. Red-proven by returning [] from
        # find_root_scripts: the four scratch files order 52 left on 2026-08-31
        # go back to being invisible, which is the state they sat in for
        # sixteen days.
        (root / "dbg_parse.py").write_text("import pathlib\n", encoding="utf-8")
        (root / "README.md").write_text("not a script\n", encoding="utf-8")
        (root / "tools").mkdir(exist_ok=True)
        (root / "tools" / "real.py").write_text("ok\n", encoding="utf-8")
        found_root = {p.name for p in find_root_scripts(root)}
        check(found_root == {"dbg_parse.py"},
              f"root .py detection was {sorted(found_root)}, wanted "
              f"just dbg_parse.py: scratch at the root needs no "
              f"expiry tag to be litter")
        check("real.py" not in found_root,
              "a tool in its proper section was flagged as root litter")

        # A single brief is never litter.
        with tempfile.TemporaryDirectory() as t2:
            r2 = Path(t2); (r2 / "reports").mkdir()
            (r2 / "reports" / "2026-09-01-qb-reentry.md").write_text("only one\n")
            check(not find_superseded(r2), "a lone reentry brief was called litter")

        # Driver state: a lane with a worktree is live, one without is dead.
        # Anchored on the REAL filename shapes drive-lane.py writes, which is
        # the half a hand-written fixture gets wrong: the log carries a
        # timestamp suffix and the others do not.
        logs = root / "drivers"; logs.mkdir()
        trees = root / "worktrees"; trees.mkdir()
        (trees / "live-lane").mkdir()
        for name in ("live-lane.verdict", "live-lane.timing",
                     "live-lane-20260907T155416.log"):
            (logs / name).write_text("x\n")
        # A DOTTED LANE NAME, which is the shape that made this tool delete a
        # live lane's verdict on 2026-09-12. These are the exact filenames it
        # destroyed for order 77, pasted from `state/drivers/` that morning.
        (trees / "tools-why-failed.py").mkdir()
        for name in ("tools-why-failed.py.verdict", "tools-why-failed.py.timing",
                     "tools-why-failed.py-20260911T195347.log"):
            (logs / name).write_text("x\n")
        for name in ("gone-lane.verdict", "gone-lane.timing", "gone-lane.pid",
                     "gone-lane-20260823T194431.log",
                     "other-gone.verdict"):
            (logs / name).write_text("x\n")
        # A DEAD LANE WHOSE ONLY LEFTOVER IS ITS LOG. This is the state every
        # dead lane ends in once the prune has run, and it is the state that
        # made this tool permanently red on 2026-09-16.
        (logs / "log-only-gone-20260824T101010.log").write_text("x\n")

        dead = dict(find_dead_driver_state(logs, trees))
        check("live-lane" not in dead, "a lane WITH a worktree was called dead")
        check("tools-why-failed.py" not in dead,
              "a LIVE lane with a dot in its name was called dead")
        check("tools-why-failed" not in dead and "tools-why" not in dead,
              "a dotted lane name was split on its first dot, "
              "inventing a phantom lane no worktree can ever match")
        check(not any("why-failed" in p.name
                      for p in dead_driver_state_paths(logs, trees)),
              "--prune-dead-state would delete a LIVE lane's verdict")
        check(set(dead) == {"gone-lane", "other-gone", "log-only-gone"},
              f"dead lanes were {sorted(dead)}, wanted gone-lane + "
              f"other-gone + log-only-gone")
        check(dead.get("gone-lane") == 4,
              f"gone-lane counted {dead.get('gone-lane')} files, wanted 4 "
              f"(a timestamped .log must fold back to its lane)")

        # THE FINDING MUST BE CLEARABLE BY THE REMEDY THAT EXISTS. Red-proven by
        # pointing `actionable_dead_state` back at `find_dead_driver_state`: the
        # log-only lane reappears as a finding and the count invariant breaks,
        # which is the 2026-09-16 state where the prune removed 2 files and the
        # next run still reported 8 across 8 lanes and exited 1.
        actionable = dict(actionable_dead_state(logs, trees))
        check("log-only-gone" not in actionable,
              "a dead lane whose only leftover is its LOG was "
              "reported as actionable litter, and no sanctioned "
              "action can ever clear it")
        check(sum(actionable.values()) == len(dead_driver_state_paths(logs, trees)),
              f"the report counts {sum(actionable.values())} file(s) "
              f"and the prune removes "
              f"{len(dead_driver_state_paths(logs, trees))}: a count "
              f"the remedy cannot reach goes red forever")
        check(actionable.get("gone-lane") == 3,
              f"gone-lane's actionable count was "
              f"{actionable.get('gone-lane')}, wanted 3: verdict, "
              f"timing and pid, never the log")
        # AND THE LOG IS STILL SURFACED, so this did not fix the alarm by
        # blinding the tool.
        kept = [p.name for p in retained_dead_logs(logs, trees)]
        check("log-only-gone-20260824T101010.log" in kept,
              f"a retained dead-lane log went unreported entirely: {kept}")
        check(not any("live-lane" in n for n in kept),
              f"a LIVE lane's log was counted as a dead one: {kept}")

        # THE PRUNE MUST NEVER DELETE A LOG. Deleting it destroys the only
        # record of why a run died, which is exactly what blinded
        # `why-failed.py` on 2026-09-12.
        pruned = [p.name for p in dead_driver_state_paths(logs, trees)]
        check(not any(n.endswith(".log") for n in pruned),
              f"--prune-dead-state would delete a driver LOG, which is "
              f"the only record of why that run died: {pruned}")
        check("gone-lane.verdict" in pruned,
              f"--prune-dead-state must still delete a dead VERDICT, "
              f"which is the file go.py adopts and tries to land: {pruned}")

        # A missing state directory is UNCHECKED, and must not read as clean.
        check(find_dead_driver_state(root / "nope", trees) == [],
              "a missing driver-state dir invented findings")

        # THE SWEEP'S OWN REPORT IS NOT A LANE. It lives in this directory and
        # is rewritten by every sweep, so treating it as lane state is a
        # finding that can never be cleared. Red-proven by deleting the
        # NOT_LANE_STATE guard: "sweep-report" appears as a dead lane.
        (logs / "sweep-report.json").write_text('{"found": 0}\n')
        dead_after = dict(find_dead_driver_state(logs, trees))
        check("sweep-report" not in dead_after,
              "the sweep's own report was called a dead lane, "
              "which is a finding no cleanup can ever clear")
        check(not any(p.name == "sweep-report.json"
                      for p in dead_driver_state_paths(logs, trees)),
              "--prune-dead-state would delete the sweep report")
        # That the mirrored name matches the writer's constant is asserted by
        # lanelib_liveness.py's own self-test, beside the constant, so this file
        # runs with nothing beside it (2026-10-21, for the public ai-kit).

    if fails:
        for f in fails:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print(f"litter-check self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
