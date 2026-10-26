#!/usr/bin/env python3
"""reachable.py: which tracked files does NOTHING in this repo point at?

    reachable.py                    the unreached files, biggest first
    reachable.py --summary          counts per directory, nothing else
    reachable.py --roots            print what is treated as an entry point
    reachable.py --why <path>       who points at this file, if anyone
    reachable.py --min-bytes 4000   only report files worth the argument
    reachable.py --self-test

WHY (2026-09-07): "go file by file thru the repo and find what isnt being
reached to drop em ... stop littering markdown everywhere."

A file nobody points at is worse than clutter. `rules/shared/memory-integrity.md`
is explicit: stale files are the single largest contributor to internal token
burn, because they are not clutter, they are WRONG CONTEXT THAT READS AS
CURRENT. A session that greps the repo finds them and believes them.

HOW REACHABILITY IS DECIDED, and the honesty of this matters more than the
cleverness. A file is REACHED when some other file NAMES it, transitively from
a set of declared entry points. The entry points are enumerated in ROOTS below,
never inferred at runtime (`docs/scoped-by-declaration.md`).

WHAT THIS TOOL WILL NOT DO, on purpose:
  - It never deletes. It prints. Deleting is a judgement about whether the
    content still matters, and that is not a thing a path scan can know.
  - It never claims a file is dead. It claims NOTHING POINTS AT IT, which is a
    different and checkable statement.
  - A file reached only from another unreached file is reported as
    `orphan-subtree`, not as reached. A dead branch that points at itself is
    the most common way litter survives a scan.

Exit codes:
    0  nothing unreached above the threshold
    1  unreached files found (this is a report, not a failure of the tool)
    2  bad arguments
    3  cannot answer: not a git checkout, or git failed
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ENTRY POINTS, enumerated. Anything git-tracked and reachable from one of
# these is reached. A glob here, never a guess at runtime.
ROOTS = [
    "CLAUDE.md",              # always loaded
    "README.md",
    "settings.json",          # registers every hook
    "memory/MEMORY.md",       # the memory index
    "rules/**/*.md",          # imported or mandatory-read
    "hooks/*",                # run by the harness
    "tools/*.py",             # a session's command surface
    "commands/*.md",          # slash commands
    "agents/*.md",            # spawnable agents
    "queue/*.md",             # the live queues
    "queue/orders/*.md",      # live orders
    "db/*.sql",
    "automation/workflows/*",

    # CONSUMED FROM OUTSIDE THIS REPO, so nothing inside it will ever name
    # them. Listing them here is the difference between a tool that reports
    # litter and one that cries wolf; a check whose output is routinely
    # ignored has stopped being a check.
    "tools/*.cmd",            # The owner launches these directly; see below
    ".gitignore",             # read by git, never named by a file
    ".gitattributes",         # ditto, and it carries the union-merge rules
    "*/.gitignore",
    "**/Dockerfile",          # built by docker, not referenced in prose
    "assets/**",              # source images and CSS for a site
    "state/*.toon",           # per-machine state, written by hooks each session
]

# `tools/*.cmd` is in ROOTS for a reason worth stating, since it looks like a
# blanket excuse. `tools/drivers.cmd` exists precisely BECAUSE no session may
# launch it: the `Start-Process` argument-array form it replaced could never be
# allowlisted (an array literal is failure mode 1, which no entry can fix), so
# The owner launches the file instead. It is a human ENTRY POINT, like a hook is
# a harness entry point. Reporting it as unreached every run would be the
# permanently-red-instrument fault: a finding no sanctioned action can clear.

# Directories whose contents are ARCHIVE by design: git is the record, nothing
# is expected to point at them, and reporting them every run is noise. Named,
# with the reason, rather than silently skipped.
ARCHIVE = {
    "queue/jobs/done": "finished job cards, pipeline retired 2026-08-21",
    "queue/orders/done": "finished orders; rule 4 says the commit is the record",
    "reports/go": "one generated launcher report per run",
    "state/fixtures": "captured fixtures, referenced by test code not by name",
}

# A path-ish token: at least one slash or a known extension.
#
# LONGEST EXTENSION FIRST, and this is not cosmetic. Python's alternation takes
# the FIRST branch that matches, so with `json` before `jsonl` the token
# `reference/sky/knowledge.jsonl` matched as `...knowledge.json`, resolved to no
# tracked file, and the real file was reported as litter nobody points at.
# Found 2026-09-07 by running the tool against a file created the same hour.
# The trailing (?![A-Za-z0-9]) is the real guard: it makes a partial extension
# match impossible whatever the branch order.
TOKEN = re.compile(
    r"[A-Za-z0-9_./\\-]*[A-Za-z0-9_-]\.(?:jsonl|jsonc|json|yaml|yml|toml|toon|"
    r"md|py|sh|ps1|sql|tsv|csv|txt)(?![A-Za-z0-9])")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {out.stderr.strip()}")
    return [line for line in out.stdout.splitlines() if line.strip()]


def in_archive(path: str) -> str | None:
    for prefix, reason in ARCHIVE.items():
        if path.startswith(prefix + "/"):
            return reason
    return None


def is_root(path: str, roots: list[str]) -> bool:
    """Does this tracked path match a declared entry point?

    `Path.match` anchors on the RIGHT and does not implement `**` as "any
    depth", so `assets/**` silently matched nothing and four asset directories
    kept being reported as litter. A pattern ending in `/**` is therefore
    handled as a plain directory prefix, which is what it obviously means.
    """
    for pattern in roots:
        if pattern.endswith("/**"):
            if path == pattern[:-3] or path.startswith(pattern[:-2]):
                return True
        elif pattern.startswith("**/"):
            if Path(path).name == pattern[3:] or path.endswith(
                    "/" + pattern[3:]):
                return True
        elif path == pattern or Path(path).match(pattern):
            return True
    return False


def mentions(text: str, by_name: dict[str, set[str]],
             by_stem: dict[str, set[str]]) -> set[str]:
    """Every tracked path this text names, by path fragment or by [[wikilink]]."""
    found: set[str] = set()
    for raw in TOKEN.findall(text):
        norm = raw.replace("\\", "/").lstrip("./")
        # Match on the longest suffix that identifies exactly one tracked file.
        parts = norm.split("/")
        for start in range(len(parts)):
            key = "/".join(parts[start:])
            targets = by_name.get(key)
            if targets:
                found |= targets
                break
    for name in WIKILINK.findall(text):
        found |= by_stem.get(name.strip().lower(), set())
    return found


def build(files: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Index tracked files by every path suffix, and memory files by stem."""
    by_name: dict[str, set[str]] = {}
    by_stem: dict[str, set[str]] = {}
    for path in files:
        parts = path.split("/")
        for start in range(len(parts)):
            by_name.setdefault("/".join(parts[start:]), set()).add(path)
        stem = Path(path).stem.lower()
        by_stem.setdefault(stem, set()).add(path)
    # A suffix that identifies several files is ambiguous and must not be
    # allowed to mark all of them reached: that is how a scan launders litter
    # into "someone points at it".
    return ({k: v for k, v in by_name.items() if len(v) == 1},
            {k: v for k, v in by_stem.items() if len(v) == 1})


