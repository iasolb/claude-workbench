#!/usr/bin/env python3
"""publish-tool.py: copy ONE tool to the public workbench or a kit, after its check.

`mirror-sync.py --apply --include-missing` is all-or-nothing: it would push
every unpublished file at once with no per-file review, which is why the
unpublished floor in tools/lint.py got raised instead of a tool getting
published. This is the per-file route.

The PII patterns and the workbench lookup are REUSED from mirror-sync, never
copied: one fact, one place.

    python3 tools/publish-tool.py finish-landing.py
    python3 tools/publish-tool.py finish-landing.py --dry-run
    python3 tools/publish-tool.py gatefloor.py --to ai-kit
    python3 tools/publish-tool.py --self-test

A KIT IS A NAMED TARGET (Ian, 2026-10-21, in a form: "Inside the kits"). A
useful tool lands in the kit it fits, in that kit's tools/, and
tools/ship-recency.py keeps it identical to this copy on every landing.
`store:facts/repos/snippets_live_in_the_kits`. A kit copy must STAND ALONE, so
its self-test is first run in an empty folder with nothing beside it, and a
tool that needs our other internals is refused rather than published broken
(found 2026-10-21: find-duplicates imported the TOON encoder lazily).
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

TOOLS = pathlib.Path(__file__).resolve().parent

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import machinelib  # noqa: E402  the branch names' one home

# The kits a tool may be published into, and where they are cloned: ~/dev on
# both machines, the one home tools/ship-recency.py already keeps their pins
# in. A kit is written only from the SOURCE branch, so another machine's
# possibly older copy of a tool never overwrites the published one.
KITS = ("ai-kit", "data-kit", "cloud-kit")
KITS_HOME = pathlib.Path.home() / "dev"
# The source of truth is the PC's branch, read from machinelib's map rather
# than spelled here a second time.
SOURCE_BRANCH = next(b for b, m in machinelib.BRANCH_TO_MACHINE.items() if m == "pc")


def bank_branch() -> str:
    """The branch this memory bank checkout is on, or '' when git cannot say."""
    r = subprocess.run(["git", "-C", str(TOOLS.parent), "branch", "--show-current"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def kit_root(kit: str, home: pathlib.Path = KITS_HOME) -> pathlib.Path | None:
    """This machine's checkout of one kit, or None when it is not cloned."""
    path = home / kit
    return path if (path / ".git").exists() else None


