#!/usr/bin/env python3
"""portability-check.py: find machine-specific paths and branch assumptions.

Five assertions, read from SOURCE TEXT and AST, never importing the tools
they inspect (importing would execute the tool being checked; reading its
source cannot). The one exception is machinelib.py's memory_bank_root(), the
resolver this order ships: it is imported and exercised directly, because a
resolver must be proven to RUN, not just to read.

    1. No file under tools/ contains the configured interpreter path, except
       the files that legitimately carry it. The exemption is an explicit
       named list, never a pattern match (docs/scoped-by-declaration.md).
    2. No file under tools/ returns or defaults to the bare string "windows"
       as a branch value. Detected via ast: a Return of the constant, a
       keyword-argument default of "windows" on a parameter whose name
       contains branch or base, an assignment to a name containing branch
       whose value is the constant. Legitimate sites are an explicit named
       list. The distinction that matters: "what branch does THIS machine
       own" is the bug; "what branch is the PC" is not.
    3. No MODULE-LEVEL AnnAssign under tools/ uses a PEP-604 union (an
       annotation containing a BinOp BitOr). Module scope only: walk
       tree.body, do not recurse into functions or classes. A union in a
       function or class annotation is not evaluated at import and is fine;
       a module-level one is evaluated on Python 3.9 and raises TypeError.
    4. No file under tools/ hardcodes the memory-bank root: the literal
        "Documents/ai/memory-bank" in any scanned file. 31 files carried
       it on 2026-09-02 and that is why neither machine can move
       (order 25). The one legitimate home is the fallback inside
       machinelib.py's memory_bank_root(), plus the files that quote real
       refused commands verbatim; a fixture keeps its literal or it is not
       a fixture.

Exit 0 when clean, exit 1 with one line per finding when not.

--self-test proves every assertion by BREAKING the behaviour in a fresh
temporary directory, watching it go red, then restoring it and watching it go
green. An assertion only ever seen passing is not known to run
(rules/shared/memory-integrity.md rule 2). The self-test never reads the real
tools/ directory as the subject: the resolver under test is imported from the
file this order ships, and every scan check runs against its own fixtures.
"""

import argparse
import ast
import importlib.util
import os
import re
import sys
import tempfile
import warnings
from pathlib import Path

TOOLS = Path(__file__).resolve().parent

# Defaults are environment-configurable for installations that use another
# interpreter or repository location.
INTERPRETER_PATH = str(
    Path(os.environ.get("INTERPRETER_PATH", "")) if os.environ.get("INTERPRETER_PATH")
    else Path.home() / ".local" / "bin" / "python")

# Check 1 exemptions, an explicit NAMED list, never a pattern match:
#   machinelib.py        its interpreter() fallback IS this path.
#   portability-check.py this file's constant and its fixtures carry it.
#   why-failed.py        verbatim driver-log fixtures (FRED_LOG, ORDER52_LOG)
#                        quote real refused commands that include the path; a
#                        fixture keeps its literal or it is not a fixture.
#   gatelib.py           SYSTEM_PYTHON IS THE LIST OF SPELLINGS TO REWRITE.
#                        This tool's entire job is turning those literals into
#                        portable ones, read from the real stored gates
#                        (175 backslash, 6 drive-slash, 2 POSIX on 2026-09-14).
#                        Removing them would delete the rewrite table.
#   drive-lane.py        self-test fixtures: gate strings and verbatim driver
#                        transcript lines. A fixture keeps its literal or it is
#                        not a fixture.
#   memdb.py             one documentation line recording the exact pg8000
#                        install command as it was run on the machine.
#
# ADDED 2026-09-16, when this check was first read. All three are the same
# species as why-failed.py: a literal that is DATA, not a dependency.
CHECK1_EXEMPT = {"machinelib.py", "portability-check.py", "why-failed.py",
                 "gatelib.py", "drive-lane.py", "memdb.py"}

