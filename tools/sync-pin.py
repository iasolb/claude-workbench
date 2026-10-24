#!/usr/bin/env python3
"""sync-pin.py: sync a repository branch and bump its superproject pin.

    python3 tools/sync-pin.py                 sync the branch, then bump the pin
    python3 tools/sync-pin.py --dry-run       print the plan, touch nothing
    python3 tools/sync-pin.py --branch-only   sync the branch, leave the pin
    python3 tools/sync-pin.py --pin-only      bump the pin, skip the branch sync
    python3 tools/sync-pin.py --self-test

WHY THIS EXISTS (2026-09-01): keep a submodule pointer deliberate, and make
updating it repeatable through one command.

The default repository path comes from REPO_PATH in
~/.claude/workbench.conf. Set WORKBENCH_CONF to use a different config file.
Optional sibling branches are configured as a comma-separated list in
SYNC_PIN_SIBLING_BRANCHES; when unset, no sibling branches are published.

A submodule records ONE COMMIT. This command reconciles the repository before
updating that recorded pointer, so the superproject never records an unpushed
commit. Pin updates are an explicit, repeatable operation rather than a manual
step.

WHAT IT DOES NOT DO, on purpose: it does not re-implement branch reconciliation.
`tools/sync-branch.py` already classifies equal/behind/ahead/diverged and
handles each state. This delegates to it for the repository and its superproject.

Exit codes, so a caller can tell states apart without reading prose:
    0  in sync (pin already correct, or moved and pushed)
    2  bad arguments or a path that is not what it claims to be
    3  the branch sync failed, so the pin was deliberately NOT touched
    4  the pin bump failed (commit or push)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

CONF = Path(os.environ.get("WORKBENCH_CONF") or
            (Path.home() / ".claude" / "workbench.conf"))


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def conf_get(key: str) -> str | None:
    """Read a key from the configured workbench.conf file."""
    if not CONF.exists():
        return None
    for line in CONF.read_text().splitlines():
        line = line.strip().replace("\r", "")
        if line.startswith(f"{key}="):
            return line[len(key) + 1:]
    return None


def resolve_bank(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    from_conf = conf_get("REPO_PATH")
    if not from_conf:
        print("No REPO_PATH in the configured workbench.conf and no --repo given.",
              file=sys.stderr)
        sys.exit(2)
    return Path(from_conf).resolve()


def resolve_parent(bank: Path) -> tuple[Path, str]:
    """Find the superproject and the bank's path within it.

    Asked of git rather than guessed from the directory layout: a submodule can
    sit anywhere, and `rev-parse --show-superproject-working-tree` is the only
    answer that stays true if the tree is rearranged again.
    """
    # Resolve BOTH sides. git reports real paths, so on macOS, where /var and
    # /tmp are symlinks into /private, an unresolved caller path fails the
    # relative_to below even though the submodule genuinely is inside.
    bank = bank.resolve()
    r = run(["git", "-C", str(bank), "rev-parse", "--show-superproject-working-tree"])
    root = r.stdout.strip()
    if r.returncode != 0 or not root:
        print(f"{bank} is not a submodule of anything, so there is no pin to move.",
              file=sys.stderr)
        print("That is a valid setup; this command simply has nothing to do.",
              file=sys.stderr)
        sys.exit(2)
    parent = Path(root).resolve()
    try:
        rel = bank.relative_to(parent).as_posix()
    except ValueError:
        print(f"{bank} is not inside its superproject {parent}.", file=sys.stderr)
        sys.exit(2)
    return parent, rel


def recorded_pin(parent: Path, rel: str) -> str | None:
    """The commit the PARENT currently records, which is not the same question
    as what is checked out. `submodule status` answers about the worktree."""
    r = run(["git", "-C", str(parent), "ls-tree", "HEAD", rel])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.split()
    return parts[2] if len(parts) >= 3 else None


def sync_branch(repo: Path, label: str, dry_run: bool) -> bool:
    tool = Path(__file__).resolve().parent / "sync-branch.py"
    if not tool.exists():
        print(f"Missing {tool}; cannot sync {label}.", file=sys.stderr)
        return False
    cmd = [sys.executable, str(tool), "--repo", str(repo)]
    if dry_run:
        cmd.append("--dry-run")
    print(f"--- sync-branch: {label}")
    r = run(cmd)
    out = (r.stdout + r.stderr).strip()
    if out:
        print("\n".join("    " + line for line in out.splitlines()))
    return r.returncode == 0


# Optional peer branches are configuration, never inferred from a host name.
SIBLINGS_ENV = "SYNC_PIN_SIBLING_BRANCHES"


def publish_siblings(repo: Path, dry_run: bool) -> bool:
    """Fast-forward each configured sibling branch to this one.

    FAST-FORWARD ONLY. If the sibling has commits this branch does not, that is
    a real divergence and the answer is a merge someone looks at, never a push
    that discards it.
    """
    branch = run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"]
                 ).stdout.strip()
    targets = [b.strip() for b in os.environ.get(SIBLINGS_ENV, "").split(",")
               if b.strip()]
    if not targets:
        print(f"--- publish: branch {branch!r} has no declared sibling, "
              f"nothing to publish")
        return True

    ok = True
    for target in targets:
        print(f"--- publish: {branch} -> origin/{target}")
        run(["git", "-C", str(repo), "fetch", "origin", "--quiet"])
        remote = f"origin/{target}"
        exists = run(["git", "-C", str(repo), "rev-parse", "--verify",
                      "--quiet", remote]).returncode == 0
        if exists:
            ancestor = run(["git", "-C", str(repo), "merge-base",
                            "--is-ancestor", remote, "HEAD"]).returncode == 0
            if not ancestor:
                behind = run(["git", "-C", str(repo), "rev-list", "--count",
                              f"HEAD..{remote}"]).stdout.strip()
                print(f"    DIVERGED: {remote} has {behind} commit(s) this "
                      f"branch does not. NOT pushing. Merge {remote} into "
                      f"{branch} first, then re-run.", file=sys.stderr)
                ok = False
                continue
            same = run(["git", "-C", str(repo), "rev-list", "--count",
                        f"{remote}..HEAD"]).stdout.strip()
            if same == "0":
                print(f"    already identical, nothing to publish.")
                continue
            print(f"    fast-forwarding {remote} by {same} commit(s)")
        if dry_run:
            print("    (dry run) would push.")
            continue
        push = run(["git", "-C", str(repo), "push", "origin",
                    f"HEAD:{target}"])
        if push.returncode != 0:
            print(f"    push failed: {(push.stderr or '').strip()}",
                  file=sys.stderr)
            ok = False
        else:
            print(f"    pushed.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="repository to sync (default: REPO_PATH from workbench.conf)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    ap.add_argument("--branch-only", action="store_true", help="sync the branch, leave the pin")
    ap.add_argument("--pin-only", action="store_true", help="bump the pin, skip the branch sync")
    ap.add_argument("--no-publish", action="store_true",
                    help="skip publishing configured sibling branches")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if a.branch_only and a.pin_only:
        print("--branch-only and --pin-only are contradictory.", file=sys.stderr)
        return 2

    bank = resolve_bank(a.repo)
    if not (bank / ".git").exists():
        # .git is a FILE in a submodule. Testing for a directory is the bug that
        # silenced six hooks on this machine on 2026-09-01.
        print(f"Not a git repo: {bank}", file=sys.stderr)
        return 2

    if not a.pin_only:
        if not sync_branch(bank, "repository", a.dry_run):
            print("Branch sync failed. The pin was NOT touched: pinning a branch "
                  "that did not reconcile records a commit nobody else has.",
                  file=sys.stderr)
            return 3

    # Publish BEFORE the pin. The pin records a commit, and recording one a
    # configured sibling cannot fetch defeats the branch-sync guard above.
    if not a.no_publish and not publish_siblings(bank, a.dry_run):
        print("Publish failed or diverged. The pin was NOT touched.",
              file=sys.stderr)
        return 5

    if a.branch_only:
        return 0

    parent, rel = resolve_parent(bank)
    head = run(["git", "-C", str(bank), "rev-parse", "HEAD"]).stdout.strip()
    pinned = recorded_pin(parent, rel)

    print(f"--- pin: {parent.name}/{rel}")
    if pinned == head:
        print(f"    already at {head[:8]}, nothing to move.")
        return 0

    span = ""
    if pinned:
        c = run(["git", "-C", str(bank), "rev-list", "--count", f"{pinned}..{head}"])
        if c.returncode == 0 and c.stdout.strip():
            span = f", {c.stdout.strip()} commit(s) ahead of the pin"
    print(f"    {(pinned or 'none')[:8]} -> {head[:8]}{span}")

    if a.dry_run:
        print("    (dry run) would stage, commit and push the pointer.")
        return 0

    add = run(["git", "-C", str(parent), "add", "--", rel])
    if add.returncode != 0:
        print(f"    stage failed: {add.stderr.strip()}", file=sys.stderr)
        return 4

    msg = (f"Move the {Path(rel).name} pointer to {head[:8]}\n\n"
           f"Recorded by tools/sync-pin.py so the pin stops being a hand step. "
           f"The submodule pin is deliberate, and this command keeps it current.")
    # Commit by PATHSPEC. Sessions run concurrently here and a plain commit
    # takes whatever another session staged (CLAUDE.md, standing rule).
    com = run(["git", "-C", str(parent), "commit", "-m", msg, "--", rel])
    if com.returncode != 0:
        blob = com.stdout + com.stderr
        if "nothing to commit" in blob:
            print("    nothing staged; the pin already matched.")
            return 0
        print(f"    commit failed: {blob.strip()}", file=sys.stderr)
        return 4

    if not sync_branch(parent, parent.name, False):
        print("    committed, but the push did not land. Resolve in "
              f"{parent} and re-run.", file=sys.stderr)
        return 4
    print(f"    pinned and pushed.")
    return 0


def self_test() -> int:
    """Build a real superproject + submodule in a temp dir and prove the pin
    moves. Asserts against git's own output, never against this module's
    opinion of what it did (memory-integrity rule 2)."""
    import tempfile

    def git(*args: str, cwd: Path) -> str:
        r = run(["git", *args], cwd=cwd)
        if r.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
        return r.stdout.strip()

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sub_origin = root / "sub-origin"
        sub_origin.mkdir()
        # -b main matters: a bare repo whose HEAD names a branch that was never
        # pushed clones as an unborn branch, and `submodule add` then fails.
        git("init", "-q", "--bare", "-b", "main", ".", cwd=sub_origin)

        work = root / "subwork"
        work.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=work)
        git("config", "user.email", "sync-pin-test-user", cwd=work)
        git("config", "user.name", "t", cwd=work)
        (work / "a.txt").write_text("one\n")
        git("add", "-A", cwd=work)
        git("commit", "-qm", "one", cwd=work)
        git("remote", "add", "origin", str(sub_origin), cwd=work)
        git("push", "-q", "-u", "origin", "main", cwd=work)

        parent = root / "parent"
        parent.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=parent)
        git("config", "user.email", "sync-pin-test-user", cwd=parent)
        git("config", "user.name", "t", cwd=parent)
        # A submodule cannot be added onto an unborn branch.
        (parent / "README.md").write_text("parent\n")
        git("add", "-A", cwd=parent)
        git("commit", "-qm", "init", cwd=parent)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
            str(sub_origin), "sub", cwd=parent)
        git("commit", "-qm", "add sub", cwd=parent)

        sub = parent / "sub"
        git("checkout", "-q", "main", cwd=sub)
        git("config", "user.email", "sync-pin-test-user", cwd=sub)
        git("config", "user.name", "t", cwd=sub)
        (sub / "b.txt").write_text("two\n")
        git("add", "-A", cwd=sub)
        git("commit", "-qm", "two", cwd=sub)
        git("push", "-q", "origin", "main", cwd=sub)

        before = recorded_pin(parent, "sub")
        head = git("rev-parse", "HEAD", cwd=sub)
        if before == head:
            failures.append("setup wrong: pin already matched, nothing would be proven")

        # Superproject resolution must come from git, not from the layout.
        got_parent, rel = resolve_parent(sub)
        if got_parent != parent.resolve() or rel != "sub":
            failures.append(f"resolve_parent gave {got_parent}/{rel}, wanted {parent}/sub")

        # A dry run must leave the recorded pin untouched.
        r = run([sys.executable, str(Path(__file__).resolve()),
                 "--repo", str(sub), "--pin-only", "--dry-run"])
        if recorded_pin(parent, "sub") != before:
            failures.append("--dry-run moved the pin")
        if r.returncode != 0:
            failures.append(f"--dry-run exited {r.returncode}")

        # The real run must move it to exactly the submodule's HEAD.
        r = run([sys.executable, str(Path(__file__).resolve()),
                 "--repo", str(sub), "--pin-only"])
        after = recorded_pin(parent, "sub")
        if after != head:
            failures.append(f"pin is {after}, wanted {head} (exit {r.returncode}): "
                            f"{r.stdout}{r.stderr}")

        # Re-running must be a no-op, not a second empty commit.
        commits_before = git("rev-list", "--count", "HEAD", cwd=parent)
        run([sys.executable, str(Path(__file__).resolve()),
             "--repo", str(sub), "--pin-only"])
        if git("rev-list", "--count", "HEAD", cwd=parent) != commits_before:
            failures.append("re-run created a commit; it should be a no-op")

        # ---- publishing to a configured sibling branch ----
        # Both directions are proven: a fast-forward publishes, a divergence
        # REFUSES rather than discarding work from the sibling.
        git("branch", "-f", "peer", "HEAD~1", cwd=sub)
        git("push", "-q", "origin", "peer", cwd=sub)
        git("checkout", "-q", "-B", "main", cwd=sub)
        git("push", "-q", "origin", "main", cwd=sub)
        old_siblings = os.environ.get(SIBLINGS_ENV)
        os.environ[SIBLINGS_ENV] = "peer"
        run(["git", "-C", str(sub), "fetch", "origin", "--quiet"])
        if not publish_siblings(sub, dry_run=False):
            failures.append("a clean fast-forward should have published")
        git("fetch", "origin", "--quiet", cwd=sub)
        if git("rev-parse", "origin/peer", cwd=sub) != git(
                "rev-parse", "HEAD", cwd=sub):
            failures.append("origin/peer did not reach this branch's HEAD")

        # Now make the sibling genuinely diverge and prove it is REFUSED.
        # `work` has to start FROM origin/peer, or git rejects the push on
        # its own and the guard under test never runs: the fixture would pass
        # for the wrong reason.
        git("fetch", "origin", "--quiet", cwd=work)
        git("checkout", "-q", "-B", "sib", "origin/peer", cwd=work)
        (work / "sibling-only.txt").write_text("theirs\n", encoding="utf-8")
        git("add", "-A", cwd=work)
        git("commit", "-qm", "a commit only the sibling has", cwd=work)
        git("push", "-q", "origin", "sib:peer", cwd=work)
        run(["git", "-C", str(sub), "fetch", "origin", "--quiet"])
        if publish_siblings(sub, dry_run=False):
            failures.append("a DIVERGED sibling must refuse, not push")
        git("fetch", "origin", "--quiet", cwd=sub)
        if "sibling-only" not in git("show", "--stat", "origin/peer",
                                      cwd=sub):
            failures.append("the refusal did not protect the sibling's commit")
        if old_siblings is None:
            os.environ.pop(SIBLINGS_ENV, None)
        else:
            os.environ[SIBLINGS_ENV] = old_siblings

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print("sync-pin self-test: 9 assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
