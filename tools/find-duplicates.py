#!/usr/bin/env python3
"""Find the same fact written in two places. Stdlib only, no model, no network.

Compares paragraphs across tracked markdown (default: rules/, docs/, memory/)
with difflib.SequenceMatcher on normalised text and reports pairs that state
the same thing in different places. Observer, never a gate: writes
state/routines/duplicate-facts.toon and exits 0 always. --self-test proves the
detector against its own temporary fixture and is the only mode permitted to
exit nonzero.

A pair is NOT a finding when either paragraph is marked dead (Superseded /
kept for provenance / RETRACTED / RETIRED, any case), sits inside a fenced
code block, a table row or frontmatter, or runs under about 15 words. When in
doubt stay silent: a false positive costs a person a read and teaches them to
ignore the tool.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# The TOON encoder it writes its report with, which a kit copy needs beside it
# (tools/publish-tool.py reads this line).
KIT_NEEDS = ("toon.py",)

ARTIFACT_REL = "state/routines/duplicate-facts.toon"
DEFAULT_PATHS = ["rules", "docs", "memory"]
DEFAULT_THRESHOLD = 0.8
MIN_WORDS = 15
MAX_SCAN_BYTES = 2 * 1024 * 1024
EXCERPT_CHARS = 240

DEAD_MARKERS = ("superseded", "kept for provenance", "retracted", "retired")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
NON_WORD_RE = re.compile(r"[^a-z0-9\s]+")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(text):
    """Lowercased, punctuation and markdown emphasis stripped, collapsed."""
    for ch in ("*", "_", "`"):
        text = text.replace(ch, "")
    text = NON_WORD_RE.sub(" ", text.lower())
    return " ".join(text.split())


def is_dead(raw):
    low = raw.lower()
    return any(marker in low for marker in DEAD_MARKERS)


def iter_paragraphs(text):
    """Yields (start_line, raw_text) for prose blocks.

    Skips frontmatter, anything inside fenced code blocks, and table rows;
    a paragraph never contains one. Headings survive here and are dropped
    later by the word floor.
    """
    paragraphs = []
    buf = []
    start = None
    fence_char = ""
    fence_len = 0
    in_frontmatter = False

    def flush():
        nonlocal buf, start
        if buf and start is not None:
            paragraphs.append((start, "\n".join(buf)))
        buf = []
        start = None

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if lineno == 1 and stripped in ("---", "--- "):
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped in ("---", "..."):
                in_frontmatter = False
            continue
        if fence_char:
            m = FENCE_RE.match(stripped)
            if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
                fence_char = ""
            continue
        m = FENCE_RE.match(stripped)
        if m:
            flush()
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("|"):
            flush()
            continue
        if start is None:
            start = lineno
        buf.append(line)
    flush()
    return paragraphs


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


def load_paragraphs(root, paths):
    root = Path(root)
    tops = tuple(p.replace("\\", "/").rstrip("/") + "/" for p in paths)
    names = [n for n in tracked_files(root)
             if n.lower().endswith(".md") and n.startswith(tops)]
    items = []
    for name in sorted(names):
        path = root / name
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for start_line, raw in iter_paragraphs(text):
            items.append({"path": name, "line": start_line, "raw": raw})
    return items


def find_pairs(items, threshold):
    """Pairs above threshold, highest first. Silent on every doubt."""
    prepared = []
    for item in items:
        norm = normalise(item["raw"])
        if len(norm.split()) < MIN_WORDS or is_dead(item["raw"]):
            continue
        prepared.append(dict(item, norm=norm))
    findings = []
    for i, a in enumerate(prepared):
        la = len(a["norm"])
        for b in prepared[i + 1:]:
            lb = len(b["norm"])
            if is_dead(b["raw"]):
                continue
            if 2.0 * min(la, lb) / (la + lb) < threshold:
                continue
            sim = difflib.SequenceMatcher(
                None, a["norm"], b["norm"], autojunk=False).ratio()
            if sim < threshold:
                continue
            first, second = (a, b) if la <= lb else (b, a)
            excerpt = " ".join(first["raw"].split())[:EXCERPT_CHARS]
            findings.append({
                "similarity": round(sim, 2),
                "path_a": a["path"], "line_a": a["line"],
                "path_b": b["path"], "line_b": b["line"],
                "excerpt": excerpt,
                "full_excerpt": " ".join(first["raw"].split()),
            })
    findings.sort(key=lambda f: (-f["similarity"], f["path_a"], f["line_a"],
                                 f["path_b"], f["line_b"]))
    return findings


def render_toon(report):
    """The report as TOON, encoded by the one sanctioned encoder.

    CONVERTED 2026-09-16, and this tool was the WORST case of the hand-built
    writers rather than a hypothetical one: `excerpt` is arbitrary prose lifted
    out of the repo, so a paragraph containing a comma split its own tabular
    row, and every column after it shifted. Nothing could have noticed, because
    nothing ever read these files back.

    `paths` also stops being a comma-joined STRING and becomes a real array,
    which is what it always was.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from toon import encode
    return encode({
        "produced_at": report["produced_at"],
        "paths": list(report["paths"]),
        "threshold": report["threshold"],
        "paragraphs_compared": report["compared"],
        "total_findings": len(report["findings"]),
        "findings": [{"similarity": f["similarity"],
                      "path_a": f["path_a"], "line_a": f["line_a"],
                      "path_b": f["path_b"], "line_b": f["line_b"],
                      "excerpt": f["excerpt"]}
                     for f in report["findings"]],
    }) + "\n"