def read(path: str) -> str:
    try:
        return (REPO / path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


def analyse(roots: list[str]) -> dict[str, object]:
    files = tracked_files()
    by_name, by_stem = build(files)
    edges: dict[str, set[str]] = {}
    for path in files:
        if path.endswith((".png", ".jpg", ".svg", ".ico")):
            edges[path] = set()
            continue
        edges[path] = mentions(read(path), by_name, by_stem) - {path}

    seeds = {p for p in files if is_root(p, roots)}
    reached: set[str] = set()
    frontier = list(seeds)
    while frontier:
        current = frontier.pop()
        if current in reached:
            continue
        reached.add(current)
        frontier.extend(edges.get(current, ()) - reached)

    unreached = []
    subtree = []
    archived = []
    for path in sorted(files):
        if path in reached:
            continue
        reason = in_archive(path)
        if reason:
            archived.append((path, reason))
        elif any(path in targets for targets in edges.values()):
            subtree.append(path)
        else:
            unreached.append(path)
    return {"files": files, "reached": reached, "unreached": unreached,
            "subtree": subtree, "archived": archived, "edges": edges,
            "seeds": seeds}


def size_of(path: str) -> int:
    try:
        return (REPO / path).stat().st_size
    except OSError:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--roots", action="store_true")
    parser.add_argument("--why", metavar="PATH")
    parser.add_argument("--min-bytes", type=int, default=0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.roots:
        for pattern in ROOTS:
            print(f"  {pattern}")
        print("\nArchive directories, reported separately, never as litter:")
        for prefix, reason in ARCHIVE.items():
            print(f"  {prefix:24} {reason}")
        return 0

    try:
        result = analyse(ROOTS)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"CANNOT ANSWER: {exc}", file=sys.stderr)
        return 3

    if args.why:
        target = args.why.replace("\\", "/").lstrip("./")
        pointers = [src for src, dsts in result["edges"].items()
                    if target in dsts]
        if target in result["seeds"]:
            print(f"{target} is an ENTRY POINT (matches a pattern in ROOTS)")
        if not pointers:
            print(f"nothing points at {target}")
            return 1
        for src in sorted(pointers):
            mark = "reached" if src in result["reached"] else "UNREACHED ITSELF"
            print(f"  {src}   [{mark}]")
        return 0

    unreached = [p for p in result["unreached"]
                 if size_of(p) >= args.min_bytes]
    subtree = [p for p in result["subtree"] if size_of(p) >= args.min_bytes]

    if args.summary:
        per_dir: dict[str, list[int]] = {}
        for path in unreached + subtree:
            key = str(Path(path).parent)
            per_dir.setdefault(key, []).append(size_of(path))
        for key in sorted(per_dir, key=lambda k: -sum(per_dir[k])):
            sizes = per_dir[key]
            print(f"{sum(sizes)//1024:6} KB  {len(sizes):4} file(s)  {key}")
    else:
        for label, group in (("UNREACHED", unreached),
                             ("ORPHAN-SUBTREE", subtree)):
            if not group:
                continue
            print(f"\n== {label} ==")
            for path in sorted(group, key=lambda p: -size_of(p)):
                print(f"{size_of(path)//1024:5} KB  {path}")

    total = sum(size_of(p) for p in unreached + subtree)
    print(f"\ntracked={len(result['files'])} "
          f"reached={len(result['reached'])} "
          f"unreached={len(unreached)} "
          f"orphan-subtree={len(subtree)} "
          f"archived={len(result['archived'])} "
          f"({total//1024} KB not pointed at)")
    return 1 if (unreached or subtree) else 0


def _self_test() -> int:
    import shutil
    import tempfile
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(f"{'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    global REPO
    saved = REPO
    root = Path(tempfile.mkdtemp(prefix="reachable-selftest-"))
    try:
        REPO = root
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        (root / "CLAUDE.md").write_text(
            "see docs/live.md and [[a-memory]]\n", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "memory").mkdir()
        (root / "docs" / "live.md").write_text(
            "points onward at docs/second.md\n", encoding="utf-8")
        (root / "docs" / "second.md").write_text("leaf\n", encoding="utf-8")
        (root / "docs" / "orphan.md").write_text(
            "nobody names me, and I name docs/orphan-child.md\n",
            encoding="utf-8")
        (root / "docs" / "orphan-child.md").write_text("child\n",
                                                       encoding="utf-8")
        (root / "memory" / "a-memory.md").write_text("wiki target\n",
                                                     encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

        result = analyse(ROOTS)
        reached = result["reached"]
        check("CLAUDE.md" in reached, "an entry point is reached")
        check("docs/live.md" in reached, "a file named by an entry point is reached")
        check("docs/second.md" in reached,
              "reachability is TRANSITIVE, two hops from the root")
        check("memory/a-memory.md" in reached,
              "a [[wikilink]] counts as a pointer")
        check("docs/orphan.md" in result["unreached"],
              "a file nobody names is UNREACHED")
        check("docs/orphan-child.md" in result["subtree"],
              "a file named only by an orphan is ORPHAN-SUBTREE, never reached")
        check("docs/orphan-child.md" not in reached,
              "an orphan cannot launder its child into reached")

        # A shorter extension must never swallow a longer one. `json` before
        # `jsonl` in the alternation made a real 23 KB file read as litter.
        found = TOKEN.findall("see reference/sky/knowledge.jsonl here")
        check(found == ["reference/sky/knowledge.jsonl"],
              f"a .jsonl path is matched whole, not truncated to .json "
              f"(got {found})")
        check(TOKEN.findall("a.yaml b.jsonc c.toon") ==
              ["a.yaml", "b.jsonc", "c.toon"],
              "every multi-character extension matches whole")

        rc = main(["--summary"])
        check(rc == 1, "a repo with unreached files exits 1")

        # Remove the orphans; the same repo must then come back clean, which is
        # the assertion that proves the finding was real and not a constant.
        (root / "docs" / "orphan.md").unlink()
        (root / "docs" / "orphan-child.md").unlink()
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        check(main(["--summary"]) == 0,
              "with the orphans gone the same repo exits 0")
    finally:
        REPO = saved
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