# Check 2 exemptions. Each is the "what branch is the PC" half of the
# distinction, never "what branch does THIS machine own":
#   cleanup-crew.py          is_ancestor(..., base="windows") compares to the
#                            PC's branch deliberately.
#   check-lane-from-brief.py passes --base-branch windows: a PC lane's base.
#   land-lane.py             BASE_BRANCH = "windows": the declared base it
#                            lands memory-bank lanes onto.
#   lane-from-brief.py       BASE_BRANCH = "windows": the same declared base.
#   lint.py                  _BASE_BRANCH = "windows": the PC merge-base.
#   machinelib.py            machine_branch() RETURNS "windows" as the
#                            fallback; that is the one home of the machine fact.
#   portability-check.py     the self-test fixtures build their own world.
#   pc.py                    REMOTE_BRANCH = "windows": every name in that block
#                            is prefixed REMOTE and describes THE PC, read from
#                            the Mac over ssh. It is the textbook "what branch
#                            is the PC" case this check's own docstring calls
#                            legitimate. Added 2026-09-16.
CHECK2_EXEMPT = {
    "cleanup-crew.py",
    "check-lane-from-brief.py",
    "land-lane.py",
    "lane-from-brief.py",
    "lint.py",
    "machinelib.py",
    "portability-check.py",
    "pc.py",
}

DEFAULT_MEMORY_BANK_ROOT = str(
    Path(os.environ.get("MEMORY_BANK_DEFAULT_ROOT", ""))
    if os.environ.get("MEMORY_BANK_DEFAULT_ROOT") else
    Path.home() / ".local" / "share" / "memory-bank")

# Check 4 exemptions. Each is a legitimate home for a machine-specific root
# literal, an explicit NAMED list, never a pattern match:
#   machinelib.py        memory_bank_root()'s configured fallback IS this literal,
#                        the one legitimate home (order 25).
#   portability-check.py this file's constant and its self-test fixtures.
#   drive-lane.py        self-test fixtures deliberately quote out-of-lane
#                        repository paths, including a generic absolute path;
#                        a fixture keeps its literal or it is not a fixture.
#   why-failed.py        verbatim driver-log fixtures (FRED_LOG, ORDER52_LOG)
#                        quote real refused commands that include the path; a
#                        fixture keeps its literal or it is not a fixture.
#   check-lane-floor.py  a verbatim `git worktree list --porcelain` fixture and
#                        the expected value it is compared against.
#   lane-digest.py       the same, a verbatim worktree-porcelain fixture.
#   lane-scope.py        a verbatim opencode error message, quoted as it was
#                        actually printed.
#   gatelib.py           a stored gate STRING used as a rewrite fixture; the
#                        whole point of that fixture is that the path is the
#                        PC's and must be rewritten.
#   pc.py                BANK_REMOTE is the bank's path ON THE PC, read FROM
#                        the Mac over ssh. It is a REMOTE path by definition,
#                        so resolving it locally would be the actual bug.
#   usage.py             DELEGATE_SCRIPT, the same: the script's path on the
#                        PC, invoked remotely.
#
# ADDED 2026-09-16, when this check was first read by anyone. It had 14
# findings and nothing ran it: no hook, no lint, no gate. Five were real and
# are fixed (cleanup-crew, land-lane, lane-digest, lane-from-brief and
# public-sync all resolved through machinelib); the rest are the entries above.
# THE EXEMPTION IS PER FILE AND WRITTEN DOWN WITH ITS REASON, never a pattern:
# a pattern would have swallowed the five real ones too.
CHECK4_EXEMPT = {
    "machinelib.py",
    "portability-check.py",
    "drive-lane.py",
    "why-failed.py",
    "check-lane-floor.py",
    "lane-digest.py",
    "lane-scope.py",
    "gatelib.py",
    "pc.py",
    "usage.py",
}

# The literal every file hardcoded: "Documents/ai/memory-bank" with either
# slash, with or without a drive/user prefix. Collapsed from the escaped form
# ("Documents\\ai\\memory-bank") in check4 before matching.
ROOT_LITERAL_RE = re.compile(r"Documents[\\/]ai[\\/]memory-bank")


def check4(root):
    """A hardcoded memory-bank root must not appear outside the exemptions:
    machinelib.py is the one home of that literal."""
    findings = []
    for path in sorted(Path(root).glob("*.py")):
        if path.name in CHECK4_EXEMPT:
            continue
        # Collapse escaped backslashes (`"Documents\\ai\\memory-bank"`) so
        # a string written either way matches the same literal.
        text = _read(path).replace("\\\\", "\\")
        if ROOT_LITERAL_RE.search(text):
            findings.append(
                f"{path.name}: contains a hardcoded memory-bank root literal")
    return findings


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def check1(root):
    """The configured interpreter path must not appear outside the exemptions."""
    findings = []
    for path in sorted(Path(root).glob("*.py")):
        if path.name in CHECK1_EXEMPT:
            continue
        # Match the path whether the source wrote it escaped or raw: collapse
        # the escaped form first.
        text = _read(path).replace("\\\\", "\\")
        if INTERPRETER_PATH in text:
            findings.append(f"{path.name}: contains the configured interpreter path")
    return findings


