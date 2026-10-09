#!/usr/bin/env python3
"""Make ~/.claude/rules hold exactly the always-loaded rule files, as links.

    python tools/claude-rules-link.py              check: exit 0 right, 1 wrong
    python tools/claude-rules-link.py --apply      make it right
    python tools/claude-rules-link.py --self-test

WHY (2026-10-09): Claude Code loads EVERY .md under ~/.claude/rules into every
session and every subagent. The installer linked the whole rules/ folder there
so CLAUDE.md's `@rules/...` imports would resolve, and that also loaded the
twelve read-on-demand files, about 60k tokens a session that the byte-budget
lint never counted. The always-loaded set is CLAUDE.md's own `@rules/`
imports, read from the file, never a list kept here.

Exit: 0 right (or made right), 1 wrong or a real file in the way, 2 no
CLAUDE.md to read the imports from.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMPORT_RE = re.compile(r"^\s*@(rules/\S+\.md)\s*$")


def wanted(repo: Path) -> list[str] | None:
    """The `@rules/...` imports in CLAUDE.md, repo-relative. None: no CLAUDE.md."""
    claude = repo / "CLAUDE.md"
    if not claude.is_file():
        return None
    out = []
    for line in claude.read_text(encoding="utf-8").splitlines():
        m = IMPORT_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def _rel_md(target: Path) -> list[str]:
    """Every .md under a real target dir, relative, forward slashes."""
    return sorted(p.relative_to(target).as_posix() for p in target.rglob("*.md"))


def problems(repo: Path, target: Path, want: list[str],
             check_targets: bool = True) -> list[str]:
    """What is wrong with `target`, in plain words. Empty means right.
    `check_targets=False` skips where each link points, for a caller (the lint
    in a lane worktree) whose repo is not the checkout the links point into."""
    rules = "rules/"
    if target.is_symlink():
        return [f"{target} is ONE link to a whole folder, so every file in it loads"]
    if not target.is_dir():
        return [f"{target} does not exist, so CLAUDE.md's imports cannot resolve"]
    found = _rel_md(target)
    need = [w[len(rules):] for w in want]
    out = []
    for rel in found:
        if rel not in need:
            out.append(f"{rel} is auto-loaded but CLAUDE.md does not import it")
    for rel in need:
        link = target / rel
        src = repo / rules / rel
        if not link.is_symlink():
            out.append(f"{rel} is missing or is not a link")
        elif check_targets and Path(os.path.realpath(link)) != Path(os.path.realpath(src)):
            out.append(f"{rel} links somewhere other than {src}")
    return out


def _remove_link(path: Path) -> None:
    """Remove a link and never what it points at. A Windows directory link
    needs rmdir; a file link, or any link on POSIX, needs unlink."""
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def apply(repo: Path, target: Path, want: list[str]) -> list[str]:
    """Make `target` right. Returns what it could not fix (a real file)."""
    rules = "rules/"
    if target.is_symlink():
        _remove_link(target)
    target.mkdir(parents=True, exist_ok=True)
    need = [w[len(rules):] for w in want]
    stuck = []
    for rel in _rel_md(target):
        if rel in need:
            continue
        p = target / rel
        if p.is_symlink():
            _remove_link(p)
        else:
            stuck.append(f"{p} is a real file, not a link; moved nothing, delete it by hand")
    for rel in need:
        link, src = target / rel, repo / rules / rel
        if link.is_symlink():
            if Path(os.path.realpath(link)) == Path(os.path.realpath(src)):
                continue
            _remove_link(link)
        elif link.exists():
            stuck.append(f"{link} is a real file where a link belongs")
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(src, link)
    return stuck


def run(repo: Path, target: Path, do_apply: bool) -> int:
    want = wanted(repo)
    if want is None:
        print(f"claude-rules-link: no CLAUDE.md in {repo}, so no import list to follow")
        return 2
    if do_apply:
        stuck = apply(repo, target, want)
        for s in stuck:
            print(f"claude-rules-link: {s}")
    left = problems(repo, target, want)
    for p in left:
        print(f"claude-rules-link: WRONG: {p}")
    if left:
        if not do_apply:
            print("claude-rules-link: fix with: python tools/claude-rules-link.py --apply")
        return 1
    print(f"claude-rules-link: {target} holds exactly the {len(want)} imported "
          f"rule file(s), as links")
    return 0


def self_test() -> int:
    total = passed = 0

    def want_(label: str, cond: bool) -> None:
        nonlocal total, passed
        total += 1
        if cond:
            passed += 1
        else:
            print(f"self-test FAIL: {label}")

    def repo_at(td: Path) -> Path:
        repo = td / "repo"
        for rel in ("shared/a.md", "shared/b.md", "shared/lanes.md", "claude/jobs.md"):
            (repo / "rules" / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / "rules" / rel).write_text("x", encoding="utf-8")
        (repo / "CLAUDE.md").write_text(
            "# g\n@rules/shared/a.md\n@rules/shared/b.md\nsee `rules/claude/jobs.md`\n",
            encoding="utf-8")
        return repo

    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        repo = repo_at(td)
        target = td / "claude" / "rules"
        target.parent.mkdir()
        try:
            os.symlink(repo / "rules", target, target_is_directory=True)
        except OSError as exc:
            print(f"self-test: CANNOT CREATE LINKS here ({exc}), so nothing was tested")
            return 1
        want_("imports are read from CLAUDE.md, prose mentions are not",
              wanted(repo) == ["rules/shared/a.md", "rules/shared/b.md"])
        want_("a link to the whole folder is wrong",
              run(repo, target, False) == 1)
        want_("apply makes it right", run(repo, target, True) == 0)
        want_("only the two imports are left",
              _rel_md(target) == ["shared/a.md", "shared/b.md"])
        want_("the repo's own files survive removing the folder link",
              (repo / "rules" / "claude" / "jobs.md").is_file()
              and (repo / "rules" / "shared" / "lanes.md").is_file())
        want_("each one links to the repo copy",
              Path(os.path.realpath(target / "shared" / "a.md"))
              == Path(os.path.realpath(repo / "rules" / "shared" / "a.md")))
        want_("a second apply changes nothing", run(repo, target, True) == 0)
        (repo / "CLAUDE.md").write_text("@rules/shared/a.md\n", encoding="utf-8")
        want_("an import removed from CLAUDE.md makes its link wrong",
              run(repo, target, False) == 1)
        want_("and apply removes that link", run(repo, target, True) == 0
              and _rel_md(target) == ["shared/a.md"])
        (target / "shared" / "stray.md").write_text("real", encoding="utf-8")
        want_("a real file in the way is refused and kept",
              run(repo, target, True) == 1 and (target / "shared" / "stray.md").is_file())

    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        want_("no CLAUDE.md is exit 2", run(td, td / "rules", False) == 2)
        repo = repo_at(td)
        want_("a missing target is wrong, and apply creates it",
              run(repo, td / "none" / "rules", False) == 1
              and run(repo, td / "none" / "rules", True) == 0)

    print(f"claude-rules-link self-test: {passed}/{total} checks passed")
    return 0 if passed == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="claude-rules-link")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    return run(REPO, Path(a.claude_dir) / "rules", a.apply)


if __name__ == "__main__":
    sys.exit(main())
