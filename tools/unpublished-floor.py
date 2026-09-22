#!/usr/bin/env python3
"""unpublished-floor.py: a NEW private tool must not appear unnoticed.

Goal 14 in queue/global.md. tools/lint.py WARNS that many tools have no public
counterpart, and a warning that never fails is a detector nobody obeys. This
turns that count into an exit code against a FLOOR, so the existing backlog
stays legal while a NEW private tool goes red.

    python3 tools/unpublished-floor.py --self-test
    python3 tools/unpublished-floor.py --private <dir> --public <dir>
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

# Measured 2026-09-21 by tools/lint.py. RAISING THIS IS A DECISION, not a fix.
FLOOR = 147


def unpublished(private: pathlib.Path, public: pathlib.Path) -> list[str]:
    """Tool filenames present privately and absent publicly."""
    have = {p.name for p in public.glob("*.py")}
    return sorted(p.name for p in private.glob("*.py") if p.name not in have)


def check(private: pathlib.Path, public: pathlib.Path,
          floor: int = FLOOR) -> tuple[int, str]:
    """(exit_code, sentence). 0 fine, 1 over the floor, 2 cannot tell."""
    if not private.is_dir():
        return 2, f"no private tools directory at {private}"
    if not public.is_dir():
        # SILENCE AND SUCCESS MUST NOT LOOK ALIKE. A missing public clone
        # would make every tool look published, so refuse to answer.
        return 2, f"no public clone at {public}, so the count is UNKNOWN, not 0"
    missing = unpublished(private, public)
    if len(missing) > floor:
        return 1, (f"{len(missing)} tools have no public counterpart, above the "
                   f"floor of {floor}. First offenders: "
                   + ", ".join(missing[:5]))
    return 0, f"{len(missing)} unpublished, at or under the floor of {floor}"


def self_test() -> int:
    fails: list[str] = []
    n = 0

    def want(cond, label, got=None):
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{label}  [got {got!r}]")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        priv = root / "private"
        pub = root / "public"
        priv.mkdir()
        pub.mkdir()
        for name in ("a.py", "b.py", "c.py"):
            (priv / name).write_text("x", encoding="utf-8")
        (pub / "a.py").write_text("x", encoding="utf-8")

        want(unpublished(priv, pub) == ["b.py", "c.py"],
             "only the tools with no public twin are counted",
             unpublished(priv, pub))

        code, why = check(priv, pub, floor=1)
        want(code == 1, "two over a floor of one is a FAILURE", code)
        want("above the floor" in why, "and it says so in words", why)

        code, why = check(priv, pub, floor=2)
        want(code == 0, "at the floor it passes, so the backlog stays legal",
             code)

        code, why = check(priv, root / "absent", floor=99)
        want(code == 2, "a missing public clone REFUSES rather than saying 0",
             code)
        want("UNKNOWN" in why, "and names the count as unknown", why)

    for f in fails:
        print(f"FAIL {f}")
    print(f"unpublished-floor self-test: {n - len(fails)}/{n} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="unpublished-floor")
    ap.add_argument("--private", default="tools")
    ap.add_argument("--public")
    ap.add_argument("--floor", type=int, default=FLOOR)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.public:
        print("unpublished-floor: --public is required outside --self-test",
              file=sys.stderr)
        return 2
    code, why = check(pathlib.Path(a.private), pathlib.Path(a.public), a.floor)
    print(why)
    return code


if __name__ == "__main__":
    sys.exit(main())
