#!/usr/bin/env python3
"""Deterministic framework audit. Zero model calls, stdlib only.

Observer, never a gate: writes state/routines/framework-audit.toon and exits 0
even with findings. --self-test proves the checks against its own temporary
fixture and is the only mode permitted to exit nonzero.

Checks the deterministic subset of the acceptance invariants in
reports/2026-08-04-framework-acceptance.md. Anything that needs the owner's eyes is
reported as unknown, never as PASS: a script must never claim to have observed
anything. The lint is executed, not re-implemented: tools/lint.py runs as a
subprocess under the same absolute interpreter this script runs under
(sys.executable).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CHECKS = ["acceptance_roster", "hook_paths", "symlink_health",
          "repo_hygiene", "lint"]
ARTIFACT_REL = "state/routines/framework-audit.toon"


def default_out(root):
    """The artifact goes to the landing zone beside the repo, which is mounted
    as /state and read by the automation. Fixed 2026-10-11: it
    was <repo>/state, a folder nothing reads, so the audit ran unseen."""
    return Path(root).parent / ARTIFACT_REL


ACCEPTANCE_REL = "reports/2026-08-04-framework-acceptance.md"
TEMP_SUFFIXES = {".log", ".pid", ".tmp"}
HOOK_EXT_RE = re.compile(
    r'[^\s"]+\.(?:ps1|psm1|py|sh|cmd|bat)\b', re.IGNORECASE)
ACCEPT_HEADER_RE = re.compile(r'^\|\s*Line\s*\|.*Result')
ACCEPT_ROW_RE = re.compile(r'^\|\s*([A-Za-z]\w*)\s*\|(.*)$')
ACCEPT_SEP_RE = re.compile(r'^\|[\s:\-|]*\|?\s*$')


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tracked_files(root):
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, timeout=60)
        if proc.returncode == 0:
            names = proc.stdout.decode("utf-8", "replace").split("\0")
            return [n.replace("\\", "/") for n in names if n]
    except Exception:
        pass
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            full = Path(dirpath) / fn
            out.append(full.resolve().relative_to(Path(root).resolve())
                       .as_posix())
    return sorted(out)


def git_state(root):
    """(ok, unpushed_commit_count_or_None, stash_count_or_None)."""
    def run(args):
        try:
            proc = subprocess.run(["git", "-C", str(root)] + args,
                                  capture_output=True, timeout=30)
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8", "replace")

    ok = False
    unpushed = None
    stashes = None
    revs = run(["rev-list", "--count", "@{u}..HEAD"])
    if revs is not None:
        ok = True
        try:
            unpushed = int(revs.strip())
        except ValueError:
            unpushed = None
    listed = run(["stash", "list"])
    if listed is not None:
        stashes = len([l for l in listed.splitlines() if l.strip()])
    return ok, unpushed, stashes


def classify_result(cell):
    """Verdict class of one Result-table cell: fail, unverified, or pass."""
    c = cell.strip().strip("*").strip()
    if c.startswith("FAIL"):
        return "fail"
    if "UNVERIFIED" in c or c.startswith("PARTLY"):
        return "unverified"
    return "pass"


def parse_acceptance(path):
    """(readable, fail_cells, unverified_count, row_count).

    fail_cells is a list of (row_id, line_number, result_text)."""
    fails = []
    unverified = 0
    rows = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, [], 0, 0
    in_table = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if not in_table:
            if ACCEPT_HEADER_RE.match(line):
                in_table = True
            continue
        if ACCEPT_SEP_RE.match(line):
            continue
        m = ACCEPT_ROW_RE.match(line)
        if not m:
            in_table = False
            continue
        result_cell = m.group(2).split("|")[0].strip()
        rows += 1
        verdict = classify_result(result_cell)
        if verdict == "fail":
            fails.append((m.group(1), lineno, result_cell))
        elif verdict == "unverified":
            unverified += 1
    return True, fails, unverified, rows


def check_acceptance(root):
    """Returns (findings, unverified_count)."""
    findings = []
    path = Path(root) / ACCEPTANCE_REL
    readable, fails, unverified, rows = parse_acceptance(path)
    if not readable:
        findings.append({
            "check": "acceptance_roster", "path": ACCEPTANCE_REL,
            "line": 0,
            "detail": "acceptance report is missing or unreadable"})
        return findings, 0
    for rid, lineno, cell in fails:
        findings.append({
            "check": "acceptance_roster", "path": ACCEPTANCE_REL,
            "line": lineno,
            "detail": "%s is FAIL: %s" % (rid, cell[:160])})
    return findings, unverified


def check_hooks(root):
    findings = []
    settings = Path(root) / "settings.json"
    if not settings.is_file():
        return [{"check": "hook_paths", "path": "settings.json",
                 "line": 0, "detail": "settings.json is missing"}]
    try:
        data = json.loads(settings.read_text(encoding="utf-8",
                                             errors="replace"))
    except (OSError, ValueError):
        return [{"check": "hook_paths", "path": "settings.json",
                 "line": 0, "detail": "settings.json does not parse"}]
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return findings
    home = os.path.expanduser("~")
    checked = set()
    for event in sorted(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                cmd = hook.get("command") or ""
                if not cmd:
                    continue
                expanded = cmd.replace("$HOME", home)
                if expanded.startswith("~"):
                    expanded = home + expanded[1:]
                for m in HOOK_EXT_RE.finditer(expanded):
                    target = m.group(0)
                    key = target.lower()
                    if key in checked:
                        continue
                    checked.add(key)
                    candidates = [target]
                    if not os.path.isabs(target):
                        candidates.append(str(Path(root) / target))
                    if not any(os.path.exists(c) for c in candidates):
                        findings.append({
                            "check": "hook_paths", "path": target,
                            "line": 0,
                            "detail": "hook script does not exist "
                                      "(registered for %s)" % event})
    return findings


def check_symlink(root, home_settings=None):
    """Returns (findings, resolved_target_string).

    home_settings overrides the checked path so the self-test can prove both
    directions without creating a real symlink."""
    hp = (Path(home_settings).expanduser() if home_settings
          else Path.home() / ".claude" / "settings.json")
    label = "~/.claude/settings.json"
    repo = Path(root) / "settings.json"
    if not hp.exists():
        return ([{"check": "symlink_health", "path": str(hp), "line": 0,
                  "detail": "%s is missing" % label}], "")
    resolved = str(Path(os.path.realpath(hp)))
    expected = str(Path(os.path.realpath(repo)))
    if resolved != expected:
        findings = [{"check": "symlink_health", "path": resolved,
                     "line": 0,
                     "detail": "%s resolves outside this repo "
                               "(expected %s)" % (label, expected)}]
    else:
        findings = []
    return findings, resolved


def check_repo_hygiene(tracked, git_ok, unpushed, stashes):
    findings = []
    if not git_ok:
        findings.append({"check": "repo_hygiene", "path": ".git",
                         "line": 0,
                         "detail": "git state unavailable, unpushed and "
                                   "stash counts are unknown"})
    else:
        if unpushed:
            findings.append({
                "check": "repo_hygiene", "path": "HEAD", "line": 0,
                "detail": "%d unpushed commit(s) on HEAD" % unpushed})
        if stashes:
            findings.append({
                "check": "repo_hygiene", "path": "refs/stash", "line": 0,
                "detail": "%d stash entrie(s)" % stashes})
    for name in tracked:
        if Path(name).suffix.lower() in TEMP_SUFFIXES:
            findings.append({
                "check": "repo_hygiene", "path": name, "line": 0,
                "detail": "tracked %s file should be ignored or removed"
                          % Path(name).suffix})
    return findings


def run_lint(root, interpreter=None):
    exe = interpreter or sys.executable
    lint_py = Path(root) / "tools" / "lint.py"
    if not lint_py.is_file():
        return {"ran": False, "exit_code": None, "errors": None}
    try:
        proc = subprocess.run(
            [exe, str(lint_py), "--json"], cwd=str(root),
            capture_output=True, timeout=300)
    except Exception:
        return {"ran": False, "exit_code": None, "errors": None}
    errors = None
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        data = None
    if isinstance(data, list):
        errors = sum(1 for v in data
                     if isinstance(v, dict)
                     and v.get("severity") == "ERROR"
                     and not v.get("baselined"))
    return {"ran": True, "exit_code": proc.returncode, "errors": errors}


def lint_finding(result):
    parts = []
    if not result["ran"]:
        parts.append("did not run")
    else:
        if result["exit_code"] not in (0, None):
            parts.append("exit %d" % result["exit_code"])
        if result["errors"] is None:
            parts.append("error count unparseable")
        elif result["errors"]:
            # Zero errors is a clean lint, never a finding: until 2026-10-11 it
            # was reported as "0 new error(s)" and every audit carried one.
            parts.append("%d new error(s)" % result["errors"])
    if not parts:
        return None
    return {"check": "lint", "path": "tools/lint.py", "line": 0,
            "detail": "; ".join(parts)}


def render_toon(report):
    """The report as TOON, encoded by the one sanctioned encoder.

    CONVERTED 2026-09-16 off a hand-built version. `symlink_target` is a PATH,
    which is exactly the value class that broke the hand-rolled writers: it was
    interpolated unquoted into a scalar line, so a path containing a colon
    (every Windows path does) produced something the reference implementation
    refuses. Rule: `rules/shared/dev-practices.md`.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from toon import encode
    return encode({
        "produced_at": report["produced_at"],
        "total_findings": len(report["findings"]),
        "unverified_roster_lines": report["unverified_roster_lines"],
        "symlink_target": report["symlink_target"],
        "counts": [{"check": c, "total": report["counts"][c]} for c in CHECKS],
        "findings": [{"check": f["check"], "path": f["path"],
                      "line": f["line"], "detail": f["detail"]}
                     for f in report["findings"]],
    }) + "\n"