def _is_windows_const(node):
    return isinstance(node, ast.Constant) and node.value == "windows"


def _tree(path):
    """The parsed file, or None when this interpreter cannot parse it.

    THE FILENAME IS PASSED ON PURPOSE. Without it a SyntaxError carries
    `<unknown>`, so `tools/lint.py` printed "the portability check could not
    run (unterminated string literal, line 160)" and named no casualty;
    finding the file took a loop over every tool in the directory.
    """
    try:
        return ast.parse(_read(path), filename=str(path))
    except SyntaxError:
        return None


def check0(root):
    """A tool that will not PARSE here cannot run here.

    The most portable-fault thing there is, and it belongs to this check rather
    than to a caller's exception handler. `check2` and `check3` used to let the
    SyntaxError escape, which took this whole check down, and `tools/lint.py`
    with it, on every Python before 3.12: `store-export.py` held an f-string
    whose replacement field spanned two lines, which is PEP 701 syntax and
    legal only from 3.12. Measured 2026-09-22 on 3.11.15 in a cloud container,
    where it also blocked the store export the rest of that lint depends on.

    NO EXEMPTION LIST, deliberately. Every other check here has one, because
    every other finding can be a legitimate machine-specific choice. A file
    that does not parse never is.
    """
    findings = []
    for path in sorted(Path(root).glob("*.py")):
        try:
            ast.parse(_read(path), filename=str(path))
        except SyntaxError as exc:
            findings.append(
                "%s:%d: does not parse on Python %d.%d (%s), so this tool "
                "cannot run on every machine"
                % (path.name, exc.lineno or 1, sys.version_info.major,
                   sys.version_info.minor, exc.msg))
    return findings


def check2(root):
    """Bare "windows" must not be returned or defaulted as a branch value."""
    findings = []
    for path in sorted(Path(root).glob("*.py")):
        if path.name in CHECK2_EXEMPT:
            continue
        tree = _tree(path)
        if tree is None:
            continue           # check0 owns the parse failure; no double count
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and _is_windows_const(node.value):
                findings.append(f"{path.name}:{node.lineno}: returns the bare "
                                f"string 'windows' as a branch value")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args
                defaults = node.args.defaults
                for arg, default in zip(args[len(args) - len(defaults):],
                                        defaults):
                    if (_is_windows_const(default)
                            and ("branch" in arg.arg.lower()
                                 or "base" in arg.arg.lower())):
                        findings.append(
                            f"{path.name}:{node.lineno}: parameter "
                            f"'{arg.arg}' defaults to the bare string "
                            f"'windows'")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Name)
                            and "branch" in target.id.lower()
                            and _is_windows_const(node.value)):
                        findings.append(
                            f"{path.name}:{node.lineno}: assigns the bare "
                            f"string 'windows' to '{target.id}'")
            if (isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and "branch" in node.target.id.lower()
                    and _is_windows_const(node.value)):
                findings.append(f"{path.name}:{node.lineno}: assigns the bare "
                                f"string 'windows' to '{node.target.id}'")
    return findings


def _has_union(expr):
    for node in ast.walk(expr):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return True
    return False


def check3(root):
    """Module-level annotations must not use a PEP-604 union."""
    findings = []
    for path in sorted(Path(root).glob("*.py")):
        tree = _tree(path)
        if tree is None:
            continue           # check0 owns the parse failure; no double count
        for node in tree.body:
            if (isinstance(node, ast.AnnAssign)
                    and node.annotation is not None
                    and _has_union(node.annotation)):
                findings.append(f"{path.name}:{node.lineno}: module-level "
                                f"annotation uses a PEP-604 union")
    return findings


