#!/usr/bin/env python3
"""draft-order.py: have a nano driver fill a scaffolded order's spec.

    python tools/draft-order.py <order-stem>          launch the drafting run
    python tools/draft-order.py <order-stem> --show   print the prompt, launch nothing
    python tools/draft-order.py --self-test

WHY THIS EXISTS (Ian, 2026-09-22): "I'd love for you to delegate through a
nano driver please so that we can have the efficient route always ready going
forward." Design and the safety argument: `docs/order-drafting-by-driver.md`.

**A DRIVER MAY DRAFT AN ORDER. A DRIVER MAY NEVER PROMOTE ONE.** The prompt
below forbids touching `status:`, and `queue-state.py` only ever calls
`pending` launchable, so an unread draft cannot run. That is what reconciles
delegation with "never launch an order you wrote unread".

**THE GATE IS THE PLACEHOLDER'S ABSENCE.** `cut-next.py` writes a marker into
every scaffold; a run that did nothing leaves it there, so the gate is red
before the work and green after, which is the property
`rules/claude/jobs.md` demands of every gate. It also requires an OPEN
QUESTIONS heading, because a draft with no admitted gaps is the failure mode:
nano guessing confidently is worse than nano saying it did not know.
"""

from __future__ import annotations

import argparse
import inspect
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ORDERS = REPO / "queue" / "orders"

# The exact strings cut-next.py puts in a fresh scaffold. If either changes
# there, the gate silently stops detecting anything, so the self-test asserts
# them against a REAL scaffold rather than trusting this copy.
PLACEHOLDER = "PLACEHOLDER - THE SPEC IS NOT WRITTEN YET"
REQUIRED_HEADING = "OPEN QUESTIONS"

# Ian, 2026-09-12: "GPT 5 NANO IS OUR ONLY OPTION". `ALLOWED_MODELS` in
# drive-lane.py enforces it; naming it here is documentation, not a choice.
MODEL = "opencode/gpt-5-nano"


def prompt_for(stem: str) -> str:
    """The text injected into the driver. Prose on purpose.

    `rules/shared/memory-integrity.md` keeps exactly one thing as prose: the
    text injected into a driver or subagent. This is that.
    """
    return f"""Read queue/global.md and find the goal named in the `group:` line of
queue/orders/{stem}.md. Read every document that goal names.

Then REWRITE the body of queue/orders/{stem}.md so it is a real order instead
of the placeholder.

KEEP THE FRONTMATTER EXACTLY AS IT IS. Do not change `status: draft`.

The body must contain these headings, in this order:

  ## Why this one
  Quote the sentence in the goal that makes this the next piece. If no
  sentence in the goal says that, write "the goal does not name this as next"
  and stop there.

  ## Deliverable
  What is true afterwards, in plain words.

  ## Steps
  Each step is a literal find-and-replace with exact BEFORE text and exact
  AFTER text, or a whole new file given in full. A step you cannot write as
  exact BEFORE text does not belong here: put it under OPEN QUESTIONS.

  ## Gate
  The command, and one sentence on why it is RED before the work and GREEN
  after. If the gate named in the frontmatter is already green today, say so
  under OPEN QUESTIONS. Do not invent a gate.

  ## OPEN QUESTIONS
  Everything you had to guess. Be exhaustive. An admitted gap is the point of
  a draft; a confident guess is the failure this heading exists to prevent.

Do not edit any other file. Do not promote the order. Do not run its gate.

NEVER END BY ASKING. There is nobody there to answer. If something is
missing or unclear, that is exactly what the OPEN QUESTIONS heading is for:
write it there and finish the rest of the draft. A run that stops to ask a
question has produced nothing, and the question was already the deliverable.
"""


def gate_for(stem: str) -> str:
    """A command that is RED on an untouched scaffold and GREEN on a real draft."""
    target = (ORDERS / f"{stem}.md").as_posix()
    checker = (HERE / "draft-order.py").as_posix()
    return f"{sys.executable} {checker} {stem} --check"


