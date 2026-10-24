#!/usr/bin/env python3
"""Deterministic config-review audit. Zero model calls, stdlib only.

Observer, never a gate: writes state/routines/config-review.toon and exits 0
even with findings. --self-test proves the checks against its own temporary
fixture and is the only mode permitted to exit nonzero.
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

CHECKS = ["bare_interpreters", "dead_path_rules", "gitignore_tracked",
          "secret_shaped_keys", "stale_seen_ticks"]
ARTIFACT_REL = "state/routines/config-review.toon"


def default_out(root):
    """The artifact goes to the landing zone beside the repo, which is mounted
    as /state and read by the automation. Fixed 2026-10-11: it
    was <repo>/state, a folder nothing reads, so the review ran unseen."""
    return Path(root).parent / ARTIFACT_REL


TEMP_SUFFIXES = {".log", ".pid", ".tmp"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".py", ".toml", ".yaml", ".yml",
                 ".sh", ".ps1", ".psm1", ".csv", ".tsv", ".ini", ".cfg",
                 ".html", ".js", ".ts", ".sql", ".env"}
MAX_SCAN_BYTES = 2 * 1024 * 1024

RULE_LINE_RE = re.compile(r'\b(Read|Edit)\(([^)]+)\)')
BARE_RE = re.compile(r"(?<![\w./\\:-])(pytest|py\.test|pip3?|python3?)(?![\w.-])")
GHP_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")
GHP_LOOSE_RE = re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
JWT_LOOSE_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
B64_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")
PURE_HEX_RE = re.compile(r"[0-9a-fA-F]{40,}")
TICK_RE = re.compile(r"^-\s*\[[xX ]\].*?\brev\s+([0-9a-fA-F]{7,40})\b")
SECRET_CONTEXT_RE = re.compile(
    r"(?:=|:)\s*[\"']?[A-Za-z0-9+/=]{40,}[\"']?"
    r"|[\"'][A-Za-z0-9+/=]{40,}[\"']")


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


def git_ancestry(root):
    """Returns (ok, ancestor_set). ok False means HEAD could not be read."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-list", "HEAD"],
            capture_output=True, timeout=60)
        if proc.returncode != 0:
            return False, set()
        return True, {line.strip().lower()
                      for line in proc.stdout.decode("ascii", "replace").splitlines()
                      if line.strip()}
    except Exception:
        return False, set()


def redact(text):
    text = JWT_RE.sub("[REDACTED]", text)
    text = GHP_RE.sub("[REDACTED]", text)

    def _b64(m):
        s = m.group(0)
        return "[REDACTED]" if not PURE_HEX_RE.fullmatch(s) else s

    return B64_RE.sub(_b64, text)


def _rule_target(spec):
    """Normalise a Read/Edit path spec to an existence-checkable path.

    Returns (windows_path_or_None, reason_or_None). A non-normalised
    spelling (C:\\..., /c/...) is itself a finding because those forms
    never match how path rules are stored (//c/...).
    """
    spec = spec.strip()
    star = spec.find("*")
    base = spec[:star] if star != -1 else spec
    base = base.rstrip("/\\")
    if not base:
        return None, "wildcard-only rule resolves to no path"
    m = re.match(r"^//([A-Za-z])/(.*)$", base)
    if m:
        return "{}:\\{}".format(m.group(1).upper(),
                                m.group(2).replace("/", "\\")), None
    if re.match(r"^[A-Za-z]:[\\/]", base):
        return None, ("drive-letter form matches nothing "
                      "(normalise to //c/...)")
    if re.match(r"^//[^/]{2,}", base):
        # A double slash before a multi-letter segment is the POSIX absolute
        # form, the OTHER machine's path in a shared settings.json: valid
        # there, uncheckable here. Was misread as the single-slash form until
        # 2026-10-11 (12 false findings).
        return None, None
    m = re.match(r"^/[A-Za-z]/(.*)$", base)
    if m or base.startswith("/"):
        return None, ("single-slash /c/... form matches nothing "
                      "(normalise to //c/...)")
    return base, None