def summarize(report):
    lines = ["find-duplicates: %d pair(s) >= %s over %s"
             % (len(report["findings"]), report["threshold"],
                ", ".join(report["paths"]))]
    for f in report["findings"]:
        lines.append("  %.2f  %s:%d  <->  %s:%d"
                     % (f["similarity"], f["path_a"], f["line_a"],
                        f["path_b"], f["line_b"]))
        lines.append('    "%s"' % f["full_excerpt"])
    return "\n".join(lines)


def write_artifact(report, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_toon(report), encoding="utf-8")
    return out


def run_scan(root, paths=None, threshold=DEFAULT_THRESHOLD):
    paths = list(paths) if paths else list(DEFAULT_PATHS)
    items = load_paragraphs(root, paths)
    return {
        "produced_at": utc_now(),
        "paths": paths,
        "threshold": threshold,
        "compared": len(items),
        "findings": find_pairs(items, threshold),
    }


def self_test():
    fixture = Path(tempfile.mkdtemp(prefix="find-duplicates-selftest-"))
    genuine = ("The nightly brief gathers queue state, lane status and spend "
               "into one artifact before any session starts reading, so the "
               "morning pass never opens three files to learn where we are.")
    claim = ("Driver sessions work exactly one card from the queue and stop "
             "when the card's gate command passes, leaving commit and landing "
             "to the launcher outside the lane.")
    fenced = ("run: docker compose -f C:\\stack\\compose.yaml up --build -d\n"
              "then: docker compose -f C:\\stack\\compose.yaml exec app\n"
              "migrate: python manage.py migrate --noinput every deploy")
    try:
        for sub in ("rules", "docs", "memory"):
            (fixture / sub).mkdir(parents=True)
        (fixture / "rules" / "genuine.md").write_text(
            "# Genuine source\n\n%s\n" % genuine, encoding="utf-8")
        (fixture / "docs" / "copy.md").write_text(
            "# Restated elsewhere\n\n%s\n" % genuine, encoding="utf-8")
        (fixture / "memory" / "claim.md").write_text(
            "# Live claim\n\n%s\n" % claim, encoding="utf-8")
        (fixture / "rules" / "old.md").write_text(
            "# Old copy, dated and explicitly dead\n\n%s Superseded "
            "2026-08-01, kept for provenance.\n" % claim, encoding="utf-8")
        (fixture / "docs" / "headings.md").write_text(
            "# One\n\n## Cost guard\n\nbody one\n\n# Two\n",
            encoding="utf-8")
        (fixture / "memory" / "headings2.md").write_text(
            "# Two\n\n## Cost guard\n\nbody two\n",
            encoding="utf-8")
        (fixture / "docs" / "fenced-a.md").write_text(
            "# Script a\n\n```\n%s\n```\n" % fenced, encoding="utf-8")
        (fixture / "memory" / "fenced-b.md").write_text(
            "# Script b\n\n~~~\n%s\n~~~\n" % fenced, encoding="utf-8")

        report = run_scan(fixture, threshold=DEFAULT_THRESHOLD)
        failures = []

        def expect(cond, label):
            if not cond:
                failures.append(label)

        findings = report["findings"]
        expect(len(findings) == 1,
               "expected exactly 1 finding, got %d" % len(findings))
        if findings:
            got = {findings[0]["path_a"], findings[0]["path_b"]}
            expect(got == {"rules/genuine.md", "docs/copy.md"},
                   "finding is the wrong pair: %s" % sorted(got))
            expect(findings[0]["similarity"] >= DEFAULT_THRESHOLD,
                   "genuine duplicate below threshold")
            expect(not findings[0]["path_a"].startswith(("memory/", "docs/f")),
                   "dead or fenced copy surfaced")
        expect(is_dead("%s Superseded 2026-08-01, kept for provenance."
                       % claim), "superseded marker not detected")
        artifact = render_toon(report)
        out = Path(fixture) / "state" / "routines" / "duplicate-facts.toon"
        write_artifact(report, out)
        expect(out.is_file(), "artifact not written")
        expect(artifact.startswith('produced_at: "'), "no produced_at")
        expect("findings[1]" in artifact, "artifact lost the finding")
        empty = render_toon({"produced_at": utc_now(), "paths": ["x"],
                             "threshold": 0.8, "compared": 0,
                             "findings": []})
        # REAL TOON WRITES AN EMPTY ARRAY AS `findings: []`, not `findings[0]:`.
        # This assertion used to demand the latter, which is what the
        # hand-built renderer emitted and what the reference implementation
        # does not produce. Corrected 2026-09-16 with the renderer.
        expect(empty.startswith("produced_at:") and "findings: []" in empty,
               "zero-finding artifact malformed")
        expect("total_findings: 0" in empty, "zero count not reported")

        if failures:
            print("SELF-TEST FAILED:")
            for f in failures:
                print("  - %s" % f)
            return 1
        print("self-test PASS: 1 genuine duplicate found; superseded copy, "
              "short heading and fenced repeat all stayed silent")
        return 0
    finally:
        shutil.rmtree(fixture, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paths", nargs="+", default=list(DEFAULT_PATHS),
                    metavar="DIR",
                    help="tracked directories to compare (default: %s)"
                         % " ".join(DEFAULT_PATHS))
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="similarity floor, 0..1 (default: %s)"
                         % DEFAULT_THRESHOLD)
    ap.add_argument("--root", help="repository root (default: this repo)")
    ap.add_argument("--out", help="artifact path (default: <root>/%s)"
                                  % ARTIFACT_REL)
    ap.add_argument("--self-test", action="store_true",
                    help="run the throwaway-fixture self-test and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    root = Path(args.root).resolve() if args.root \
        else Path(__file__).resolve().parents[1]
    report = run_scan(root, paths=args.paths, threshold=args.threshold)
    out = Path(args.out) if args.out else root / ARTIFACT_REL
    write_artifact(report, out)
    print(summarize(report))
    print("artifact: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
