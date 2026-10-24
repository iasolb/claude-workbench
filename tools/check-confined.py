#!/usr/bin/env python3
"""check-confined.py: did a lane's diff stay inside the files its order declared?

    python tools/check-confined.py <lane>        # one lane, exit 0 if confined
    python tools/check-confined.py --all         # every lane with a worktree
    python tools/check-confined.py --self-test

WHY THIS EXISTS (2026-09-02). Landing a lane requires two things: the gate was
green, and the diff stayed inside the files the order named. `land-lane.py` does
neither of the second, so the confinement check has always been a person reading
`git diff --name-only` and comparing it to the order by eye.

That is the entire reason landing needs a session at all, and it is why six
orders sat finished and unlanded while the expensive tier was busy. The check is
mechanical: a set of changed paths against a declared set. It never needed
judgement, it needed a script.

EXIT CODES CARRY THEIR REASON, one state each, because a number that means ten
things is how a caller invents a reason and writes it down as a measurement:

    0  confined      every changed path is inside the declared write set
    2  escaped       at least one changed path is outside it (the paths print)
    3  undeclared    the order declares no write set, so nothing can be checked
    4  unreadable    the lane, its worktree, or its order could not be read

3 and 4 are NOT failures of the diff. They are failures to ASK the question, and
a caller must be able to tell them from a real escape. Silence and success must
never look alike, and neither must "clean" and "could not look".
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

CONFINED, ESCAPED, UNDECLARED, UNREADABLE = 0, 2, 3, 4

REASON = {
    CONFINED: "confined",
    ESCAPED: "escaped",
    UNDECLARED: "undeclared",
    UNREADABLE: "unreadable",
}


def parse_writes(raw: str) -> frozenset[str]:
    """Comma, semicolon or whitespace separated paths, same shape the queue uses.

    Deliberately a copy of the queue's own rule rather than an import, because
    this tool must keep working when run from a lane worktree that has had the
    queue tooling scoped away. The format is four characters of syntax and has
    not changed; the risk of drift is smaller than the risk of a hard dependency
    on a file the scope pass deletes.
    """
    import re
    return frozenset(t for t in re.split(r"[,;\s]+", raw or "") if t.strip())


def declared_writes(order_text: str) -> frozenset[str]:
    for line in order_text.splitlines():
        if line.startswith("writes:"):
            return parse_writes(line.split(":", 1)[1])
        if line.strip() == "---" and order_text.index(line) > 0:
            break
    return frozenset()


def is_inside(path: str, declared: frozenset[str]) -> bool:
    """Is one changed path covered by the declared set?

    A trailing '/' declares a directory and covers everything beneath it. A '*'
    is a glob. Anything else must match exactly: a PREFIX match would let
    `tools/go.py` authorise `tools/go.py.bak`, and near-miss filenames are
    exactly what an escaped diff looks like.
    """
    for d in declared:
        if d.endswith("/"):
            if path.startswith(d):
                return True
        elif "*" in d:
            if fnmatch.fnmatch(path, d):
                return True
        elif path == d:
            return True
    return False


def changed_paths(worktree: Path, base: str) -> list[str] | None:
    r = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.splitlines() if p.strip()]


def check(worktree: Path, order_text: str, base: str = "origin/main",
          declared: frozenset[str] | None = None):
    """Return (code, changed, offenders). Never raises on a bad lane.

    `declared` lets a caller that ALREADY HAS the write set pass it straight
    in, instead of round-tripping it back through `writes:` prose so this
    function can re-parse it. The landing step reads an archived order's write
    set out of the store as a list of paths (2026-09-17), and synthesising a
    fake frontmatter line to hand it over would have been a second spelling of
    the same fact (`rules/shared/memory-integrity.md` rule 3).

    An EMPTY set passed explicitly still means UNDECLARED, exactly as an order
    with no `writes:` line does: the two must not diverge.
    """
    if declared is None:
        declared = declared_writes(order_text)
    if not declared:
        return UNDECLARED, [], []
    changed = changed_paths(worktree, base)
    if changed is None:
        return UNREADABLE, [], []
    offenders = [p for p in changed if not is_inside(p, declared)]
    return (ESCAPED if offenders else CONFINED), changed, offenders


def _self_test() -> int:
    fails: list[str] = []

    def want(label: str, ok: bool) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            fails.append(label)

    d = frozenset({"tools/go.py", "tools/lane-scope.py"})
    want("a declared file is inside", is_inside("tools/go.py", d))
    want("an undeclared file is outside", not is_inside("tools/pc.py", d))
    # THE NEAR MISS. A prefix match would call this confined, and a backup file
    # beside a declared one is what a wandering driver actually leaves behind.
    want("a name that merely STARTS WITH a declared file is outside",
         not is_inside("tools/go.py.bak", d))
    want("a directory declaration covers what is beneath it",
         is_inside("automation/workflows/beat.json", frozenset({"automation/workflows/"})))
    want("and does not cover a sibling directory",
         not is_inside("automation/other/x.json", frozenset({"automation/workflows/"})))
    want("a glob matches", is_inside("docs/a.md", frozenset({"docs/*.md"})))

    fm = "---\nstatus: pending\nwrites: tools/go.py, tools/lane-scope.py\n---\nbody\n"
    want("the declared set is read out of the order's frontmatter",
         declared_writes(fm) == d)
    want("an order with no write set declares nothing, rather than everything",
         declared_writes("---\nstatus: pending\n---\n") == frozenset())

    # THE FOUR STATES ARE DISTINCT. This is the whole point of the file: a
    # caller must be able to tell 'clean' from 'could not look'.
    want("the four exit codes are four different numbers",
         len({CONFINED, ESCAPED, UNDECLARED, UNREADABLE}) == 4)
    want("an order declaring nothing reports undeclared, never confined",
         check(Path("/nonexistent"), "---\nstatus: pending\n---\n")[0] == UNDECLARED)
    want("an unreadable worktree reports unreadable, never confined",
         check(Path("/nonexistent"), fm)[0] == UNREADABLE)

    # A CALLER MAY HAND THE WRITE SET STRAIGHT IN. The landing step reads an
    # archived order's writes out of the store as paths, and the alternative was
    # synthesising a fake `writes:` line for this function to re-parse.
    want("an explicitly passed write set is used instead of the order text",
         check(Path("/nonexistent"), "---\nstatus: pending\n---\n",
               declared=d)[0] == UNREADABLE)
    want("and an explicitly EMPTY set still reports undeclared, never confined",
         check(Path("/nonexistent"), fm, declared=frozenset())[0] == UNDECLARED)

    print(f"\npassed: {len(fails)} failure(s)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check-confined")
    ap.add_argument("lane", nargs="?")
    ap.add_argument("--worktree", help="explicit worktree path")
    ap.add_argument("--order", help="explicit order file")
    ap.add_argument("--base", default="origin/main",
                    help="comparison base ref (default: origin/main)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()
    if not a.worktree or not a.order:
        print("need --worktree and --order (or a lane name once the store exists)",
              file=sys.stderr)
        return UNREADABLE

    try:
        order_text = Path(a.order).read_text()
    except OSError as e:
        print(f"unreadable order: {e}", file=sys.stderr)
        return UNREADABLE

    code, changed, offenders = check(Path(a.worktree), order_text, a.base)
    print(f"{REASON[code]}: {len(changed)} changed path(s)")
    for p in offenders:
        print(f"  ESCAPED  {p}")
    return code


if __name__ == "__main__":
    sys.exit(main())
