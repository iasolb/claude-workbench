#!/usr/bin/env python3
"""publish-tool.py: copy ONE tool to the public workbench, after its check.

`mirror-sync.py --apply --include-missing` is all-or-nothing: it would push
every unpublished file at once with no per-file review, which is why the
unpublished floor in tools/lint.py got raised instead of a tool getting
published. This is the per-file route.

The PII patterns and the workbench lookup are REUSED from mirror-sync, never
copied: one fact, one place.

    python3 tools/publish-tool.py finish-landing.py
    python3 tools/publish-tool.py finish-landing.py --dry-run
    python3 tools/publish-tool.py --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import shutil
import sys

TOOLS = pathlib.Path(__file__).resolve().parent


def _mirror_sync():
    """Load the hyphenated sibling module, which import cannot name."""
    path = TOOLS / "mirror-sync.py"
    spec = importlib.util.spec_from_file_location("mirror_sync", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def publish(name: str, dry_run: bool = False, ms=None,
            wb: pathlib.Path | None = None) -> tuple[int, str]:
    """(exit_code, sentence). 0 published, 1 refused, 2 cannot tell."""
    if "/" in name or "\\" in name or name.startswith("."):
        return 2, f"give a bare tool filename, not a path: {name}"
    src = TOOLS / name
    if not src.is_file():
        return 2, f"no such tool: {src}"

    ms = ms or _mirror_sync()
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

    dest = pathlib.Path(wb) / "tools" / name
    if dry_run:
        return 0, f"WOULD publish {name} to {dest} (it is clean)"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    except OSError as exc:
        return 2, f"could not copy {name} to {dest}: {exc}"
    return 0, f"published {name} to {dest}. Commit and push the workbench."


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

    for f in fails:
        print(f"FAIL {f}")
    print(f"publish-tool self-test: {n - len(fails)}/{n} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="publish-tool")
    ap.add_argument("name", nargs="?", help="a bare tool filename")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.name:
        print("publish-tool: name a tool, or pass --self-test",
              file=sys.stderr)
        return 2
    code, why = publish(a.name, dry_run=a.dry_run)
    print(why, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
