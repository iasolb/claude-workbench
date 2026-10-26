#!/usr/bin/env python3
"""Create, style, and safely remove a job's scratch worktree.

    python tools/scratch.py new <job> --repo <checkout> [--base main]
    python tools/scratch.py css <job>
    python tools/scratch.py drop <job>
    python tools/scratch.py --self-test

Scratch directories default to ~/.local/share/claude-workbench/scratch and can
be changed with SCRATCH_ROOT. The default base branch is main; set
WORKBENCH_DEFAULT_BRANCH or pass --base to use another branch.

(2026-10-03: repeated scratch setup motivated automating the lifecycle.)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("SCRATCH_ROOT") or
            (Path.home() / ".local" / "share" / "claude-workbench" / "scratch")).expanduser()
JOB = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def paths(root: Path, job: str) -> tuple[Path, Path]:
    folder = root / job
    return folder, folder / "wt"


def is_dir_link(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(isjunction and isjunction(path))


def unlink_dir_link(path: Path) -> None:
    if os.name == "nt":
        os.rmdir(path)
    else:
        path.unlink()


def unlink_links(folder: Path) -> list[str]:
    removed = []
    for entry in folder.iterdir():
        if is_dir_link(entry):
            unlink_dir_link(entry)
            removed.append(entry.name)
    return removed


def make_dir_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def repo_of(worktree: Path) -> Path | None:
    result = git("-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(result.stdout.strip()).parent if result.returncode == 0 and result.stdout.strip() else None


def refuse(message: str) -> int:
    print(f"scratch: REFUSED, {message}")
    return 2


def new(job: str, repo: Path, base: str = "main", root: Path = ROOT) -> int:
    if not JOB.fullmatch(job):
        return refuse("use a lowercase job name containing letters, digits, and dashes")
    _folder, worktree = paths(root, job)
    if worktree.exists():
        return refuse(f"{worktree} already exists; drop it first or pick another job name")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = git("-C", str(repo), "worktree", "add", "-b", f"scratch/{job}", str(worktree), base)
    if result.returncode:
        print(f"scratch: git worktree add failed: {result.stderr.strip()}")
        return 1
    print(f"scratch: {worktree} on branch scratch/{job}, cut from {base}")
    return 0


def needs_css(worktree: Path) -> bool:
    package = worktree / "package.json"
    return package.is_file() and '"build:css"' in package.read_text(encoding="utf-8")


def css(job: str, root: Path = ROOT, build: list[str] | None = None) -> int:
    """Build CSS in the worktree, borrowing dependencies only during the build."""
    _folder, worktree = paths(root, job)
    if not worktree.is_dir():
        return refuse(f"no worktree at {worktree}")
    repo = repo_of(worktree)
    if repo is None:
        return refuse(f"{worktree} is not a git worktree")
    modules = repo / "node_modules"
    if not modules.is_dir():
        return refuse(f"{repo} has no node_modules to borrow")
    link = worktree / "node_modules"
    if link.exists() and not is_dir_link(link):
        return refuse(f"{link} is a real directory; build there directly")
    if build is None:
        npm = shutil.which("npm")
        if npm is None:
            return refuse("npm is not on PATH")
        build = [npm, "--prefix", str(worktree), "run", "build:css"]
    made = False
    try:
        if not link.exists():
            make_dir_link(link, modules)
            made = True
        result = subprocess.run(build, cwd=worktree)
    finally:
        if made and is_dir_link(link):
            unlink_dir_link(link)
    if result.returncode:
        print(f"scratch: stylesheet build exited {result.returncode}")
        return 1
    print(f"scratch: stylesheet built in {worktree}; borrowed node_modules link removed")
    return 0


def unique_commits(repo: Path, branch: str) -> int | None:
    result = git("-C", str(repo), "rev-list", "--count", branch, "--not",
                 f"--exclude={branch}", "--branches", "--remotes")
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdigit() else None


def drop(job: str, root: Path = ROOT) -> int:
    if not JOB.fullmatch(job):
        return refuse("use a lowercase job name containing letters, digits, and dashes")
    folder, worktree = paths(root, job)
    if not folder.exists():
        print(f"scratch: nothing to drop, {folder} does not exist")
        return 0
    if worktree.is_dir():
        for name in unlink_links(worktree):
            print(f"scratch: removed link {name}; its target is untouched")
    repo = repo_of(worktree) if worktree.is_dir() else None
    kept = ""
    if repo is not None:
        result = git("-C", str(repo), "worktree", "remove", "--force", str(worktree))
        if result.returncode:
            print(f"scratch: git worktree remove failed: {result.stderr.strip()}")
            return 1
        branch = f"scratch/{job}"
        unique = unique_commits(repo, branch)
        if unique == 0:
            git("-C", str(repo), "branch", "-D", branch)
        elif unique is None:
            kept = f"; branch {branch} kept because git could not check for unique work"
        else:
            kept = f"; branch {branch} kept because it has {unique} unique commit(s)"
    shutil.rmtree(folder)
    print(f"scratch: dropped {job}{kept}")
    return 0


def self_test() -> int:
    checks = passed = 0

    def want(label: str, condition: bool) -> None:
        nonlocal checks, passed
        checks += 1
        if condition:
            passed += 1
            print(f"  ok    {label}")
        else:
            print(f"  FAIL  {label}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo, scratch_root = root / "repo", root / "scratch"
        repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "scratch-self-test"),
                     ("config", "user.name", "Test User"), ("config", "commit.gpgsign", "false")):
            git("-C", str(repo), *args)
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        git("-C", str(repo), "add", "base.txt")
        git("-C", str(repo), "commit", "-q", "-m", "base")
        modules = repo / "node_modules"
        modules.mkdir()
        sentinel = modules / "keep.js"
        sentinel.write_text("packages\n", encoding="utf-8")
        want("a scratch worktree gets its own branch",
             new("demo", repo, root=scratch_root) == 0 and
             (scratch_root / "demo" / "wt" / "base.txt").is_file())
        want("a repeated worktree is refused", new("demo", repo, root=scratch_root) == 2)
        want("a traversal name is refused", new("../bad", repo, root=scratch_root) == 2)
        worktree = scratch_root / "demo" / "wt"
        build = [sys.executable, "-c", "import pathlib; assert pathlib.Path('node_modules/keep.js').is_file(); pathlib.Path('built.css').write_text('x')"]
        want("CSS builds while dependencies are temporarily linked",
             css("demo", root=scratch_root, build=build) == 0 and (worktree / "built.css").is_file())
        want("the borrowed link is removed and original dependencies survive",
             not (worktree / "node_modules").exists() and sentinel.is_file())
        failed_build = [sys.executable, "-c", "raise SystemExit(5)"]
        want("a failed build is reported and removes the borrowed link",
             css("demo", root=scratch_root, build=failed_build) == 1 and not (worktree / "node_modules").exists())
        make_dir_link(worktree / "node_modules", modules)
        want("a directory link is unlinked without changing its target",
             unlink_links(worktree) == ["node_modules"] and sentinel.is_file())
        make_dir_link(worktree / "node_modules", modules)
        want("drop removes the worktree without following its link",
             drop("demo", root=scratch_root) == 0 and sentinel.is_file() and not (scratch_root / "demo").exists())
        want("dropping a missing job is a no-op", drop("ghost", root=scratch_root) == 0)
        want("a checkout without a CSS build script needs no build", not needs_css(root))
        (root / "package.json").write_text('{"scripts":{"build:css":"tool"}}', encoding="utf-8")
        want("a CSS build script is detected", needs_css(root))
    print(f"scratch self-test: {passed}/{checks} checks passed")
    return 0 if passed == checks else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scratch")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("verb", nargs="?", choices=("new", "css", "drop"))
    parser.add_argument("job", nargs="?")
    parser.add_argument("--repo")
    parser.add_argument("--base")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.verb or not args.job:
        parser.error("say new, css, or drop, then the job name")
    root = Path(args.root).expanduser()
    if args.verb == "new":
        if not args.repo:
            parser.error("new needs --repo, the checkout for the worktree")
        base = args.base or os.environ.get("WORKBENCH_DEFAULT_BRANCH", "main")
        result = new(args.job, Path(args.repo).expanduser(), base, root)
        if result == 0 and needs_css(paths(root, args.job)[1]):
            return css(args.job, root)
        return result
    if args.verb == "css":
        return css(args.job, root)
    return drop(args.job, root)


if __name__ == "__main__":
    sys.exit(main())