def check_dead_paths(root):
    findings = []
    settings = Path(root) / "settings.json"
    if not settings.is_file():
        return findings
    try:
        text = settings.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings
    for lineno, line in enumerate(text.splitlines(), 1):
        m = RULE_LINE_RE.search(line)
        if not m:
            continue
        rule = m.group(0)
        target, reason = _rule_target(m.group(2))
        if target is None and reason is None:
            continue
        if reason:
            findings.append({"check": "dead_path_rules", "path": "settings.json",
                             "line": lineno,
                             "detail": "%s: %s" % (rule, reason)})
        elif not os.path.exists(target):
            findings.append({"check": "dead_path_rules", "path": "settings.json",
                             "line": lineno,
                             "detail": "%s: target does not exist" % rule})
    return findings


def _archived(name):
    """A finished order or card under a done/ folder: history, never live
    config. 312 of 414 findings on 2026-10-11 came from these."""
    return "/done/" in "/" + name


def _bare_hits(line):
    """Bare interpreter words on a line, minus `-m pytest` (the canonical form)
    and `import pytest` / `from pytest`, which name a module, not a command."""
    hits = set()
    for m in BARE_RE.finditer(line):
        if line[:m.start()].rstrip().endswith(("-m", "import", "from")):
            continue
        hits.add(m.group(0))
    return sorted(hits)


