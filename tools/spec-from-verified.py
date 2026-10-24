#!/usr/bin/env python3
"""Write an order's Deliverable FROM the files a session already verified.

    python tools/spec-from-verified.py --order queue/orders/<n>-<slug>.md \
        --verified <scratch worktree> --base <real checkout> \
        --whole api/x.py:python --whole templates/y.html:html \
        [--edits edits.json] [--intro intro.md]

WHY (2026-09-27). The order-cutting practice that lands is: make the change in
a scratch worktree, run the real tests there, then hand the driver the exact
text. Typing that text into the order by hand is where it breaks: order 167
went red twice on transcription faults (a markdown indent pasted into Python,
an edit block ending one line short), while order 168, generated from the
verified files, landed first try. So the text is never retyped: it is READ.

What it refuses, because each is a way an order cannot land:
  * a --whole file that does not exist in the verified copy
  * a file that contains a ``` fence (it would break the order's own fences)
  * an edit whose BEFORE is not EXACTLY ONCE in the real checkout
  * an edit whose AFTER is not in the verified copy (the order would describe
    a change nobody tested)

`--whole` means "write this file with exactly this content": new, or replacing
one small enough to give entire, which nano applies far more reliably than an
anchored edit. `--edits` is a JSON list of {"path", "lang", "before", "after",
"why"} for anchored edits in files too big to give whole.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

HEADER = (
    "**Every fenced block below is EXACT, indentation included.** Copy "
    "docstrings and comments too; do not rewrite them. A file marked WHOLE is "
    "written with exactly that content (create it, or replace what is there). "
    "An edit replaces its BEFORE text with its AFTER text and nothing else.\n\n"
    "**ONE FILE PER TOOL CALL.** Write or patch each file on its own, never "
    "several in one patch: measured 2026-09-27 on order 169, a four-file patch "
    "failed as a whole because one file did not exist yet, three files were "
    "silently never written, and the report claimed all four. After each file, "
    "Read it back before moving to the next."
)


# The longest AFTER block a driver is trusted to transcribe (order 173).
MAX_EDIT_LINES = 40

# ONE HOME for "what was verified", read back by tools/land-lane.py (exit 14).
# Measured 2026-09-27, order 177: nano landed pond.py with an existing message
# paraphrased and a method moved, the gate was green, and only a hand diff
# against the verified copy noticed. Each covered file's hash goes in the spec
# as one machine line the lander matches, so a paraphrase is a refusal.
VERIFIED_RE = re.compile(r"^<!-- verified-sha256 (\S+) ([0-9a-f]{64}) -->$", re.M)
# A file the verified copy DELETED: it must be absent from the lane too.
ABSENT_RE = re.compile(r"^<!-- verified-absent (\S+) -->$", re.M)
_DELETE_RE = re.compile(r"^### `([^`]+)`: DELETE$", re.M)


def norm_sha(data: bytes) -> str:
    """sha256 of file text with CRLF folded and trailing whitespace dropped, so
    a Windows checkout or a missing final newline is not a difference."""
    import hashlib
    text = data.decode("utf-8", "replace").replace("\r\n", "\n").rstrip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Refused(Exception):
    """The spec cannot be built honestly; the message says why."""


def build(verified: Path, base: Path, whole: list[tuple[str, str]],
          edits: list[dict], intro: str, deletes=()) -> str:
    out = ["## Deliverable", "", intro.strip(), "", HEADER, ""]
    for rel in deletes:
        if (verified / rel).exists():
            raise Refused(f"--delete {rel}: the verified copy still has it")
        if not (base / rel).exists():
            raise Refused(f"--delete {rel}: not in the real checkout, nothing to delete")
        out += [f"### `{rel}`: DELETE", "",
                f"Delete this file, exactly this command in your lane: `git -C . rm {rel}`", ""]
    for rel, lang in whole:
        src = verified / rel
        if not src.is_file():
            raise Refused(f"--whole {rel}: not in the verified copy {verified}")
        body = src.read_text(encoding="utf-8").rstrip("\n")
        if "```" in body:
            raise Refused(f"--whole {rel}: contains a ``` fence")
        # A driver once "patched" existing files whole and changed nothing
        # (order 169), which is why this was refused. Generated orders are
        # applied by `--apply` with no model since 2026-09-27, so an
        # existing file may be replaced whole; the hash still proves it.
        verb = ("replaces the existing file, WHOLE" if (base / rel).is_file()
                else "new file, WHOLE")
        out += [f"### `{rel}`: {verb}", "", f"```{lang}", body, "```", ""]
    for i, e in enumerate(edits, 1):
        rel = e["path"]
        # A PROSE EDIT IS ALLOWED AGAIN, 2026-10-02. It was refused because nano
        # could not retype doc edits (order 176: a ``` block and a \"\$VAR\"
        # escape). That reason died the same day: a generated order is applied
        # by `--apply`, literally, with no model. Keeping the refusal blocked
        # order 195, whose gate reads a row in docs/naming-sheet.md, so the
        # code could not land without the doc it is tested against.
        # Superseded, dead: "PROSE IS THE SESSION'S, NOT A DRIVER'S ... Land the
        # code; edit the doc by hand."
        base_text = (base / rel).read_text(encoding="utf-8")
        n = base_text.count(e["before"])
        if n != 1:
            raise Refused(f"edit {i} {rel}: BEFORE occurs {n} times in {base}")
        if e["after"] not in (verified / rel).read_text(encoding="utf-8"):
            raise Refused(f"edit {i} {rel}: AFTER is not in the verified copy")
        # No AFTER length limit any more: the limit existed because a driver
        # mis-transcribed a 90-line AFTER (order 173), and a generated order is
            # applied by `--apply` with no model since 2026-09-27.
        lang = e.get("lang", "")
        # An edit to a markdown doc can itself hold a ``` block, which would
        # close a ``` fence early (order 176). Fence with one more backtick
        # than the longest run inside, so the block stays exact.
        run = max((len(m) for m in re.findall(r"`+", e["before"] + "\n" + e["after"])), default=0)
        f = "`" * max(3, run + 1)
        out += [f"### Edit {i}, `{rel}`: {e.get('why', '')}", "", "BEFORE:", "",
                f"{f}{lang}", e["before"], f, "", "AFTER:", "",
                f"{f}{lang}", e["after"], f, ""]
    out += ["Touch nothing else.", ""]
    covered = list(dict.fromkeys([rel for rel, _ in whole] + [e["path"] for e in edits]))
    out += [f"<!-- verified-sha256 {rel} {norm_sha((verified / rel).read_bytes())} -->"
            for rel in covered]
    out += [f"<!-- verified-absent {rel} -->" for rel in deletes] + [""]
    return "\n".join(out)


def check_absent(text: str, root: Path) -> list[str]:
    """Files the spec deleted that still exist under `root`."""
    return [f"{rel}: should be deleted and is still there"
            for rel in ABSENT_RE.findall(text) if (root / rel).exists()]


def diff_edits(verified: Path, base: Path, rel: str, lang: str,
               context: int = 2) -> list[dict]:
    """Anchored edits for one EXISTING file, computed from base -> verified.

    Each changed region becomes one BEFORE/AFTER pair with `context` unchanged
    lines either side, widened until the BEFORE occurs exactly once in the base,
    so nothing is typed and every anchor is unique by construction. Regions
    whose context would overlap are merged into one edit."""
    import difflib
    a = (base / rel).read_text(encoding="utf-8").splitlines(keepends=True)
    b = (verified / rel).read_text(encoding="utf-8").splitlines(keepends=True)
    ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False)
           .get_opcodes() if o[0] != "equal"]
    spans: list[list[int]] = []
    for _tag, i1, i2, j1, j2 in ops:
        if spans and i1 - spans[-1][1] <= 2 * context:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    edits, whole_a = [], "".join(a)
    for n, (i1, i2, j1, j2) in enumerate(spans, 1):
        c = context
        while True:
            lo, hi = max(0, i1 - c), min(len(a), i2 + c)
            before = "".join(a[lo:hi])
            if whole_a.count(before) == 1 or (lo == 0 and hi == len(a)):
                break
            c += 1
        after = "".join(a[lo:i1] + b[j1:j2] + a[i2:hi])
        edits.append({"path": rel, "lang": lang, "before": before.rstrip("\n"),
                      "after": after.rstrip("\n"), "why": f"change {n} of {len(spans)}"})
    return edits


_WHOLE_RE = re.compile(r"^### `([^`]+)`: (?:new file|replaces the existing file), WHOLE"
                       r"\n\n(`{3,})[^\n]*\n(.*?)\n\2$",
                       re.M | re.S)
_EDIT_RE = re.compile(r"^### Edit \d+, `([^`]+)`:[^\n]*\n\nBEFORE:\n\n(`{3,})[^\n]*\n(.*?)\n\2"
                      r"\n\nAFTER:\n\n(`{3,})[^\n]*\n(.*?)\n\4$", re.M | re.S)


def parse_spec(text: str) -> tuple[dict, list]:
    """(whole files, edits) read back from a spec THIS tool wrote. The self-test
    round-trips build() through it, so the reader cannot drift from the writer."""
    whole = {m.group(1): m.group(3) for m in _WHOLE_RE.finditer(text)}
    edits = [{"path": m.group(1), "before": m.group(3), "after": m.group(5)}
             for m in _EDIT_RE.finditer(text)]
    return whole, edits


def apply_spec(text: str, root: Path) -> list[str]:
    """Apply a spec to a checkout with no model: write each WHOLE file, replace
    each BEFORE with its AFTER (skipped when the AFTER is already there, so a
    half-done lane is repaired rather than doubled), then check every recorded
    hash. Returns the problems; [] means every file matches its verified copy."""
    whole, edits = parse_spec(text)
    problems = []
    for rel in _DELETE_RE.findall(text):
        if (root / rel).is_file():
            (root / rel).unlink()
    for rel, body in whole.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body + "\n", encoding="utf-8")
    for i, e in enumerate(edits, 1):
        path = root / e["path"]
        cur = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else ""
        # DONE means the AFTER is there AND the BEFORE is not, unless the BEFORE
        # lives inside the AFTER (an append). Order 182, 2026-09-27, found both
        # halves: "AFTER is there" alone is true of every untouched DELETION
        # (its AFTER sits inside its BEFORE), so they were all skipped; and
        # "BEFORE is there" alone is true of every applied APPEND, so a re-run
        # appended twice.
        done = e["after"] in cur and (e["before"] not in cur or e["before"] in e["after"])
        if done:
            continue
        if cur.count(e["before"]) == 1:
            path.write_text(cur.replace(e["before"], e["after"], 1), encoding="utf-8")
        else:
            problems.append(f"{e['path']}: edit {i} has no unique BEFORE to replace")
    for rel, sha in VERIFIED_RE.findall(text):
        path = root / rel
        if not path.is_file() or norm_sha(path.read_bytes()) != sha:
            problems.append(f"{rel}: differs from the verified copy")
    return problems + check_absent(text, root)


def splice(order_text: str, deliverable: str) -> str:
    """Replace the order's Deliverable section, keeping everything else."""
    start = order_text.index("## Deliverable")
    end = order_text.index("## Gate")
    return order_text[:start] + deliverable + "\n" + order_text[end:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spec-from-verified")
    ap.add_argument("--order")
    ap.add_argument("--verified")
    ap.add_argument("--base")
    ap.add_argument("--whole", action="append", default=[],
                    help="REL:LANG, repeatable")
    ap.add_argument("--edits", help="JSON file of anchored edits")
    ap.add_argument("--diff", action="append", default=[],
                    help="REL:LANG of an EXISTING file: edits computed from "
                         "the base->verified difference, repeatable")
    ap.add_argument("--intro", help="markdown file: what and why, first")
    ap.add_argument("--delete", action="append", default=[],
                    help="REL of a file the verified copy deleted, repeatable")
    ap.add_argument("--apply", metavar="ORDER",
                    help="apply ORDER's spec to --to with no model, then check hashes")
    ap.add_argument("--to", help="the checkout or lane --apply writes into")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.apply:
        if not a.to:
            ap.error("--apply needs --to <checkout or lane>")
        problems = apply_spec(Path(a.apply).read_text(encoding="utf-8"), Path(a.to))
        for p in problems:
            print(f"spec-from-verified: NOT AS VERIFIED: {p}", file=sys.stderr)
        print(f"spec-from-verified: applied {Path(a.apply).name} to {a.to}: "
              + ("every file matches its verified copy" if not problems
                 else f"{len(problems)} problem(s)"))
        return 1 if problems else 0
    if not (a.order and a.verified and a.base):
        ap.error("--order, --verified and --base are required")
    whole = [tuple(w.rsplit(":", 1)) if ":" in w else (w, "") for w in a.whole]
    edits = json.loads(Path(a.edits).read_text(encoding="utf-8")) if a.edits else []
    intro = Path(a.intro).read_text(encoding="utf-8") if a.intro else ""
    order = Path(a.order)
    try:
        for d in a.diff:
            rel, _, lang = d.partition(":")
            edits += diff_edits(Path(a.verified), Path(a.base), rel, lang)
        spec = build(Path(a.verified), Path(a.base), whole, edits, intro, a.delete)
        order.write_text(splice(order.read_text(encoding="utf-8"), spec),
                         encoding="utf-8")
    except (Refused, ValueError, OSError) as exc:
        print(f"spec-from-verified: REFUSED: {exc}. The order was NOT changed.",
              file=sys.stderr)
        return 1
    print(f"spec-from-verified: wrote {order.name}: {len(whole)} whole file(s), "
          f"{len(edits)} edit(s)")
    return 0


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    with tempfile.TemporaryDirectory(prefix="spec-verified-") as td:
        v, b = Path(td) / "verified", Path(td) / "base"
        for d in (v, b):
            (d / "pkg").mkdir(parents=True)
        (v / "pkg" / "new.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (b / "pkg" / "main.py").write_text("import a\nrun(a)\n", encoding="utf-8")
        (v / "pkg" / "main.py").write_text("import a, b\nrun(a)\n", encoding="utf-8")
        edit = {"path": "pkg/main.py", "lang": "python", "before": "import a\n",
                "after": "import a, b\n", "why": "import b"}

        spec = build(v, b, [("pkg/new.py", "python")], [edit], "Why this.")
        want("a whole file is copied byte for byte, indentation included",
             "```python\ndef f():\n    return 1\n```" in spec)
        want("a file absent from the base is marked new",
             "`pkg/new.py`: new file, WHOLE" in spec)
        want("every spec tells the driver one file per tool call",
             "ONE FILE PER TOOL CALL" in spec)
        want("an edit carries its exact BEFORE and AFTER",
             "BEFORE:\n\n```python\nimport a\n\n```" in spec
             and "import a, b" in spec)

        def refused(fn):
            try:
                fn()
            except Refused:
                return True
            return False

        want("a whole file missing from the verified copy is refused",
             refused(lambda: build(v, b, [("pkg/gone.py", "python")], [], "")))
        (v / "pkg" / "fenced.md").write_text("```x```\n", encoding="utf-8")
        want("a file containing a fence is refused",
             refused(lambda: build(v, b, [("pkg/fenced.md", "")], [], "")))
        repl = build(v, b, [("pkg/main.py", "python")], [], "")
        want("a WHOLE replacement of an existing file is written as one, since no "
             "driver applies it any more",
             "`pkg/main.py`: replaces the existing file, WHOLE" in repl
             and parse_spec(repl)[0].get("pkg/main.py") == "import a, b\nrun(a)")
        want("a BEFORE that is not in the real checkout is refused",
             refused(lambda: build(v, b, [], [{**edit, "before": "nope\n"}], "")))
        (b / "pkg" / "dup.py").write_text("x\nx\n", encoding="utf-8")
        (v / "pkg" / "dup.py").write_text("y\nx\n", encoding="utf-8")
        want("a BEFORE that occurs twice is refused, it is not an anchor",
             refused(lambda: build(v, b, [], [{"path": "pkg/dup.py", "before": "x\n",
                                                "after": "y\n"}], "")))
        want("an AFTER nobody tested is refused",
             refused(lambda: build(v, b, [], [{**edit, "after": "import z\n"}], "")))
        big = "".join(f"x{i} = {i}\n" for i in range(MAX_EDIT_LINES + 1))
        (b / "pkg" / "big.py").write_text("anchor = 0\n", encoding="utf-8")
        (v / "pkg" / "big.py").write_text("anchor = 0\n" + big, encoding="utf-8")
        want("a long AFTER is accepted, since no driver transcribes it any more",
             not refused(lambda: build(v, b, [], [{"path": "pkg/big.py",
                                                    "before": "anchor = 0",
                                                    "after": "anchor = 0\n" + big.rstrip("\n")}], "")))

        (b / "pkg" / "long.py").write_text(
            "".join(f"line{i}\n" for i in range(20)), encoding="utf-8")
        (v / "pkg" / "long.py").write_text(
            "".join(("NEW\n" if i == 3 else "") + f"line{i}\n" for i in range(20))
            .replace("line15\n", "CHANGED\n"), encoding="utf-8")
        got = diff_edits(v, b, "pkg/long.py", "python")
        want("two distant changes become two anchored edits", len(got) == 2)
        applied = (b / "pkg" / "long.py").read_text(encoding="utf-8")
        for e in got:
            applied = applied.replace(e["before"], e["after"], 1)
        want("applying the computed edits to the base reproduces the verified file",
             applied == (v / "pkg" / "long.py").read_text(encoding="utf-8"))
        want("every computed BEFORE is unique in the base",
             all(applied is not None and
                 (b / "pkg" / "long.py").read_text(encoding="utf-8").count(e["before"]) == 1
                 for e in got))
        (b / "pkg" / "rep.py").write_text("x\ny\nx\ny\nz\n", encoding="utf-8")
        (v / "pkg" / "rep.py").write_text("x\ny\nx\ny\nZ\n", encoding="utf-8")
        rep = diff_edits(v, b, "pkg/rep.py", "", context=1)
        want("context widens until a repeated neighbourhood is unique",
             (b / "pkg" / "rep.py").read_text(encoding="utf-8").count(rep[0]["before"]) == 1)

        # A DELETION-ONLY EDIT IS APPLIED. Its AFTER sits inside its BEFORE, and
        # apply_spec once took that for "already applied" (order 182).
        lane = b.parent / "lane"
        (lane / "pkg").mkdir(parents=True)
        (lane / "pkg" / "cut.py").write_text("keep = 1\n\n\ndef gone():\n    pass\n", encoding="utf-8")
        cut = ("### Edit 1, `pkg/cut.py`: change 1 of 1\n\nBEFORE:\n\n```python\n"
               "keep = 1\n\n\ndef gone():\n    pass\n```\n\nAFTER:\n\n```python\nkeep = 1\n```\n")
        apply_spec(cut, lane)
        want("a deletion-only edit is applied, not mistaken for done",
             (lane / "pkg" / "cut.py").read_text(encoding="utf-8") == "keep = 1\n")
        want("re-applying an applied edit is a no-op, not a problem",
             apply_spec(cut, lane) == [])
        # AN APPEND IS APPLIED ONCE. Its BEFORE survives inside its AFTER, and a
        # re-run once appended the block a second time (order 182).
        (lane / "pkg" / "add.sql").write_text("a;\nb;\n", encoding="utf-8")
        add = ("### Edit 1, `pkg/add.sql`: change 1 of 1\n\nBEFORE:\n\n```sql\na;\nb;\n```\n\n"
               "AFTER:\n\n```sql\na;\nb;\nc;\n```\n")
        apply_spec(add, lane)
        apply_spec(add, lane)
        want("an append re-applied is not appended twice",
             (lane / "pkg" / "add.sql").read_text(encoding="utf-8") == "a;\nb;\nc;\n")

        # MEASURED 2026-09-27 on order 176: an edit whose text held a ```bash
        # block was wrapped in a ``` fence, so the inner fence CLOSED the outer
        # one. A Python docstring can hold one too, so the fence still matters.
        (b / "pkg" / "doc.py").write_text('"""\n```bash\nold\n```\n"""\n', encoding="utf-8")
        (v / "pkg" / "doc.py").write_text('"""\n```bash\nnew\n```\n"""\n', encoding="utf-8")
        md = build(v, b, [], [{"path": "pkg/doc.py", "lang": "markdown",
                               "before": "```bash\nold\n```", "after": "```bash\nnew\n```"}], "")
        # THE SPEC RECORDS WHAT WAS VERIFIED, so the lander can refuse a lane that
        # differs (order 177, 2026-09-27: nano landed pond.py with an existing
        # message paraphrased, green gate, nothing noticed but a hand diff).
        marks = dict(VERIFIED_RE.findall(spec))
        want("every file the spec covers carries its verified hash",
             set(marks) == {"pkg/new.py", "pkg/main.py"})
        want("the hash is of the VERIFIED copy, line endings normalised",
             marks.get("pkg/main.py") == norm_sha(b"import a, b\r\nrun(a)\r\n\r\n"))
        want("a paraphrased file hashes differently",
             norm_sha(b"import a,b\nrun(a)\n") != marks.get("pkg/main.py"))
        (b / "pkg" / "notes.md").write_text("old\n", encoding="utf-8")
        (v / "pkg" / "notes.md").write_text("new\n", encoding="utf-8")
        want("an edit to a prose file is NOT refused: --apply needs no model "
             "(order 195, 2026-10-02; order 176's reason is dead)",
             not refused(lambda: build(v, b, [], [{"path": "pkg/notes.md",
                                                    "before": "old", "after": "new"}], "")))
        (b / "pkg" / "requirements.txt").write_text("a>=1\nold>=2\n", encoding="utf-8")
        (v / "pkg" / "requirements.txt").write_text("a>=1\n", encoding="utf-8")
        want("an edit to a requirements file is code, and is not refused (order 183)",
             not refused(lambda: build(v, b, [], [{"path": "pkg/requirements.txt",
                                                    "before": "a>=1\nold>=2",
                                                    "after": "a>=1"}], "")))
        want("an edit whose text holds a ``` fence is wrapped in a LONGER fence",
             "\n````markdown\n```bash\nold\n```\n````\n" in md
             and "\n````markdown\n```bash\nnew\n```\n````\n" in md)
        want("the fence is ONE backtick longer, not the BEFORE and AFTER runs "
             "glued together (a first cut emitted seven)", "`````" not in md)
        want("a plain edit keeps the ordinary three-backtick fence",
             "````" not in spec)

        # --apply: THE SPEC CAN BE APPLIED WITHOUT A MODEL (2026-09-27). Two lanes
        # that night were repaired by hand (177 paraphrased, 178 dropped lines);
        # this is that repair as a command. Round trip through the tool's own
        # output, so the parser can never drift from the writer.
        full = build(v, b, [("pkg/new.py", "python")],
                     [edit] + diff_edits(v, b, "pkg/long.py", "python"), "Why.")
        got_whole, got_edits = parse_spec(full)
        want("parse_spec reads back every WHOLE file and every edit it wrote",
             set(got_whole) == {"pkg/new.py"} and len(got_edits) == 3)
        tgt = Path(td) / "target"
        (tgt / "pkg").mkdir(parents=True)
        for rel in ("pkg/main.py", "pkg/long.py"):
            (tgt / rel).write_text((b / rel).read_text(encoding="utf-8"), encoding="utf-8")
        first = apply_spec(full, tgt)
        want("applying the spec to the base reproduces every verified file",
             first == [] and all(norm_sha((tgt / r).read_bytes())
                                 == norm_sha((v / r).read_bytes())
                                 for r in ("pkg/new.py", "pkg/main.py", "pkg/long.py")))
        want("applying it again is a no-op, so it can repair a half-done lane",
             apply_spec(full, tgt) == [])
        (tgt / "pkg" / "main.py").write_text("import zzz\nrun(a)\n", encoding="utf-8")
        want("a file it cannot bring to the verified copy is NAMED, never skipped",
             any("pkg/main.py" in p for p in apply_spec(full, tgt)))

        # --delete: a file the verified copy no longer has (MySQL retirement,
        # 2026-09-27). Refused when the verified copy STILL has it.
        (b / "pkg" / "old.sql").write_text("x\n", encoding="utf-8")
        gone = build(v, b, [], [], "", deletes=["pkg/old.sql"])
        want("a deletion is written as its own entry and an absent marker",
             "### `pkg/old.sql`: DELETE" in gone
             and "<!-- verified-absent pkg/old.sql -->" in gone)
        want("a deletion the verified copy did not make is refused",
             refused(lambda: build(v, b, [], [], "", deletes=["pkg/main.py"])))
        (tgt / "pkg" / "old.sql").write_text("x\n", encoding="utf-8")
        want("applying a deletion removes the file",
             apply_spec(gone, tgt) == [] and not (tgt / "pkg" / "old.sql").exists())
        (tgt / "pkg" / "old.sql").write_text("x\n", encoding="utf-8")
        want("and a file that should be absent but is not is NAMED",
             any("pkg/old.sql" in p for p in check_absent(gone, tgt)))

        order = Path(td) / "1-x.md"
        order.write_text("---\nstatus: draft\n---\n# O\n\n## Deliverable\n\nPLACEHOLDER\n\n"
                         "## Gate\n\ngate\n", encoding="utf-8")
        rc = main(["--order", str(order), "--verified", str(v), "--base", str(b),
                   "--whole", "pkg/new.py:python"])
        text = order.read_text(encoding="utf-8")
        want("the CLI splices the spec in and keeps the frontmatter and gate",
             rc == 0 and "PLACEHOLDER" not in text and text.startswith("---\nstatus")
             and "## Gate\n\ngate" in text)
        before = order.read_text(encoding="utf-8")
        rc = main(["--order", str(order), "--verified", str(v), "--base", str(b),
                   "--whole", "pkg/gone.py:python"])
        want("a refusal exits 1 and leaves the order untouched",
             rc == 1 and order.read_text(encoding="utf-8") == before)

    print("\n%d check(s), %d failure(s)" % (checks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