def summarize(report):
    lines = ["audit-framework: %d finding(s)" % len(report["findings"]),
             "  symlink_target       %s" % report["symlink_target"]]
    for c in CHECKS:
        lines.append("  %-20s %d" % (c, report["counts"][c]))
    lines.append("  %-20s %d" % ("unverified_roster",
                                    report["unverified_roster_lines"]))
    for f in report["findings"]:
        lines.append("  %s %s:%d %s" % (f["check"], f["path"],
                                        f["line"], f["detail"]))
    return "\n".join(lines)


def run_audit(root, tracked=None, git_ok=None, unpushed=None, stashes=None,
              lint_result=None, home_settings=None):
    root = Path(root)
    if tracked is None:
        tracked = tracked_files(root)
    if git_ok is None:
        git_ok, unpushed, stashes = git_state(root)
    if lint_result is None:
        lint_result = run_lint(root)
    findings = []
    roster, unverified = check_acceptance(root)
    findings += roster
    findings += check_hooks(root)
    link_findings, link_target = check_symlink(root,
                                               home_settings=home_settings)
    findings += link_findings
    findings += check_repo_hygiene(tracked, git_ok, unpushed, stashes)
    lint_hit = lint_finding(lint_result)
    if lint_hit:
        findings.append(lint_hit)
    counts = {c: sum(1 for f in findings if f["check"] == c)
              for c in CHECKS}
    return {"produced_at": utc_now(), "counts": counts,
            "findings": findings, "unverified_roster_lines": unverified,
            "symlink_target": link_target}


