#!/usr/bin/env python3
"""log-to-store.py: a prose log becomes one row per finding.

    python tools/log-to-store.py reports/personas/_LOG.md          what it would load
    python tools/log-to-store.py reports/personas/_LOG.md --apply  load it
    python tools/log-to-store.py --self-test

WHY THIS EXISTS (2026-09-16): the repo should hold mostly pointers into the
real stores, and "the thing at the end of the pointer must also be stored in the
most efficient means possible such that we can section ourselves off well."

THE FILE THAT FORCED IT: `reports/personas/_LOG.md`, 82 KB. A persona is told to
read it and the one-call read cap is about 65 KB, so **the instruction could not
be obeyed** and a session that tried spent the call on a partial file. The
knowledge was neither deleted nor usable. `docs/permission-incidents.md` is 68 KB
of the same shape.

AND IT IS A TOOL RATHER THAN A SESSION'S HANDS ON PURPOSE (golden rule A3, and
A11). Transcribing thirty sections by hand is a repeatable mechanical transform
done by the most expensive available actor, it drifts on every repeat, and
crucially it costs the session ~50k tokens of reading to do badly what a split
on `## ` does exactly.

IT NEVER DELETES THE SOURCE. Loading and deleting are separate acts, in that
order, so a bad load is not also a data loss. Deleting the prose afterwards is
the caller's, once the rows are verified.

A SLUG COLLISION REFUSES, loudly. Two sections mapping to one slug means one
would silently overwrite the other, and the one that lost would be gone with no
record that it existed.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# A FILE MUST OPT IN, by carrying this marker. Added 2026-09-16 within minutes
# of shipping the tool, because the failure happened immediately: `_LOG.md` was
# loaded, reduced to a POINTER file, and then the documented command was run
# again, which inserted the pointer file's own navigation headings ("How to use
# it", "Writing a new one") as three findings. Nothing objected.
#
# "Do not re-point the loader at a converted file" is a wish, not a mechanism
# (`rules/shared/memory-integrity.md`). The marker is the mechanism: a file that
# holds findings says so, and a file that has been converted loses the marker in
# the same edit, so the mistake cannot be repeated by following the old command.
LOADABLE_MARKER = "log-to-store: load"

SECTION = re.compile(r"^## +(.+?)\s*$")
# A trailing parenthetical carries the date and who wrote it:
#   "... (2026-09-13 evening, quarterback)"  or  "(2026-09-05)"
STAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})(?:[^,)]*)?(?:,\s*([a-z-]+))?\)")
# Some headings lead with the date instead: "## 2026-09-07 quarterback: ..."
LEADING = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+([a-z-]+)?:?\s*")


def slugify(title: str, limit: int = 60) -> str:
    """A stable key from a heading. Lowercase, underscores, no punctuation."""
    text = LEADING.sub("", title)
    text = STAMP.sub("", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text.lower()).strip("_")
    return text[:limit].rstrip("_")


def stamp_of(title: str) -> tuple[str | None, str | None]:
    """(date, persona) read out of the heading, or (None, None).

    NULL RATHER THAN A GUESS. An undated finding gets no date: inventing one
    from the file's mtime would be a logger stating an inference as a fact
    (golden rule 2).
    """
    m = LEADING.match(title)
    if m:
        return m.group(1), m.group(2)
    m = STAMP.search(title)
    if m:
        return m.group(1), m.group(2)
    return None, None


def sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(the preamble before the first heading, [(title, body)]).

    LEVEL 3 HEADINGS STAY INSIDE THEIR PARENT. They are sub-findings of the
    write-up above them, and splitting on them would strand a paragraph whose
    subject is a heading it no longer sits under.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    preamble: list[str] = []
    out: list[tuple[str, str]] = []
    title: str | None = None
    body: list[str] = []
    for line in lines:
        m = SECTION.match(line)
        if m:
            if title is not None:
                out.append((title, "\n".join(body).strip()))
            title = m.group(1)
            body = []
            continue
        (body if title is not None else preamble).append(line)
    if title is not None:
        out.append((title, "\n".join(body).strip()))
    return "\n".join(preamble).strip(), out


# A SECTION CONTAINER IS NOT A FINDING, and the tell is already in the data:
# every container has NO DATE in its heading, and every real finding has one.
# Size alone is not enough (a long finding is still a finding) and datelessness
# alone is not enough (a short undated note is still a note), so both must hold.
CONTAINER_BYTES = 4000


def rows_for(rel: str, text: str) -> tuple[list[dict], list[str]]:
    """(rows to upsert, refusals). Pure, so it is tested without a database."""
    _preamble, found = sections(text)
    rows: list[dict] = []
    refusals: list[str] = []
    seen: dict[str, str] = {}
    for title, body in found:
        slug = slugify(title)
        if not slug:
            refusals.append(f"a heading produced an empty slug: {title!r}")
            continue
        if not body:
            refusals.append(f"heading with no body, so there is nothing to "
                            f"store: {title!r}")
            continue
        if stamp_of(title)[0] is None and len(body) > CONTAINER_BYTES:
            refusals.append(f"REFUSED, {title!r} looks like a SECTION "
                            f"CONTAINER, not a finding: no date in the heading "
                            f"and {len(body)} bytes of body, over the "
                            f"{CONTAINER_BYTES}-byte limit. Split it into "
                            f"dated findings, or import it at a deeper "
                            f"heading level")
            continue
        if slug in seen:
            refusals.append(f"REFUSED, two headings map to the slug {slug!r}: "
                            f"{seen[slug]!r} and {title!r}. One would silently "
                            f"overwrite the other")
            continue
        seen[slug] = title
        occurred, persona = stamp_of(title)
        rows.append({"slug": slug, "title": title, "body": body,
                     "occurred_on": occurred, "persona": persona,
                     "source_file": rel})
    return rows, refusals


def _memdb():
    spec = importlib.util.spec_from_file_location("memdb", HERE / "memdb.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def resolve_in_repo(raw: str) -> Path:
    """A relative path means REPO-relative, never cwd-relative.

    A session's working directory here is `Documents/ai`, one level ABOVE the
    repo, so a bare `reports/personas/_LOG.md` resolves to a path that does not
    exist. The store already carries this as a fact
    (`store:facts/tools/no_relative_default_paths`): a tool whose path argument
    is cwd-relative answers a different question depending on where it is run.
    """
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (REPO / p).resolve()


def run(path: Path, apply: bool) -> int:
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        print(f"log-to-store: {path} is outside {REPO}, so it has no "
              f"repo-relative name to record", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"log-to-store: no such file: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    if LOADABLE_MARKER not in text:
        print(f"log-to-store: {rel} does not carry the marker "
              f"`{LOADABLE_MARKER}`, so it is not a findings log. Add the "
              f"marker deliberately, or you are about to store some other "
              f"file's headings as findings.", file=sys.stderr)
        return 2
    rows, refusals = rows_for(rel, text)
    for line in refusals:
        print(f"  {line}", file=sys.stderr)
    dated = sum(1 for r in rows if r["occurred_on"])
    print(f"  {rel}: {len(rows)} finding(s), {dated} dated, "
          f"{len(refusals)} refused")
    if not apply:
        for r in rows[:5]:
            print(f"    {r['occurred_on'] or '(undated)'}  {r['slug']}")
        if len(rows) > 5:
            print(f"    ... and {len(rows) - 5} more")
        print("\nnothing written; re-run with --apply")
        return 1 if refusals else 0

    memdb = _memdb()
    written = 0
    for r in rows:
        try:
            # A SLUG COLLISION ACROSS FILES MUST NOT OVERWRITE, and this is the
            # half the in-file check cannot see. Found 2026-09-16 before loading
            # a second file: headings like "## The answer" are generic, the
            # upsert keys on slug alone, so the second file's row would have
            # silently replaced the first file's and nothing would record it.
            # Re-loading the SAME file is still an upsert, which is what makes
            # "append here then re-run" safe.
            owner = memdb._query(
                "select source_file from findings where slug = %s",
                (r["slug"],))
            if owner and str(owner[0][0]) != r["source_file"]:
                print(f"  REFUSED {r['slug']}: already loaded from "
                      f"{owner[0][0]}. Two files claim one slug; rename the "
                      f"heading rather than overwriting", file=sys.stderr)
                refusals.append(r["slug"])
                continue
            memdb._query(
                "insert into findings (slug, title, body, occurred_on, "
                "persona, source_file) values (%s, %s, %s, %s, %s, %s) "
                "on conflict (slug) do update set title = excluded.title, "
                "body = excluded.body, occurred_on = excluded.occurred_on, "
                "persona = excluded.persona, source_file = excluded.source_file",
                (r["slug"], r["title"], r["body"], r["occurred_on"],
                 r["persona"], r["source_file"]), write=True)
            written += 1
        except Exception as exc:
            print(f"  FAILED {r['slug']}: {exc}", file=sys.stderr)
    print(f"\n{written} of {len(rows)} finding(s) written")
    return 0 if written == len(rows) and not refusals else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="log-to-store")
    ap.add_argument("path", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.path:
        ap.error("give the markdown log to load")
    return run(resolve_in_repo(a.path), a.apply)


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    want("a trailing stamp is stripped out of the slug",
         slugify("A GATE THAT LIES (2026-09-13 evening, quarterback)")
         == "a_gate_that_lies")
    want("a leading stamp is stripped too, because both spellings exist",
         slugify("2026-09-07 quarterback: THIRD INSTANCE, a suite that cannot fail")
         == "third_instance_a_suite_that_cannot_fail")
    want("the date and persona come out of a trailing stamp",
         stamp_of("A GATE (2026-09-13 evening, quarterback)")
         == ("2026-09-13", "quarterback"))
    want("and out of a leading one",
         stamp_of("2026-09-07 quarterback: THIRD INSTANCE")
         == ("2026-09-07", "quarterback"))
    # NULL RATHER THAN A GUESS. Red-proven by defaulting the date to today: an
    # undated finding then sorts as the newest thing in the table forever.
    want("an undated heading yields NO date, never an invented one",
         stamp_of("Traps in the ExampleProject codebase") == (None, None))

    doc = ("# Title\n\npreamble line\n\n"
           "## FIRST FINDING (2026-09-01)\n\nbody one\n\n"
           "### a sub-finding\n\nstill finding one\n\n"
           "## SECOND FINDING (2026-09-02)\n\nbody two\n")
    pre, found = sections(doc)
    want("the preamble before the first heading is not a finding",
         "preamble line" in pre and len(found) == 2)
    # A LEVEL-3 HEADING IS PART OF ITS PARENT. Red-proven by splitting on
    # `#{2,3}`: "still finding one" becomes a finding of its own whose subject
    # is a heading it no longer sits under.
    want("a level-3 heading stays inside its parent finding",
         "a sub-finding" in found[0][1] and "still finding one" in found[0][1])
    want("bodies do not bleed into each other",
         "body two" not in found[0][1] and "body one" not in found[1][1])

    rows, refusals = rows_for("x.md", doc)
    want("every section becomes a row with its date",
         len(rows) == 2 and rows[0]["occurred_on"] == "2026-09-01"
         and not refusals)
    want("and the source file rides along, so a row knows where it came from",
         all(r["source_file"] == "x.md" for r in rows))

    # A COLLISION REFUSES. Red-proven by dropping the `seen` check: the second
    # section overwrites the first and nothing records that it existed.
    dup = ("## SAME NAME (2026-09-01)\n\none\n\n"
           "## SAME NAME (2026-09-02)\n\ntwo\n")
    rows, refusals = rows_for("x.md", dup)
    want("two headings with one slug REFUSE instead of overwriting",
         len(rows) == 1 and len(refusals) == 1
         and "REFUSED" in refusals[0])

    empty = "## A HEADING WITH NOTHING UNDER IT (2026-09-01)\n\n"
    rows, refusals = rows_for("x.md", empty)
    big = "x" * (CONTAINER_BYTES + 1)
    container_rows, container_refusals = rows_for(
        "f.md", f"## Live findings, lesson not yet a mechanism\n\n{big}\n")
    want("an undated section with a huge body is REFUSED as a container",
         container_rows == []
         and any("SECTION CONTAINER" in r for r in container_refusals))
    want("and the refusal names the byte size, so the limit is checkable",
         any(str(CONTAINER_BYTES + 1) in r for r in container_refusals))
    dated_rows, _dated_refusals = rows_for(
        "f.md", f"## 2026-09-07 quarterback: a real long finding\n\n{big}\n")
    want("a DATED section of the same size is kept, because size alone is not "
         "the test", len(dated_rows) == 1)
    short_rows, _short_refusals = rows_for(
        "f.md", "## An undated short note\n\nbody\n")
    want("and an UNDATED SHORT section is kept, because datelessness alone is "
         "not the test either", len(short_rows) == 1)
    # RESTORED 2026-09-20. Order 131's driver DELETED the assertion below and
    # left a comment calling it a duplicate tail. It was not a duplicate: it is
    # the only check that an empty section is refused rather than stored, and
    # nothing else covers it. The gate could not catch the deletion because the
    # gate counts passing assertions and the count still ROSE (15 -> 18), just
    # by less than the order demanded. The cause is order-cutting, not the
    # model: the order's AFTER block ENDED on the anchor line, so the driver
    # treated the anchor as text to replace. An anchor is never re-emitted at
    # the END of a replacement block.
    want("a heading with no body is refused, not stored as an empty finding",
         rows == [] and len(refusals) == 1
         and "nothing to store" in refusals[0])

    want("a file with no level-2 headings produces no rows and no crash",
         rows_for("x.md", "# just a title\n\nsome prose\n") == ([], []))

    # A RELATIVE PATH IS REPO-RELATIVE, NEVER cwd-RELATIVE. Red-proven by
    # reverting to `Path(raw).resolve()`: the documented invocation
    # `tools/log-to-store.py reports/personas/_LOG.md` then dies, because a
    # session here runs from one level ABOVE the repo.
    want("a relative path resolves against the repo, not the session's cwd",
         resolve_in_repo("reports/personas/_LOG.md")
         == (REPO / "reports" / "personas" / "_LOG.md").resolve())
    want("and an absolute path is left exactly as given",
         resolve_in_repo(str(REPO / "README.md"))
         == (REPO / "README.md").resolve())

    if fails:
        print(f"\nlog-to-store self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\nlog-to-store self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
