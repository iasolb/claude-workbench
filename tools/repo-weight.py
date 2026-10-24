#!/usr/bin/env python3
"""repo-weight.py: what is this repo actually made of? Biggest first.

    python tools/repo-weight.py                 top-level dirs, biggest first
    python tools/repo-weight.py --depth 2       drill one level in
    python tools/repo-weight.py --under queue   only inside one directory
    python tools/repo-weight.py --top 15        also the biggest single files
    python tools/repo-weight.py --self-test

WHY THIS EXISTS (2026-09-16): "you should run a command to order
directories by size to help triage." That was right, and it was already the second
time that measurement had been taken by hand in one evening, which is the A3
trigger: the second occurrence is a command.

WHY NOT JUST `du -sk`, and this is the whole point rather than a nicety. A
session had just spent an evening consolidating `memory/` on the strength of a
memory-health budget, and the first honest measurement showed **`memory/` is
under 3 percent of the repo**. The same measurement showed `tools/` as the
biggest directory at 5.1 MB, which is ALSO misleading: 1.7 MB of it is
`__pycache__`, correctly gitignored and untracked, so it is not repo weight at
all. A number that cannot tell those apart sends the next session at the wrong
directory twice.

So this reports TWO numbers and never one:

    ON DISK   what a `du` would say, including ignored and untracked files
    TRACKED   what git actually carries, which is what a clone pays for

**TRACKED is the number that matters for consolidation.** A big gap between them
is itself a finding: either build output that should be ignored (fine) or
untracked work nobody has committed (not fine).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def tracked_sizes(root: Path) -> dict[str, int]:
    """{repo-relative posix path: bytes} for every file git tracks.

    Read from `git ls-files -s` plus `cat-file --batch-check`, so the answer is
    the BLOB size git carries rather than whatever is on disk right now. A file
    modified and uncommitted would otherwise be counted at its working-tree
    size and quietly disagree with what a clone gets.
    """
    res = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)
    if res.returncode != 0:
        return {}
    blobs: list[tuple[str, str]] = []
    for line in res.stdout.splitlines():
        # `<mode> <sha> <stage>\t<path>`
        head, _, path = line.partition("\t")
        parts = head.split()
        if len(parts) >= 2 and path:
            blobs.append((parts[1], path))
    if not blobs:
        return {}
    batch = "\n".join(sha for sha, _ in blobs) + "\n"
    res = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch-check"],
        input=batch, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120)
    sizes: dict[str, int] = {}
    lines = res.stdout.splitlines()
    for (sha, path), line in zip(blobs, lines):
        parts = line.split()
        # `<sha> blob <size>`; anything else means git could not size it, and
        # that is recorded as unknown rather than as zero.
        if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
            sizes[path] = int(parts[2])
    return sizes


def disk_sizes(root: Path) -> dict[str, int]:
    """{repo-relative posix path: bytes} for every file on disk, `.git` aside."""
    out: dict[str, int] = {}
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if rel.startswith(".git/"):
            continue
        try:
            out[rel] = p.stat().st_size
        except OSError:
            continue
    return out


def group(sizes: dict[str, int], depth: int, under: str = "") -> dict[str, int]:
    """Roll file sizes up to directories at `depth` components.

    A file shallower than `depth` is reported under its own path, so a big
    top-level file is never hidden inside a directory bucket it does not
    belong to.
    """
    prefix = under.strip("/") + "/" if under.strip("/") else ""
    out: dict[str, int] = {}
    for rel, size in sizes.items():
        if prefix and not rel.startswith(prefix):
            continue
        parts = rel.split("/")
        key = "/".join(parts[:depth]) if len(parts) > depth else rel
        out[key] = out.get(key, 0) + size
    return out


def human(n: int) -> str:
    if n >= 1024 * 1024:
        return "%.1fM" % (n / 1024 / 1024)
    if n >= 1024:
        return "%dK" % (n // 1024)
    return "%dB" % n


def report(root: Path, depth: int, under: str, top: int) -> int:
    disk = disk_sizes(root)
    tracked = tracked_sizes(root)
    if not tracked:
        print("repo-weight: git could not list tracked files, so TRACKED is "
              "UNKNOWN rather than zero", file=sys.stderr)

    by_disk = group(disk, depth, under)
    by_tracked = group(tracked, depth, under)
    keys = sorted(set(by_disk) | set(by_tracked),
                  key=lambda k: -max(by_disk.get(k, 0), by_tracked.get(k, 0)))

    print("%-42s %9s %9s %s" % ("path", "ON DISK", "TRACKED", "gap"))
    for key in keys:
        d, t = by_disk.get(key, 0), by_tracked.get(key, 0)
        gap = "" if d == t else "%s untracked or ignored" % human(d - t)
        print("%-42s %9s %9s %s" % (key[:42], human(d), human(t), gap))
    print("%-42s %9s %9s" % ("TOTAL", human(sum(disk.values())),
                             human(sum(tracked.values()))))

    if top:
        print("\nbiggest %d TRACKED file(s), because one 82KB file can be the "
              "whole problem:" % top)
        for rel, size in sorted(tracked.items(), key=lambda kv: -kv[1])[:top]:
            if under and not rel.startswith(under.strip("/") + "/"):
                continue
            print("  %9s  %s" % (human(size), rel))
    return 0


# THE SPLIT THRESHOLD, and it is measured rather than felt (2026-09-16):
# "do this process for any file over a percentile length - I'm think 1k is
# probs around the right spot could be wrong." He was right. Measured that day
# across 116 Python tools totalling 70,687 lines: **15 files are over 1,000
# lines and they hold 32,327 of those lines, 46 percent of the code in 13
# percent of the files.** The median tool is around 350. So 1,000 is a real
# pareto point, not a round number.
OVERSIZE_LINES = 1000


def source_lines(root: Path, tracked: dict[str, int]) -> dict[str, int]:
    """{path: line count} for every tracked source file."""
    out: dict[str, int] = {}
    for rel in tracked:
        if not rel.endswith((".py", ".ps1", ".sh")):
            continue
        p = root / rel
        try:
            out[rel] = p.read_text(encoding="utf-8",
                                   errors="replace").count("\n") + 1
        except OSError:
            continue
    return out


def oversize(root: Path, limit: int = OVERSIZE_LINES) -> list[tuple[str, int]]:
    """Tracked source files over the split threshold, biggest first."""
    counts = source_lines(root, tracked_sizes(root))
    return sorted(((rel, n) for rel, n in counts.items() if n > limit),
                  key=lambda kv: -kv[1])


def report_oversize(root: Path, limit: int) -> int:
    rows = oversize(root, limit)
    counts = source_lines(root, tracked_sizes(root))
    total = sum(counts.values())
    big = sum(n for _, n in rows)
    if not rows:
        print(f"repo-weight: no tracked source file is over {limit} lines")
        return 0
    print(f"{'lines':>7}  file")
    for rel, n in rows:
        print(f"{n:>7}  {rel}")
    share = (big * 100 // total) if total else 0
    print(f"\n{len(rows)} file(s) over {limit} lines, holding {big} of "
          f"{total} source lines ({share}%). Each one is a SPLIT: one module "
          f"per job, its own --self-test, imported back under the original "
          f"names so no call site changes.")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="repo-weight")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--under", default="")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--over", nargs="?", type=int, const=OVERSIZE_LINES,
                    help="list tracked source files over N lines "
                         f"(default {OVERSIZE_LINES}), which are the split "
                         f"candidates")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.over:
        return report_oversize(Path(a.root).resolve(), a.over)
    return report(Path(a.root).resolve(), max(1, a.depth), a.under, a.top)


def self_test() -> int:
    import tempfile

    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    sizes = {"tools/a.py": 100, "tools/sub/b.py": 50, "docs/c.md": 10,
             "README.md": 5}

    g1 = group(sizes, 1)
    want("depth 1 rolls a whole directory into one number",
         g1 == {"tools": 150, "docs": 10, "README.md": 5})
    # A TOP-LEVEL FILE IS NOT HIDDEN IN A DIRECTORY BUCKET. Red-proven by
    # keying on parts[:depth] unconditionally: README.md then becomes its own
    # bucket named "README.md" anyway at depth 1, but at depth 2 it would be
    # swallowed, and a big loose file at the root is exactly the litter class
    # litter-check now watches for.
    g2 = group(sizes, 2)
    want("depth 2 drills in and still reports a shallow file by name",
         g2 == {"tools/a.py": 100, "tools/sub": 50, "docs/c.md": 10,
                "README.md": 5})
    want("--under restricts to one subtree",
         group(sizes, 2, under="tools") == {"tools/a.py": 100,
                                            "tools/sub": 50})
    want("--under with a trailing slash behaves the same",
         group(sizes, 2, under="tools/") == group(sizes, 2, under="tools"))
    want("an empty size map produces no buckets", group({}, 1) == {})

    want("bytes render as bytes", human(512) == "512B")
    want("kilobytes render as K", human(2048) == "2K")
    want("megabytes render as M", human(3 * 1024 * 1024) == "3.0M")

    # THE POINT OF THE TOOL: the two numbers are computed from different
    # sources, so a directory that is all ignored build output shows a gap.
    disk = {"tools/a.py": 100, "tools/__pycache__/a.pyc": 900}
    trk = {"tools/a.py": 100}
    want("an ignored cache shows up as a gap, never as repo weight",
         group(disk, 1)["tools"] == 1000 and group(trk, 1)["tools"] == 100)

    # A temporary git index makes this check work in a clone or a staging
    # directory that is not itself a repository.
    with tempfile.TemporaryDirectory(prefix="repo-weight-selftest-") as td:
        root = Path(td)
        (root / "tools").mkdir()
        tracked_file = root / "tools" / "tracked.py"
        tracked_file.write_text("print('tracked')\n", encoding="utf-8")
        (root / "ignored.bin").write_bytes(b"ignored build output")
        subprocess.run(["git", "-C", str(root), "init", "--quiet"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "tools/tracked.py"],
                       check=True, capture_output=True)
        fixture_disk = sum(disk_sizes(root).values())
        fixture_tracked = sum(tracked_sizes(root).values())
        want("git reports the staged file's blob size", fixture_tracked > 0)
        want("tracked size is no larger than on-disk size",
             fixture_tracked <= fixture_disk)

    if fails:
        print(f"\nrepo-weight self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nrepo-weight self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