def check_bare_interpreters(root, tracked):
    findings = []
    excluded = ("docs/command-forms", "docs/permission-incidents.md")
    for name in tracked:
        if name.startswith("reports/") or any(name.startswith(p) for p in excluded):
            continue
        if _archived(name):
            continue
        if Path(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        path = Path(root) / name
        if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fenced = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced:
                continue
            hits = _bare_hits(line)
            if hits:
                findings.append({
                    "check": "bare_interpreters", "path": name,
                    "line": lineno,
                    "detail": "bare %s: %s" % (
                        "+".join(hits),
                        redact(line.strip())[:120])})
    return findings


def check_tracked_tempfiles(tracked):
    return [{"check": "gitignore_tracked", "path": name, "line": 0,
             "detail": "tracked %s file should be ignored or removed"
                       % Path(name).suffix}
            for name in tracked
            if Path(name).suffix.lower() in TEMP_SUFFIXES]


def check_stale_ticks(root, tracked, ancestors, git_ok):
    findings = []
    if not git_ok:
        return findings
    ordered = sorted(ancestors)
    for name in tracked:
        if Path(name).suffix.lower() != ".md" or _archived(name):
            continue
        path = Path(root) / name
        if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        section = None
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.startswith("#"):
                section = line.lstrip("#").strip().lower()
                continue
            if section != "seen":
                continue
            m = TICK_RE.match(line.strip())
            if not m:
                continue
            rev = m.group(1).lower()
            full = [a for a in ordered if a.startswith(rev)]
            if len(full) != 1 or full[0] not in ancestors:
                findings.append({
                    "check": "stale_seen_ticks", "path": name,
                    "line": lineno,
                    "detail": "tick rev %s is not an ancestor of HEAD"
                              % rev})
    return findings


def _scan_line_for_secret(line):
    if GHP_LOOSE_RE.search(line):
        return "github-token-shaped value (withheld)"
    if JWT_LOOSE_RE.search(line):
        return "jwt-shaped value (withheld)"
    for m in SECRET_CONTEXT_RE.finditer(line):
        s = m.group(0)
        candidate = B64_RE.search(s)
        if candidate is None or PURE_HEX_RE.fullmatch(candidate.group(0)):
            continue
        # A long FILE PATH is base64-alphabet too. Three or more slashes in
        # one run is a path, not a key (80 false findings, 2026-10-11).
        if candidate.group(0).count("/") >= 3:
            continue
        return "long base64-like run (withheld)"
    return None


def check_secrets(root, tracked):
    findings = []
    skip = {ARTIFACT_REL}
    prose_dirs = ("docs/", "reports/", "rules/", "queue/")
    for name in tracked:
        if name in skip:
            continue
        path = Path(root) / name
        if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if b"\0" in blob[:4096]:
            continue
        text = blob.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if name.startswith(prose_dirs) and not (GHP_LOOSE_RE.search(line) or
                                                     JWT_LOOSE_RE.search(line)):
                continue
            kind = _scan_line_for_secret(line)
            if kind:
                findings.append({
                    "check": "secret_shaped_keys", "path": name,
                    "line": lineno,
                    "detail": "line matches %s; value never reported"
                              % kind})
    return findings


def render_toon(report):
    """The report as TOON, encoded by the one sanctioned encoder.

    CONVERTED 2026-09-16. This used to build the format by hand, joining
    tabular rows with commas and quoting with `json.dumps`, which is the shape
    `rules/shared/dev-practices.md` now forbids: a `detail` string containing a
    comma would have split its own row, and nothing would have said so. The
    reference implementation refuses several files this framework wrote.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from toon import encode
    return encode({
        "produced_at": report["produced_at"],
        "total_findings": len(report["findings"]),
        "counts": [{"check": c, "total": report["counts"][c]} for c in CHECKS],
        "findings": [{"check": f["check"], "path": f["path"],
                      "line": f["line"], "detail": f["detail"]}
                     for f in report["findings"]],
    }) + "\n"


def run_audit(root, tracked=None, ancestors=None, git_ok=None):
    root = Path(root)
    if tracked is None:
        tracked = tracked_files(root)
    if git_ok is None or ancestors is None:
        ok, anc = git_ancestry(root)
        git_ok = ok if git_ok is None else git_ok
        ancestors = anc if ancestors is None else ancestors
    findings = []
    findings += check_bare_interpreters(root, tracked)
    findings += check_dead_paths(root)
    findings += check_tracked_tempfiles(tracked)
    findings += check_secrets(root, tracked)
    findings += check_stale_ticks(root, tracked, ancestors, git_ok)
    counts = {c: sum(1 for f in findings if f["check"] == c) for c in CHECKS}
    return {"produced_at": utc_now(), "counts": counts, "findings": findings}


def summarize(report):
    lines = ["audit-config: %d finding(s)" % len(report["findings"])]
    for c in CHECKS:
        lines.append("  %-20s %d" % (c, report["counts"][c]))
    for f in report["findings"]:
        lines.append("  %s %s:%d %s" % (f["check"], f["path"], f["line"],
                                        f["detail"]))
    return "\n".join(lines)


def self_test():
    fixture = Path(tempfile.mkdtemp(prefix="audit-config-selftest-"))
    token_value = "ghp_" + ("Ab1" * 8)
    try:
        (fixture / "queue").mkdir()
        (fixture / "docs").mkdir()
        (fixture / "state" / "routines").mkdir(parents=True)
        settings = {
            "permissions": {
                "allow": [
                    "Bash(git status)",
                    "Read(//c/nonexistent-config-review-target/file.txt)",
                    "Read(//" + "home/someone/dev/**)",
                ],
                "deny": [
                    "Edit(C:\\missing\\dir\\file.md)",
                ],
            }
        }
        (fixture / "settings.json").write_text(json.dumps(settings, indent=2),
                                               encoding="utf-8")
        (fixture / "queue" / "bare.md").write_text(
            "# notes\nrun: `pytest -q`\nthen: `pip install requests`\n"
            "fine: C:\\Python313\\python.exe tools/lint.py\n",
            encoding="utf-8")
        (fixture / "queue" / "fenced.md").write_text(
            "# notes\n```sh\npytest -q\n```\n", encoding="utf-8")
        (fixture / "queue" / "canonical.md").write_text(
            "# notes\n```sh\nC:/venv/python.exe -m pytest -q\n```\n"
            "```python\nimport pytest\nfrom pytest import raises\n```\n",
            encoding="utf-8")
        (fixture / "queue" / "done").mkdir()
        (fixture / "queue" / "done" / "old.md").write_text(
            "# old\n```sh\npytest -q\n```\n## Seen\n"
            "- [x] windows 2026-08-01 06:00 rev abcdef1234567\n",
            encoding="utf-8")
        (fixture / "conf.json").write_text(
            '{"cmd": "/c/' + 'home/someone/AppData/Local/Programs/Python/Python313/py"}\n',
            encoding="utf-8")
        (fixture / "docs" / "prose.md").write_text(
            "A bare pytest belongs in prose, not a command.\n"
            "Example text: Abcdefghijklmnopqrstuvwxyz0123456789+/abcd\n"
            "A real ghp_Abcdefghijklmnop token is still reported.\n",
            encoding="utf-8")
        (fixture / "secrets.txt").write_text("api_key = %s\n" % token_value,
                                             encoding="utf-8")
        (fixture / "leftover.log").write_text("noise\n", encoding="utf-8")
        (fixture / "ledger.md").write_text(
            "## Seen\n- [x] windows 2026-08-22 06:00 rev deadbeefcafe123\n"
            "- [ ] mac\n",
            encoding="utf-8")

        report = run_audit(fixture, tracked=None,
                           ancestors=frozenset(), git_ok=True)
        artifact = render_toon(report)
        failures = []

        def expect(cond, label):
            if not cond:
                failures.append(label)

        expect(report["counts"]["dead_path_rules"] == 2,
               "expected 2 dead path rules, got %d"
               % report["counts"]["dead_path_rules"])
        expect(any("nonexistent-config-review-target" in f["detail"]
                   for f in report["findings"]), "missing-path rule not found")
        expect(any("normalise to //c/..." in f["detail"]
                   for f in report["findings"]),
               "drive-letter spelling rule not flagged")
        expect(report["counts"]["bare_interpreters"] == 1,
               "expected 1 bare interpreter hit, got %d"
               % report["counts"]["bare_interpreters"])
        expect(any(f["path"] == "queue/fenced.md" and f["line"] == 3
                   and "pytest" in f["detail"] for f in report["findings"]),
               "planted bare pytest not found")
        expect(not any(f["path"] == "queue/bare.md" and f["line"] == 4
                       for f in report["findings"]),
               "absolute-path python was flagged")
        expect(report["counts"]["gitignore_tracked"] == 1,
               "tracked leftover.log not flagged")
        expect(report["counts"]["stale_seen_ticks"] == 1,
               "stale Seen tick not flagged")
        expect(any("deadbeefcafe123" in f["detail"]
                   for f in report["findings"]), "stale rev not named")
        expect(report["counts"]["secret_shaped_keys"] == 2,
               "planted secret-shaped values not flagged")
        expect(not any(f["path"] == "docs/prose.md" and
                       "base64-like" in f["detail"]
                       for f in report["findings"]),
               "prose base64-like run was flagged")
        expect(any(f["path"] == "docs/prose.md" and
                   "github-token-shaped" in f["detail"]
                   for f in report["findings"]),
               "literal ghp_ token in prose was not flagged")
        expect(token_value not in artifact, "token VALUE leaked into artifact")
        expect(token_value not in summarize(report),
               "token VALUE leaked into stdout summary")
        expect(default_out(Path("/x/memory-bank"))
               == Path("/x/state/routines/config-review.toon"),
               "default artifact is not in the landing zone beside the repo")
        expect(not any(f["path"] == "queue/canonical.md"
                       for f in report["findings"]),
               "`-m pytest` or `import pytest` was flagged as a bare interpreter")
        expect(not any(f["path"].startswith("queue/done/")
                       for f in report["findings"]),
               "an archived done/ file was audited")
        expect(not any(f["path"] == "conf.json" for f in report["findings"]),
               "a long file path was flagged as a base64 secret")
        expect(not any("someone/dev" in f["detail"] for f in report["findings"]),
               "the other machine's double-slash absolute rule was flagged")

        if failures:
            print("SELF-TEST FAILED:")
            for f in failures:
                print("  - %s" % f)
            return 1
        print("self-test PASS: 5 checks, %d planted findings, "
              "token value never surfaced" % len(report["findings"]))
        return 0
    finally:
        shutil.rmtree(fixture, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="repository root (default: this repo)")
    ap.add_argument("--out", help="artifact path (default: <root>/../state/"
                                  "routines/config-review.toon)")
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