def _load_machinelib():
    """Import the real machinelib.py under test by absolute path. Importing
    executes the module, which is safe: machinelib has no side effects at
    import. Loaded by path because the self-test must prove the shipped
    resolver runs, not a copy that could drift."""
    spec = importlib.util.spec_from_file_location("_machinelib_under_test",
                                                  TOOLS / "machinelib.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load tools/machinelib.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def self_test():
    """Prove each check by breaking the behaviour, then restoring it.

    Runs only against fresh temp directories; the real tools/ is never read
    here (the resolver under test is imported from the file this order ships).
    The violating fixture is planted and asserted RED, then removed and
    asserted GREEN.
    """
    assertions = 0
    fails = []

    def check(cond, label):
        nonlocal assertions
        assertions += 1
        if not cond:
            fails.append(label)

    base = Path(tempfile.mkdtemp(prefix="portability-selftest-"))
    try:
        # ---- the resolver: memory_bank_root() ----
        d0 = base / "zero"
        d0.mkdir()
        mod = _load_machinelib()

        repo = d0 / "repo"
        (repo / "tools").mkdir(parents=True)
        (repo / ".git").mkdir()
        mod.__file__ = str(repo / "tools" / "machinelib.py")
        saved_env = os.environ.pop("MEMORY_BANK_ROOT", None)
        try:
            got = mod.memory_bank_root()
            check(got == repo,
                  "resolver: expected the repository containing the script "
                  "(%s), got %s" % (repo, got))

            override = d0 / "override"
            os.environ["MEMORY_BANK_ROOT"] = str(override)
            got = mod.memory_bank_root()
            check(got == override,
                  "resolver: expected the $MEMORY_BANK_ROOT override (%s), "
                  "got %s" % (override, got))
            os.environ.pop("MEMORY_BANK_ROOT", None)

            mod.__file__ = str(d0 / "nowhere" / "script.py")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                got = mod.memory_bank_root()
            check(got == mod.DEFAULT_MEMORY_BANK_ROOT,
                  "resolver: expected the configured fallback path, got %s" % got)
            check(any(issubclass(w.category, RuntimeWarning) for w in caught),
                  "resolver: the configured fallback was returned SILENTLY, with no "
                  "warning")
            try:
                mod.memory_bank_root(strict=True)
                check(False, "resolver: strict=True should raise RuntimeError "
                             "on the fallback, it returned instead")
            except RuntimeError:
                pass
        finally:
            if saved_env is None:
                os.environ.pop("MEMORY_BANK_ROOT", None)
            else:
                os.environ["MEMORY_BANK_ROOT"] = saved_env

        # ---- check 1: the configured interpreter literal ----
        d1 = base / "one"
        d1.mkdir()
        (d1 / "ok.py").write_text("import sys\nPYTHON = sys.executable\n",
                                  encoding="utf-8")
        bad1 = d1 / "bad.py"
        bad1.write_text('PYTHON = r"%s"\n' % INTERPRETER_PATH, encoding="utf-8")
        hits = check1(d1)
        check(any("bad.py" in h for h in hits),
              "check 1: a file containing the configured interpreter literal was NOT "
              "flagged")
        check(len(hits) == 1,
              "check 1: expected exactly one finding, got %d" % len(hits))
        bad1.unlink()
        check(not check1(d1),
              "check 1: after the literal is removed the scan is green again")
        (d1 / "machinelib.py").write_text('SYSTEM_PYTHON = r"%s"\n' % INTERPRETER_PATH,
                                          encoding="utf-8")
        check(not check1(d1),
              "check 1: a file on the exemption list keeps its literal "
              "unflagged")

        # ---- check 2: "windows" as a branch value ----
        d2 = base / "two"
        d2.mkdir()
        bad_return = d2 / "bad-return.py"
        bad_return.write_text('def f():\n    return "windows"\n',
                              encoding="utf-8")
        hits = check2(d2)
        check(any("bad-return.py" in h and "returns" in h for h in hits),
              "check 2: a bare `return \"windows\"` was NOT flagged")
        bad_return.unlink()

        bad_default = d2 / "bad-default.py"
        bad_default.write_text('def count(lane_dir, base="windows"):\n'
                               "    return 0\n", encoding="utf-8")
        hits = check2(d2)
        check(any("bad-default.py" in h and "defaults" in h for h in hits),
              "check 2: a base-named parameter defaulting to 'windows' was "
              "NOT flagged")
        bad_default.unlink()

        bad_assign = d2 / "bad-assign.py"
        bad_assign.write_text('BASE_BRANCH = "windows"\n', encoding="utf-8")
        hits = check2(d2)
        check(any("bad-assign.py" in h and "assigns" in h for h in hits),
              "check 2: an assignment to a branch-named name was NOT flagged")
        bad_assign.unlink()
        check(not check2(d2),
              "check 2: with every violation removed the scan is green again")

        (d2 / "ok-tuple.py").write_text('def f():\n    return PROD_REPO, '
                                        '"windows"\n', encoding="utf-8")
        (d2 / "ok-set.py").write_text('PROTECTED_BRANCHES = {"windows", '
                                      '"mac", "main"}\n', encoding="utf-8")
        (d2 / "ok-call.py").write_text('git("branch", "-b", "windows")\n',
                                       encoding="utf-8")
        check(not check2(d2),
              "check 2: a tuple return, a set assignment and a call argument "
              "are NOT the bare-string bug and must not be flagged")

        # ---- check 3: module-level PEP-604 union ----
        d3 = base / "three"
        d3.mkdir()
        bad_union = d3 / "bad-union.py"
        bad_union.write_text("CLI_GATE: str | None = None\n", encoding="utf-8")
        hits = check3(d3)
        check(any("bad-union.py" in h for h in hits),
              "check 3: a module-level `str | None` annotation was NOT "
              "flagged")
        bad_union.unlink()
        check(not check3(d3),
              "check 3: after the union is removed the scan is green again")

        (d3 / "ok-local.py").write_text("def f():\n"
                                        "    x: str | None = None\n"
                                        "    return x\n", encoding="utf-8")
        (d3 / "ok-class.py").write_text("class C:\n"
                                        "    x: str | None = None\n",
                                        encoding="utf-8")
        (d3 / "ok-optional.py").write_text("from typing import Optional\n"
                                           "X: Optional[str] = None\n",
                                           encoding="utf-8")
        check(not check3(d3),
              "check 3: a function-local or class-level union, and a "
              "module-level Optional, are not the import-time bug")

        # ---- check 4: a hardcoded memory-bank root literal ----
        d4 = base / "four"
        d4.mkdir()
        (d4 / "ok.py").write_text('REPO = "<owner>/sample-repo"\n',
                                  encoding="utf-8")
        check(not check4(d4),
              "check 4: a GitHub slug is not a filesystem root and must not "
              "be flagged")

        bad = d4 / "bad.py"
        bad.write_text('ROOT = r"C:\\sample\\Documents\\ai\\memory-bank"\n',
                       encoding="utf-8")
        hits = check4(d4)
        check(any("bad.py" in h for h in hits),
              "check 4: a hardcoded memory-bank root was NOT flagged")
        bad.unlink()

        bad_fwd = d4 / "bad-fwd.py"
        bad_fwd.write_text('ROOT = "C:/sample/Documents/ai/'
                           'memory-bank"\n', encoding="utf-8")
        hits = check4(d4)
        check(any("bad-fwd.py" in h for h in hits),
              "check 4: the forward-slash form of the root was NOT flagged")
        bad_fwd.unlink()

        bad_esc = d4 / "bad-esc.py"
        bad_esc.write_text('ROOT = "C:\\\\sample\\\\Documents\\\\ai\\\\'
                           'memory-bank"\n', encoding="utf-8")
        hits = check4(d4)
        check(any("bad-esc.py" in h for h in hits),
              "check 4: the escaped-backslash form of the root was NOT "
              "flagged")
        bad_esc.unlink()
        check(not check4(d4),
              "check 4: after every literal is removed the scan is green again")

        (d4 / "machinelib.py").write_text(
            'FALLBACK = r"%s"\n' % DEFAULT_MEMORY_BANK_ROOT, encoding="utf-8")
        check(not check4(d4),
              "check 4: machinelib.py keeps its fallback literal unflagged")
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)

    for f in fails:
        print("self-test FAIL: %s" % f)
    if fails:
        return 1
    print("self-test: %d assertions passed; every positive assertion was seen "
          "red against its planted violation before the fixture was restored"
          % assertions)
    return 0


def findings(root=None) -> list:
    """Every finding, as a list, for a caller that is not a shell.

    THE IMPORTABLE ENTRY POINT, added 2026-09-16 when `tools/lint.py` started
    running this check. A caller must never get these by running this file and
    parsing its stdout: a regex that stops matching would return an empty list
    and read as CLEAN, which is the scrape fault named in
    `rules/shared/memory-integrity.md` ("one tool must never scrape another
    tool's prose"). `main()` below prints what this returns, so the two can
    never disagree.
    """
    root = Path(root) if root else TOOLS
    return (check0(root) + check1(root) + check2(root)
            + check3(root) + check4(root))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="directory to scan (default: this tools/)")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the checks against temp fixtures and exit")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    findings_ = findings(a.root)
    for line in findings_:
        print(line)
    if findings_:
        print("portability-check: %d finding(s)" % len(findings_))
        return 1
    print("portability-check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