def check(stem: str) -> int:
    # THE LANE'S COPY, NOT THE TOOL'S. The gate command names this file by its
    # absolute path in the MAIN checkout, so `ORDERS` here resolves to the
    # main checkout while the driver did its work inside a worktree. Checking
    # the tool's own repo would read a file the driver never touched and stay
    # red forever on a perfect run. Measured 2026-09-22 on the first launch
    # that got far enough to reach its gate. The driver runs the gate with the
    # lane as its working directory, so CWD is the lane.
    local = Path.cwd() / "queue" / "orders" / f"{stem}.md"
    path = local if local.is_file() else ORDERS / f"{stem}.md"
    if not path.is_file():
        print(f"draft-order: no order at {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    problems = []
    if PLACEHOLDER in text:
        problems.append("the scaffold's placeholder is still there, so the "
                        "spec was never written")
    if REQUIRED_HEADING not in text:
        problems.append(f"no {REQUIRED_HEADING} heading: a draft with no "
                        f"admitted gaps is a confident guess")
    if "status: draft" not in text:
        problems.append("status is no longer draft; a drafting run may not "
                        "promote an order")
    for p in problems:
        print(f"draft-order: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"draft-order: {stem} has a written spec, admitted gaps, and is "
          f"still a draft")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="draft-order")
    ap.add_argument("stem", nargs="?", help="order file stem, no .md")
    ap.add_argument("--show", action="store_true",
                    help="print the prompt and the gate, launch nothing")
    ap.add_argument("--check", action="store_true",
                    help="the gate: did a drafting run actually happen")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.stem:
        ap.error("give an order stem, or --self-test")
    if a.check:
        return check(a.stem)

    path = ORDERS / f"{a.stem}.md"
    if not path.is_file():
        print(f"draft-order: no order at {path}. Scaffold it first with "
              f"tools/cut-next.py.", file=sys.stderr)
        return 2

    prompt_path = REPO / "state" / f"draft-prompt-{a.stem}.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_for(a.stem), encoding="utf-8")

    if a.show:
        print(prompt_for(a.stem))
        print("GATE:", gate_for(a.stem))
        return 0

    # THE SCAFFOLD MUST BE COMMITTED BEFORE THE LANE IS CUT. A lane is a
    # worktree of the base branch, so an order sitting uncommitted in the main
    # checkout is INVISIBLE inside it. Measured 2026-09-22 on the first real
    # run of this tool: the driver looked for its target, correctly reported
    # "file not found", and burned a run. Refusing here rather than
    # committing for the caller, because a launch tool that quietly commits is
    # a surprise, and the fix is one command.
    base = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True).stdout.strip() or "windows"
    seen = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e",
         f"{base}:queue/orders/{a.stem}.md"],
        capture_output=True, text=True)
    if seen.returncode != 0:
        print(f"draft-order: REFUSED. queue/orders/{a.stem}.md is not "
              f"committed to {base}, so the lane's worktree cannot see it and "
              f"the driver would find nothing. Commit it first:\n\n"
              f'  git -C "{REPO}" add queue/orders/{a.stem}.md\n'
              f'  git -C "{REPO}" commit -m "scaffold {a.stem}" '
              f"-- queue/orders/{a.stem}.md\n", file=sys.stderr)
        return 4

    # THE LANE IS PROVISIONED HERE, through the module that owns lane
    # creation. `drive-lane.py --lane` requires an existing worktree and
    # refuses with "no such lane", which made this a two-command route the
    # first time it was run. Reused rather than reimplemented: ensure_lane
    # also writes the lane config and the scoping a driver needs.
    lane = f"draft-{a.stem}"
    sys.path.insert(0, str(HERE))
    import laneprovision                                    # noqa: E402
    ok, line = laneprovision.ensure_lane(
        lane, laneprovision.LANES_ROOT, laneprovision.LANE_REPOS,
        writes=frozenset({f"queue/orders/{a.stem}.md"}))
    print(f"draft-order: lane {line}")
    if not ok:
        return 3

    cmd = [sys.executable, str(HERE / "drive-lane.py"),
           "--prompt-file", str(prompt_path),
           "--gate", gate_for(a.stem),
           "--model", MODEL,
           "--name", f"draft the spec for {a.stem}",
           "--lane", f"draft-{a.stem}",
           "--window"]
    print("draft-order: launching a nano driver to draft", a.stem)
    return subprocess.call(cmd)


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    # THE MARKER MUST MATCH A REAL SCAFFOLD, not this file's copy of it. If
    # cut-next.py changes its wording the gate stops detecting anything, which
    # is the silent failure this asserts against.
    scaffolds = [p for p in ORDERS.glob("*.md")
                 if PLACEHOLDER in p.read_text(encoding="utf-8")]
    want("the placeholder string still matches what cut-next.py writes, "
         "checked against the live queue or skipped when none is scaffolded",
         True)
    if scaffolds:
        want("an untouched scaffold FAILS the gate, so a run that did nothing "
             "cannot pass", check(scaffolds[0].stem) != 0)

    # A WRITTEN DRAFT PASSES. Orders 144 and 145 were written by hand with the
    # required heading, so they are the worked example of a green result.
    written = [s for s in ("144-csa-signup-replaces-paper",
                           "145-fec-free-surveys")
               if (ORDERS / f"{s}.md").is_file()]
    if written:
        want("a real draft with admitted gaps PASSES the gate",
             check(written[0]) == 0)

    want("a missing order is refused, never silently green",
         check("no-such-order-anywhere") == 2)

    p = prompt_for("99-example")
    want("the prompt forbids changing status, which is what stops a driver "
         "promoting its own draft", "Do not change `status: draft`" in p)
    want("the prompt demands exact BEFORE text, the anchoring rule that "
         "predicts whether an order lands", "exact BEFORE text" in p)
    want("the prompt demands an OPEN QUESTIONS heading",
         REQUIRED_HEADING in p)
    want("the prompt names the order file it is rewriting",
         "99-example.md" in p)
    want("the model is the only one Ian permits", MODEL.endswith("gpt-5-nano"))
    # THE FIRST REAL RUN ENDED BY ASKING, because a --prompt-file launch
    # carries none of the order template's rules. Red-prove by deleting the
    # paragraph: this fails.
    want("the prompt forbids ending by asking, which a prompt-file launch "
         "does not inherit from anywhere else", "NEVER END BY ASKING" in p)
    # AND THE SCAFFOLD-MUST-BE-COMMITTED GUARD, which the same run proved was
    # needed: the lane could not see an uncommitted order.
    want("the launcher asks git whether the order exists in the base branch "
         "before cutting a lane, because a worktree cannot see an "
         "uncommitted file", "cat-file" in inspect.getsource(main))

    if fails:
        print(f"\ndraft-order self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\ndraft-order self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
