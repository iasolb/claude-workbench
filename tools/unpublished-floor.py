#!/usr/bin/env python3
"""Fail when a private tools directory exceeds its allowed unpublished count.

    python tools/unpublished-floor.py --private tools --public public/tools
    python tools/unpublished-floor.py --private tools --public public/tools --floor 3

The default floor is zero. Raise it explicitly when an existing backlog is
accepted; the count is unknown, not zero, if either directory is unavailable.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

FLOOR = 0


def unpublished(private: pathlib.Path, public: pathlib.Path) -> list[str]:
    """Names of Python tools missing from the public tools directory."""
    published = {path.name for path in public.glob("*.py")}
    return sorted(path.name for path in private.glob("*.py") if path.name not in published)


def check(private: pathlib.Path, public: pathlib.Path,
          floor: int = FLOOR) -> tuple[int, str]:
    """Return (status, message): 0 within floor, 1 over it, 2 unknown."""
    if not private.is_dir():
        return 2, f"no private tools directory at {private}"
    if not public.is_dir():
        return 2, f"no public tools directory at {public}, so the count is UNKNOWN, not 0"
    missing = unpublished(private, public)
    if len(missing) > floor:
        return 1, (f"{len(missing)} tools have no public counterpart, above the floor of {floor}. "
                   f"First offenders: {', '.join(missing[:5])}")
    return 0, f"{len(missing)} unpublished, at or under the floor of {floor}"


def self_test() -> int:
    failures: list[str] = []
    checks = 0

    def want(condition: bool, label: str, got=None) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(f"{label}  [got {got!r}]")

    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        private, public = root / "private", root / "public"
        private.mkdir()
        public.mkdir()
        for name in ("a.py", "b.py", "c.py"):
            (private / name).write_text("pass\n", encoding="utf-8")
        (public / "a.py").write_text("pass\n", encoding="utf-8")
        want(unpublished(private, public) == ["b.py", "c.py"],
             "only tools without public twins are counted", unpublished(private, public))
        code, message = check(private, public, floor=1)
        want(code == 1 and "above the floor" in message,
             "exceeding the floor fails with an explanation", message)
        code, _message = check(private, public, floor=2)
        want(code == 0, "meeting the floor passes", code)
        code, message = check(private, root / "absent", floor=99)
        want(code == 2 and "UNKNOWN" in message,
             "a missing public directory refuses instead of reporting zero", message)
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"unpublished-floor self-test: {checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="unpublished-floor")
    parser.add_argument("--private", default="tools")
    parser.add_argument("--public")
    parser.add_argument("--floor", type=int, default=FLOOR)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.public:
        print("unpublished-floor: --public is required outside --self-test", file=sys.stderr)
        return 2
    code, message = check(pathlib.Path(args.private), pathlib.Path(args.public), args.floor)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
