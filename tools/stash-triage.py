#!/usr/bin/env python3
"""Triage git stashes: report what each one holds and whether it already sits in HEAD.

Verdicts, exactly three:
  ALREADY LANDED  every hunk in the stash is present in HEAD
  DIVERGED        some hunk is not present in HEAD
  EMPTY           the stash touches no files

ALREADY LANDED is decided by reverse-applying the stash patch against a
throwaway detached worktree of HEAD -- never guessed from filenames or dates.
The target repo's working tree is never modified; the only side effect is a
temp worktree under the system temp dir, deleted on exit. This tool never
drops, pops or applies a stash; it prints the drop commands for a human.
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile

VERDICT_LANDED = "ALREADY LANDED"
VERDICT_DIVERGED = "DIVERGED"
VERDICT_EMPTY = "EMPTY"


def _run(args, input_bytes=None, cwd=None):
    return subprocess.run(
        args,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
    )


def git(repo, argv, input_bytes=None):
    proc = _run(["git", "-C", repo, *argv], input_bytes=input_bytes)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError("git %s failed: %s" % (" ".join(argv), detail))
    return proc.stdout


def git_ok(repo, argv, input_bytes=None):
    return _run(["git", "-C", repo, *argv], input_bytes=input_bytes).returncode == 0


def list_stashes(repo):
    out = git(repo, ["stash", "list"]).decode("utf-8", "replace")
    stashes = []
    for line in out.splitlines():
        if not line.strip():
            continue
        head, sep, desc = line.partition(": ")
        if not sep or not head.startswith("stash@{"):
            continue
        stashes.append((head.strip(), desc.strip()))
    return stashes


def files_touched(repo, ref):
    out = git(repo, ["stash", "show", "--name-only", ref]).decode("utf-8", "replace")
    return [ln for ln in (raw.strip() for raw in out.splitlines()) if ln]


def lands_in_head(repo, ref):
    patch = git(repo, ["stash", "show", "-p", ref])
    scratch = tempfile.mkdtemp(prefix="stash-triage-wt-")
    added = False
    try:
        git(repo, ["worktree", "add", "--detach", scratch, "HEAD"])
        added = True
        return git_ok(scratch, ["apply", "--check", "--reverse"], input_bytes=patch)
    finally:
        if added:
            _run(["git", "-C", repo, "worktree", "remove", "--force", scratch])
        _rmtree_force(scratch)
        _run(["git", "-C", repo, "worktree", "prune"])


def _rmtree_force(path):
    if not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    shutil.rmtree(path, True)


def triage(repo):
    results = []
    for ref, desc in list_stashes(repo):
        try:
            files = files_touched(repo, ref)
        except RuntimeError as exc:
            results.append({
                "ref": ref, "desc": desc, "files": [],
                "verdict": VERDICT_DIVERGED, "note": "(could not inspect: %s)" % exc,
            })
            continue
        if not files:
            results.append({"ref": ref, "desc": desc, "files": [], "verdict": VERDICT_EMPTY})
            continue
        verdict = VERDICT_LANDED if lands_in_head(repo, ref) else VERDICT_DIVERGED
        results.append({"ref": ref, "desc": desc, "files": files, "verdict": verdict})
    return results


def report(results):
    if not results:
        print("No stashes in this repo.")
        return
    for e in results:
        files = ", ".join(e["files"]) if e["files"] else "(none)"
        print("%s: %s" % (e["ref"], e["desc"]))
        print("  files:   %s" % files)
        print("  verdict: %s%s" % (e["verdict"], (" " + e["note"]) if e.get("note") else ""))
        print()
    droppable = [e for e in results if e["verdict"] == VERDICT_LANDED]
    if droppable:
        print("Already in HEAD; safe for a human to drop:")
        for e in droppable:
            print('  git stash drop "%s"' % e["ref"])
    else:
        print("Nothing judged ALREADY LANDED; nothing to drop.")


def _put(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def self_test():
    failures = []
    checks_run = 0

    def check(name, cond, detail=""):
        nonlocal checks_run
        checks_run += 1
        ok = bool(cond)
        print("[%s] %s" % ("PASS" if ok else "FAIL", name))
        if not ok and detail:
            print("       got: %s" % detail)
        if not ok:
            failures.append(name)

    scratch_root = tempfile.mkdtemp(prefix="stash-triage-selftest-")
    try:
        repo = os.path.join(scratch_root, "repo")
        os.makedirs(repo)
        git(repo, ["init"])
        git(repo, ["config", "user.email", "selftest"])
        git(repo, ["config", "user.name", "Stash Triage Self Test"])

        _put(os.path.join(repo, "f.txt"), "one\n")
        git(repo, ["add", "f.txt"])
        git(repo, ["commit", "-m", "base"])

        _put(os.path.join(repo, "f.txt"), "two\n")
        git(repo, ["stash", "push", "-m", "landed-later"])
        _put(os.path.join(repo, "f.txt"), "two\n")
        git(repo, ["add", "f.txt"])
        git(repo, ["commit", "-m", "same change"])

        _put(os.path.join(repo, "g.txt"), "fresh\n")
        git(repo, ["add", "g.txt"])
        git(repo, ["stash", "push", "-m", "diverged-change"])

        stashed = git(repo, ["commit-tree", "HEAD^{tree}", "-p", "HEAD"]).decode().strip()
        empty_sha = git(repo, ["commit-tree", "HEAD^{tree}", "-p", "HEAD", "-p", stashed]).decode().strip()
        git(repo, ["stash", "store", "-m", "empty-stash", empty_sha])

        list_before = git(repo, ["stash", "list"]).decode()
        results = triage(repo)

        def verdict_for(tag):
            for e in results:
                if tag in e["desc"]:
                    return e["verdict"]
            return None

        check("three stashes built in the throwaway repo", len(results) == 3, str(len(results)))
        check("committed-then-restashed change reads ALREADY LANDED",
              verdict_for("landed-later") == VERDICT_LANDED, str(verdict_for("landed-later")))
        check("never-committed change reads DIVERGED",
              verdict_for("diverged-change") == VERDICT_DIVERGED, str(verdict_for("diverged-change")))
        check("content-free stash reads EMPTY",
              verdict_for("empty-stash") == VERDICT_EMPTY, str(verdict_for("empty-stash")))

        list_after = git(repo, ["stash", "list"]).decode()
        check("git stash list unchanged by analysis", list_after == list_before)
        check("no throwaway worktrees left registered",
              len(git(repo, ["worktree", "list"]).decode().splitlines()) == 1)
    finally:
        _rmtree_force(scratch_root)

    if failures:
        print("\nSELF-TEST FAILED: %d checks ran, %d failure(s): %s"
              % (checks_run, len(failures), "; ".join(failures)))
        return 1
    print("\nSELF-TEST PASSED: %d checks passed" % checks_run)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="stash-triage",
        description="Report what every git stash holds and whether it already sits in HEAD.",
    )
    parser.add_argument("--repo", default=os.getcwd(),
                        help="target repository (default: current directory)")
    parser.add_argument("--self-test", action="store_true",
                        help="verify verdicts and the no-mutation guarantee on its own throwaway repo")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    try:
        results = triage(args.repo)
    except RuntimeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