def runs_alone(src: pathlib.Path, timeout: int = 180) -> str | None:
    """None when the tool's self-test passes with NOTHING beside it, else why
    not. A sibling import that works in this repo breaks in a kit."""
    with tempfile.TemporaryDirectory() as tmp:
        lone = pathlib.Path(tmp) / src.name
        shutil.copyfile(src, lone)
        try:
            r = subprocess.run([sys.executable, str(lone), "--self-test"],
                               cwd=tmp, capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"its self-test ran past {timeout}s"
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        return f"self-test exit {r.returncode}: {tail[-1] if tail else 'no output'}"
    return None


def _mirror_sync():
    """Load the hyphenated sibling module, which import cannot name."""
    path = TOOLS / "mirror-sync.py"
    spec = importlib.util.spec_from_file_location("mirror_sync", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def publish(name: str, dry_run: bool = False, ms=None,
            wb: pathlib.Path | None = None, kit: str | None = None,
            home: pathlib.Path = KITS_HOME, alone=None,
            branch: str | None = None) -> tuple[int, str]:
    """(exit_code, sentence). 0 published, 1 refused, 2 cannot tell."""
    if "/" in name or "\\" in name or name.startswith("."):
        return 2, f"give a bare tool filename, not a path: {name}"
    src = TOOLS / name
    if not src.is_file():
        return 2, f"no such tool: {src}"

    ms = ms or _mirror_sync()
    if kit is not None:
        if kit not in KITS:
            return 2, f"no such kit: {kit} (one of {', '.join(KITS)})"
        on = bank_branch() if branch is None else branch
        if on != SOURCE_BRANCH:
            return 2, (f"kits are written only from the {SOURCE_BRANCH} branch, and "
                       f"this bank is on {on or 'an unknown branch'}, so nothing "
                       f"was published")
        wb = kit_root(kit, home)
        if wb is None:
            return 2, f"{kit} is not cloned under {home}, so nothing was published"
    if wb is None:
        wb = ms.workbench()
    if wb is None:
        # COULD NOT ASK is never NOTHING THERE. Refusing here is the whole
        # point: a missing workbench must not read as a successful publish.
        return 2, "the public workbench could not be located, so nothing was published"

    found = ms.pii_in(src)
    if found:
        shown = ", ".join(str(f) for f in list(found)[:4])
        return 1, (f"REFUSED {name}: it carries things a public repo must not "
                   f"have ({shown}). Sanitise it, then publish.")
    if kit is not None:
        why_not = (alone or runs_alone)(src)
        if why_not:
            return 1, (f"REFUSED {name}: it does not run on its own ({why_not}), "
                       f"so it was not published into {kit}")

    dest = pathlib.Path(wb) / "tools" / name
    if dry_run:
        return 0, f"WOULD publish {name} to {dest} (it is clean)"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    except OSError as exc:
        return 2, f"could not copy {name} to {dest}: {exc}"
    return 0, f"published {name} to {dest}. Commit and push that repo."


def self_test() -> int:
    import tempfile

    fails: list[str] = []
    n = 0

    def want(cond, label, got=None):
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{label}  [got {got!r}]")

    class FakeMS:
        def __init__(self, dirty=()):
            self.dirty = set(dirty)

        def workbench(self, root=None):
            return None

        def pii_in(self, path):
            return ["a-username"] if path.name in self.dirty else []

    want(publish("nope-does-not-exist.py")[0] == 2,
         "a tool that does not exist is a REFUSAL, not a publish")
    want(publish("../outside.py")[0] == 2,
         "a path instead of a bare name is refused")

    with tempfile.TemporaryDirectory() as tmp:
        wb = pathlib.Path(tmp) / "wb"
        (wb / "tools").mkdir(parents=True)

        # A CLEAN FILE PUBLISHES. Use this file itself as the fixture.
        code, why = publish(pathlib.Path(__file__).name, ms=FakeMS(), wb=wb)
        want(code == 0, "a clean tool publishes", why)
        want((wb / "tools" / pathlib.Path(__file__).name).is_file(),
             "and the file really is on the public side now")

        # A DIRTY FILE IS REFUSED, AND THE MATCH IS NAMED.
        me = pathlib.Path(__file__).name
        code, why = publish(me, ms=FakeMS(dirty={me}), wb=wb)
        want(code == 1, "a tool carrying private material is REFUSED", code)
        want("a-username" in why, "and the refusal NAMES what it found", why)

        # A MISSING WORKBENCH REFUSES rather than looking like a success.
        code, why = publish(me, ms=FakeMS())
        want(code == 2, "no workbench means no publish, loudly", code)

        # A DRY RUN COPIES NOTHING.
        wb2 = pathlib.Path(tmp) / "wb2"
        (wb2 / "tools").mkdir(parents=True)
        code, why = publish(me, dry_run=True, ms=FakeMS(), wb=wb2)
        want(code == 0 and not (wb2 / "tools" / me).exists(),
             "a dry run reports and copies nothing", why)

        # A KIT: an unknown one is refused, an uncloned one refuses loudly,
        # a cloned one receives the tool, and the same refusal applies.
        home = pathlib.Path(tmp) / "home"
        src = SOURCE_BRANCH
        code, why = publish(me, kit="no-such-kit", ms=FakeMS(), home=home, branch=src)
        want(code == 2, "an unknown kit is refused", why)
        code, why = publish(me, kit="ai-kit", ms=FakeMS(), home=home, branch=src)
        want(code == 2 and "not cloned" in why,
             "a kit with no checkout refuses rather than looking published", why)
        (home / "ai-kit" / ".git").mkdir(parents=True)
        code, why = publish(me, kit="ai-kit", ms=FakeMS(), home=home,
                            alone=lambda p: None, branch="mac")
        want(code == 2 and not (home / "ai-kit" / "tools" / me).exists(),
             "a bank off the source branch never writes a kit", why)
        code, why = publish(me, kit="ai-kit", ms=FakeMS(), home=home, branch=src,
                            alone=lambda p: "self-test exit 1: no module named x")
        want(code == 1 and not (home / "ai-kit" / "tools" / me).exists(),
             "a tool that does not run on its own is refused and copies nothing", why)
        code, why = publish(me, kit="ai-kit", ms=FakeMS(), home=home,
                            alone=lambda p: None, branch=src)
        want(code == 0 and (home / "ai-kit" / "tools" / me).is_file(),
             "a clean tool lands in the named kit's tools/", why)

        # runs_alone ITSELF, on two throwaway tools: one needs nothing, one
        # imports a module that only exists beside it in the real repo.
        lone = pathlib.Path(tmp) / "lone.py"
        lone.write_text("import sys\nprint('1 check passed')\nsys.exit(0)\n",
                        encoding="utf-8")
        needy = pathlib.Path(tmp) / "needy.py"
        needy.write_text("import a_sibling_only_the_repo_has\n", encoding="utf-8")
        want(runs_alone(lone) is None, "a tool needing nothing runs alone")
        why_not = runs_alone(needy)
        want(why_not is not None and "a_sibling_only_the_repo_has" in why_not,
             "a tool importing a sibling does not, and the reason names it", why_not)
        code, why = publish(me, kit="ai-kit", ms=FakeMS(dirty={me}), home=home,
                            branch=src)
        want(code == 1, "a kit gets the same personal-data refusal", code)

    for f in fails:
        print(f"FAIL {f}")
    print(f"publish-tool self-test: {n - len(fails)}/{n} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="publish-tool")
    ap.add_argument("name", nargs="?", help="a bare tool filename")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--to", choices=KITS,
                    help="publish into this kit's tools/ instead of the workbench")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.name:
        print("publish-tool: name a tool, or pass --self-test",
              file=sys.stderr)
        return 2
    code, why = publish(a.name, dry_run=a.dry_run, kit=a.to)
    print(why, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