def self_test():
    base = Path(tempfile.mkdtemp(prefix="audit-framework-selftest-"))
    repo = base / "repo"
    home = base / "home"
    token_marker = "PLANTED-FOR-SELF-TEST"
    try:
        (repo / "reports").mkdir(parents=True)
        (repo / "hooks").mkdir()
        (repo / "state" / "routines").mkdir(parents=True)
        (home).mkdir()

        table = [
            "# Acceptance",
            "",
            "| Line | Result | Date | Observed by |",
            "|---|---|---|---|",
            "| A9 | FAIL (%s) | 2026-08-22 | nobody |" % token_marker,
            "| B1 | PASS (clean control, must not be flagged) | 2026-08-04"
            " | the owner |",
            "| A2 | UNVERIFIED, never observed | | nobody |",
            "| A4 | PARTLY VERIFIED (control) | 2026-08-05 | nobody |",
            "",
        ]
        (repo / ACCEPTANCE_REL).write_text("\n".join(table),
                                           encoding="utf-8")

        live_hook = repo / "hooks" / "live.sh"
        live_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        absent_hook = repo / "hooks" / "absent.ps1"
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [
                        {"type": "command", "command": str(live_hook)},
                        {"type": "command",
                         "command": "powershell -NoProfile -File \"%s\""
                                    % absent_hook},
                    ]},
                ],
            },
        }
        (repo / "settings.json").write_text(json.dumps(settings, indent=2),
                                            encoding="utf-8")

        (repo / "stray.log").write_text("noise\n", encoding="utf-8")
        (home / "settings.json").write_text("{}\n", encoding="utf-8")

        report = run_audit(
            repo, tracked=["stray.log"],
            git_ok=True, unpushed=0, stashes=0,
            lint_result={"ran": True, "exit_code": 1, "errors": 3},
            home_settings=str(home / "settings.json"))
        artifact = render_toon(report)
        failures = []

        def expect(cond, label):
            if not cond:
                failures.append(label)

        expect(set(report["counts"]) == set(CHECKS),
               "counts keys do not match CHECKS")
        expect(report["counts"]["acceptance_roster"] == 1,
               "expected 1 FAIL roster line, got %d"
               % report["counts"]["acceptance_roster"])
        expect(any(token_marker in f["detail"]
                   for f in report["findings"]),
               "planted FAIL row not found")
        expect(not any("clean control" in f["detail"]
                       for f in report["findings"]),
               "PASS control row was flagged")
        expect(report["unverified_roster_lines"] == 2,
               "expected 2 UNVERIFIED/PARTLY rows, got %d"
               % report["unverified_roster_lines"])
        expect(report["counts"]["hook_paths"] == 1,
               "expected 1 missing hook, got %d"
               % report["counts"]["hook_paths"])
        expect(any("absent.ps1" in f["path"]
                   for f in report["findings"]
                   if f["check"] == "hook_paths"),
               "absent hook script not named")
        expect(not any("live.sh" in f.get("path", "")
                       for f in report["findings"]),
               "existing hook was flagged")
        expect(report["counts"]["symlink_health"] == 1,
               "expected 1 symlink mismatch, got %d"
               % report["counts"]["symlink_health"])
        clean_link, _ = check_symlink(repo,
                                      home_settings=str(repo
                                                        / "settings.json"))
        expect(clean_link == [],
               "matching symlink target was flagged")
        expect(report["counts"]["repo_hygiene"] == 1,
               "expected 1 tracked tempfile, got %d"
               % report["counts"]["repo_hygiene"])
        expect(any(f["path"] == "stray.log"
                   for f in report["findings"]),
               "planted stray.log not flagged")
        expect(report["counts"]["lint"] == 1,
               "expected 1 lint finding, got %d"
               % report["counts"]["lint"])
        expect(any("3 new error(s)" in f["detail"]
                   and f["check"] == "lint"
                   for f in report["findings"]),
               "lint error count not reported")
        expect(len(report["findings"]) == 5,
               "expected 5 total findings, got %d"
               % len(report["findings"]))
        expect("total_findings: 5" in artifact,
               "artifact does not carry the finding count")
        expect(default_out(Path("/x/memory-bank"))
               == Path("/x/state/routines/framework-audit.toon"),
               "default artifact is not in the landing zone beside the repo")
        expect(lint_finding({"ran": True, "exit_code": 0, "errors": 0}) is None,
               "a clean lint (0 errors, exit 0) was reported as a finding")

        if failures:
            print("SELF-TEST FAILED:")
            for f in failures:
                print("  - %s" % f)
            return 1
        print("self-test PASS: 5 checks, %d planted findings, "
              "controls stayed green" % len(report["findings"]))
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="repository root (default: this repo)")
    ap.add_argument("--out", help="artifact path (default: <root>/../state/"
                                  "routines/framework-audit.toon)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the throwaway-fixture self-test and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    root = Path(args.root).resolve() if args.root \
        else Path(__file__).resolve().parents[1]
    report = run_audit(root)
    out = Path(args.out) if args.out else default_out(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    artifact = render_toon(report)
    out.write_text(artifact, encoding="utf-8")
    print(summarize(report))
    print("artifact: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
