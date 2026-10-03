#!/usr/bin/env python3
"""Create, style and drop a job's scratch worktree, with no hand-run step.

    python tools/scratch.py new <job> --repo <checkout> [--base main]
    python tools/scratch.py css <job>     build the stylesheet from ITS OWN templates
    python tools/scratch.py drop <job>    remove the worktree, its branch and its folder
    python tools/scratch.py --self-test

WHY (A3, 2026-10-03). The generated-order flow builds every change in a scratch
worktree, one folder per job under Documents/ai/scratch, cleaned the moment the
job lands (rules/claude/session-dirs.md). One evening ran that lifecycle by
hand five times: `git worktree add`, a stylesheet that is a build artifact and
so missing from every fresh worktree (a browser check then shows an unstyled
page), then worktree remove, branch delete and folder delete. The guard refused
two of the hand spellings along the way.

THE HAZARD THIS REMOVES. The stylesheet build needs node_modules, which only the
real checkout has, so the worktree borrows it through a directory link. A delete
that walks INTO that link empties the real checkout's node_modules. Measured
2026-10-03: `git worktree remove --force` and shutil.rmtree do NOT walk into a
junction on the PC; a hand `Remove-Item -Recurse` in Windows PowerShell 5.1 is
reported to, UNVERIFIED and not worth testing on the real folder. So `css`
removes its link the moment the build ends, `drop` unlinks any directory link at
the worktree's top level before anything recursive runs, and no scratch folder
is ever deleted by hand.

A branch holding commits found on no other branch is UNIQUE WORK and is kept,
never deleted (rules/claude/git-github.md: deleting unique work is the owner's).

Exit: 0 done, 1 a step failed, 2 refused with nothing changed.
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

ROOT = Path(__file__).resolve().parents[2] / "scratch"
JOB = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _where(root: Path, job: str) -> tuple[Path, Path]:
    """(the job's folder, its worktree)."""
    return root / job, root / job / "wt"


def is_dir_link(path: Path) -> bool:
    """A symlink or, on Windows, a junction: a door to somewhere else."""
    isjunction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(isjunction and isjunction(path))


def unlink_dir_link(path: Path) -> None:
    """Remove the link itself; whatever it points at is never touched."""
    if os.name == "nt":
        os.rmdir(path)      # RemoveDirectoryW drops a junction, not its target
    else:
        path.unlink()


def unlink_links(folder: Path) -> list[str]:
    """Unlink every directory link at the top of `folder`, never walking into
    one. The names removed, so a caller can say what it did."""
    gone = []
    for entry in folder.iterdir():
        if is_dir_link(entry):
            unlink_dir_link(entry)
            gone.append(entry.name)
    return gone


def make_dir_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def repo_of(wt: Path) -> Path | None:
    """The real checkout a worktree belongs to, asked of git, never guessed."""
    r = _git("-C", str(wt), "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(r.stdout.strip()).parent if r.returncode == 0 and r.stdout.strip() else None


def _refuse(why: str) -> int:
    print(f"scratch: REFUSED, {why}")
    return 2


def new(job: str, repo: Path, base: str = "main", root: Path = ROOT) -> int:
    if not JOB.match(job):
        return _refuse(f"a job is named in lowercase words and dashes, not {job!r}")
    folder, wt = _where(root, job)
    if wt.exists():
        return _refuse(f"{wt} already exists; drop it first or pick another name")
    folder.mkdir(parents=True, exist_ok=True)
    r = _git("-C", str(repo), "worktree", "add", "-b", f"scratch/{job}", str(wt), base)
    if r.returncode != 0:
        print(f"scratch: git worktree add failed: {r.stderr.strip()}")
        return 1
    print(f"scratch: {wt} on branch scratch/{job}, cut from {base}")
    return 0


def css(job: str, root: Path = ROOT, build: list[str] | None = None) -> int:
    """Build the stylesheet inside the worktree from its own templates, borrowing
    the real checkout's node_modules only for as long as the build runs."""
    _, wt = _where(root, job)
    if not wt.is_dir():
        return _refuse(f"no worktree at {wt}")
    repo = repo_of(wt)
    if repo is None:
        return _refuse(f"{wt} is not a git worktree")
    modules = repo / "node_modules"
    if not modules.is_dir():
        return _refuse(f"{repo} has no node_modules to borrow; install there first")
    link = wt / "node_modules"
    if link.exists() and not is_dir_link(link):
        return _refuse(f"{link} is a real folder; build there directly")
    if build is None:
        npm = shutil.which("npm")
        if npm is None:
            return _refuse("npm is not on the PATH")
        build = [npm, "--prefix", str(wt), "run", "build:css"]
    made = False
    try:
        if not link.exists():
            make_dir_link(link, modules)
            made = True
        r = subprocess.run(build, cwd=str(wt))
    finally:
        if made and is_dir_link(link):
            unlink_dir_link(link)
    if r.returncode != 0:
        print(f"scratch: the stylesheet build exited {r.returncode}")
        return 1
    print(f"scratch: stylesheet built in {wt} from its own templates; the borrowed "
          "node_modules link is already gone")
    return 0


def _unique_commits(repo: Path, branch: str) -> int | None:
    """How many commits on `branch` are on no other branch or remote."""
    r = _git("-C", str(repo), "rev-list", "--count", branch, "--not",
             f"--exclude={branch}", "--branches", "--remotes")
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def drop(job: str, root: Path = ROOT) -> int:
    if not JOB.match(job):
        return _refuse(f"a job is named in lowercase words and dashes, not {job!r}")
    folder, wt = _where(root, job)
    if not folder.exists():
        print(f"scratch: nothing to drop, {folder} does not exist")
        return 0
    # 1. LINKS FIRST, unlinked and never walked: the hazard this tool exists for.
    if wt.is_dir():
        for name in unlink_links(wt):
            print(f"scratch: removed the link {name} (its target is untouched)")
    # 2. The worktree, through git, so the repo's worktree list stays clean.
    kept = ""
    repo = repo_of(wt) if wt.is_dir() else None
    if repo is not None:
        r = _git("-C", str(repo), "worktree", "remove", "--force", str(wt))
        if r.returncode != 0:
            print(f"scratch: git worktree remove failed: {r.stderr.strip()}")
            return 1
        branch = f"scratch/{job}"
        unique = _unique_commits(repo, branch)
        if unique == 0:
            _git("-C", str(repo), "branch", "-D", branch)
        elif unique is None:
            kept = f"; branch {branch} kept, git could not say whether it holds unique work"
        else:
            kept = (f"; branch {branch} KEPT, it holds {unique} commit(s) found on no "
                    "other branch, and deleting unique work is the owner's call")
    # 3. The folder. No link is left at the worktree's top level, and rmtree
    #    removes a nested link without following it.
    shutil.rmtree(folder)
    print(f"scratch: dropped {job}{kept}")
    return 0


def self_test() -> int:
    checks = [0, 0]

    def want(label: str, condition: bool) -> None:
        checks[0] += 1
        if condition:
            checks[1] += 1
            print("  ok    %s" % label)
        else:
            print("  FAIL  %s" % label)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo, root = base / "repo", base / "scratch"
        repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "scratch-self-test"),
                     ("config", "user.name", "t"), ("config", "commit.gpgsign", "false")):
            _git("-C", str(repo), *args)
        (repo / "a.txt").write_text("a\n")
        _git("-C", str(repo), "add", "a.txt")
        _git("-C", str(repo), "commit", "-q", "-m", "a")
        modules = repo / "node_modules"
        modules.mkdir()
        sentinel = modules / "keep.js"
        sentinel.write_text("// the real checkout's packages\n")

        want("a job is cut as a worktree on its own branch",
             new("demo", repo, root=root) == 0 and (root / "demo" / "wt" / "a.txt").is_file()
             and _git("-C", str(repo), "rev-parse", "--verify", "scratch/demo").returncode == 0)
        want("cutting the same job twice is refused", new("demo", repo, root=root) == 2)
        want("a name that could escape the scratch root is refused",
             new("../evil", repo, root=root) == 2)

        wt = root / "demo" / "wt"
        build = [sys.executable, "-c",
                 "import pathlib; assert pathlib.Path('node_modules/keep.js').is_file(); "
                 "pathlib.Path('built.css').write_text('x')"]
        want("css builds INSIDE the worktree while the packages are borrowed",
             css("demo", root=root, build=build) == 0 and (wt / "built.css").is_file())
        want("and the borrowed link is gone the moment the build ends",
             not (wt / "node_modules").exists())
        want("and the real packages are untouched", sentinel.is_file())
        failing = [sys.executable, "-c", "raise SystemExit(5)"]
        want("a failed build reports failure and still removes the link",
             css("demo", root=root, build=failing) == 1 and not (wt / "node_modules").exists())

        # THE UNLINK STEP, pinned on its own. Measured 2026-10-03: neither `git
        # worktree remove --force` nor shutil.rmtree follows a junction on the
        # PC, so disabling this step left every drop assertion below green. The
        # step is what makes a hand `Remove-Item -Recurse`, which can follow
        # one, unnecessary; these two assertions go red without it.
        make_dir_link(wt / "node_modules", modules)
        want("unlink_links removes a directory link and names it",
             unlink_links(wt) == ["node_modules"] and not (wt / "node_modules").exists())
        want("and leaves the folder it pointed at whole", sentinel.is_file())

        # THE HAZARD: a link left in the worktree when it is dropped.
        make_dir_link(wt / "node_modules", modules)
        want("drop succeeds with a link still inside the worktree", drop("demo", root=root) == 0)
        want("and the real checkout's packages survive the drop (the hazard)",
             sentinel.is_file())
        want("the worktree, its branch and its folder are all gone",
             not (root / "demo").exists()
             and _git("-C", str(repo), "rev-parse", "--verify", "scratch/demo").returncode != 0
             and str(wt) not in _git("-C", str(repo), "worktree", "list").stdout)

        new("kept", repo, root=root)
        kept_wt = root / "kept" / "wt"
        (kept_wt / "b.txt").write_text("b\n")
        _git("-C", str(kept_wt), "add", "b.txt")
        _git("-C", str(kept_wt), "commit", "-q", "-m", "b")
        want("a branch holding a commit found nowhere else is KEPT on drop",
             drop("kept", root=root) == 0
             and _git("-C", str(repo), "rev-parse", "--verify", "scratch/kept").returncode == 0)
        want("dropping a job that is not there is a no-op, not a failure",
             drop("ghost", root=root) == 0)

        new("own", repo, root=root)
        (root / "own" / "wt" / "node_modules").mkdir()
        want("css refuses a worktree with its own real node_modules",
             css("own", root=root, build=build) == 2)
        drop("own", root=root)

    print("\nscratch self-test: %d/%d checks passed" % (checks[1], checks[0]))
    return 0 if checks[1] == checks[0] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scratch")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("verb", nargs="?", choices=("new", "css", "drop"))
    ap.add_argument("job", nargs="?")
    ap.add_argument("--repo", help="the real checkout a NEW worktree is cut from")
    ap.add_argument("--base", default="main")
    ap.add_argument("--root", default=str(ROOT), help="the scratch root")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.verb or not a.job:
        ap.error("say new, css or drop, then the job's name")
    root = Path(a.root)
    if a.verb == "new":
        if not a.repo:
            ap.error("new needs --repo, the real checkout to cut from")
        return new(a.job, Path(a.repo), a.base, root)
    if a.verb == "css":
        return css(a.job, root)
    return drop(a.job, root)


if __name__ == "__main__":
    raise SystemExit(main())
