#!/usr/bin/env python3
"""toolbox.py: the index of every tool, generated from the tools themselves.

    python tools/toolbox.py                 print the index
    python tools/toolbox.py --write         write it to tools/README.md
    python tools/toolbox.py --check         is tools/README.md current?
    python tools/toolbox.py --find lane     only tools whose name or purpose matches
    python tools/toolbox.py --self-test

WHY THIS EXISTS (2026-09-16): "your tools needs HUGE organization ... make
sure to add the tool index for faster matching too."

**THE ORGANIZATION IS AN INDEX, NOT DIRECTORIES.** Some repository
configurations bind scripts by path, so moving a script can silently break its
configuration. An index makes a flat tool directory easier to navigate without
changing those paths.

**GENERATED, SO IT CANNOT DRIFT.** A hand-written index becomes stale over
time. The purpose line comes from each tool's own first docstring line, the
house convention being `\"\"\"name.py: what it does.\"\"\"`. `--check` fails when
the file on disk disagrees, so a new tool that skips the index need not go
unlisted.

GROUPS COME FROM THE FILENAME PREFIX, never a hand-kept map: `lane-*` are lane
operations, `audit-*` are audits, `check-*` are gates, and so on. A map would be
one more thing to forget to update.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
README = HERE / "README.md"

SUFFIXES = (".py", ".ps1", ".sh", ".cmd")

# A prefix becomes a group only when several tools share it, so a lone
# `sieve.py` does not invent a section of one. The label is what a session is
# actually looking for, in plain words.
GROUPS = {
    "lane": "Lanes and worktrees",
    "audit": "Audits and surveys",
    "check": "Gates and checkers",
    "queue": "The queue",
    "driver": "Drivers",
    "bench": "Benchmarking",
    "usage": "Cost and usage",
    "model": "Models",
    "log": "Logs",
    "store": "The shared store",
    "sync": "Syncing",
    "go": "The launcher",
}
OTHER = "Everything else"

DOCLINE = re.compile(r'^\s*(?:"""|\'\'\')\s*(.+?)\s*(?:"""|\'\'\')?\s*$')
COMMENTLINE = re.compile(r"^\s*(?:#|REM|<#)\s*(.+?)\s*$")


def purpose(path: Path) -> str:
    """The tool's own one-line description, or a plain admission of none.

    Read from the FIRST docstring line for Python and the first comment line
    for a shell or cmd script. **Never invented**: a tool with no description
    is reported as having none, because a generated index that guesses is worse
    than one that shows a gap (`rules/shared/memory-integrity.md` rule 2).
    """
    try:
        lines = path.read_text(encoding="utf-8",
                               errors="replace").split("\n")[:12]
    except OSError:
        return "(could not read)"
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#!") or line.startswith("from __future__"):
            continue
        if path.suffix == ".py":
            m = DOCLINE.match(line)
            if m:
                text = m.group(1)
                # The house style is `name.py: what it does`; drop the name so
                # the column carries only the purpose.
                return text.split(":", 1)[1].strip() if ":" in text else text
            if line.startswith(("import ", "\"\"\"", "'''")):
                continue
            return "(no docstring)"
        m = COMMENTLINE.match(raw)
        if m and not m.group(1).startswith("-"):
            text = m.group(1)
            return text.split(":", 1)[1].strip() if ":" in text else text
    # ONE FALLBACK PER LANGUAGE, and they must not disagree. The first version
    # had two paths to "no description" and one to "no docstring", so a Python
    # file whose first statement was an import took the wrong one.
    return "(no docstring)" if path.suffix == ".py" else "(no description)"


def group_of(name: str, counts: dict[str, int]) -> str:
    prefix = name.split("-", 1)[0].split(".", 1)[0]
    if prefix in GROUPS and counts.get(prefix, 0) > 1:
        return GROUPS[prefix]
    return OTHER


def survey(tools_dir: Path) -> list[dict]:
    """Every tool, with its purpose, size and whether it self-tests."""
    files = sorted(p for p in tools_dir.iterdir()
                   if p.is_file() and p.suffix in SUFFIXES)
    counts: dict[str, int] = {}
    for p in files:
        counts[p.name.split("-", 1)[0].split(".", 1)[0]] = counts.get(
            p.name.split("-", 1)[0].split(".", 1)[0], 0) + 1
    out = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        out.append({
            "name": p.name,
            "purpose": purpose(p),
            "bytes": p.stat().st_size if p.exists() else 0,
            "self_test": "--self-test" in text,
            "group": group_of(p.name, counts),
        })
    return out


def render(rows: list[dict]) -> str:
    """The index as markdown. Pure, so it is tested without a filesystem."""
    body = [
        "# tools/",
        "",
        "**GENERATED by `python tools/toolbox.py --write`. Do not hand-edit:**",
        "Repository checks can compare this file with the directory, so",
        "an edit here is reverted by the next run. Change a tool's first",
        "docstring line to change its entry.",
        "",
        "Why an index and not subdirectories: repository configuration may",
        "refer to tools by path, so moving a script can break those references.",
        "The map is the fix, not the layout.",
        "",
        "`no self-test` is a finding, not a note: a missing check can hide a",
        "regression.",
        "",
    ]
    # DELIBERATELY NO BYTE TOTAL. The first version printed the directory's
    # size, which meant editing ANY tool by one character made the index stale
    # and turned the lint red. An index that churns on every edit is an index
    # nobody regenerates. Size is `tools/repo-weight.py`'s question, not this
    # one; this file answers "which tool do I want".
    untested = [r["name"] for r in rows if not r["self_test"]]
    body += [
        f"{len(rows)} tools, {len(rows) - len(untested)} with a self-test.",
        "",
    ]
    order = list(GROUPS.values()) + [OTHER]
    for label in order:
        group = [r for r in rows if r["group"] == label]
        if not group:
            continue
        body += [f"## {label}", "", "| tool | what it does | self-test |",
                 "|---|---|---|"]
        for r in sorted(group, key=lambda x: x["name"]):
            mark = "yes" if r["self_test"] else "**no**"
            body.append(f"| `{r['name']}` | {r['purpose']} | {mark} |")
        body.append("")
    return "\n".join(body).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="toolbox")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--find")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    rows = survey(HERE)
    if a.find:
        needle = a.find.lower()
        hits = [r for r in rows
                if needle in r["name"].lower()
                or needle in r["purpose"].lower()]
        if not hits:
            print(f"toolbox: nothing matches {a.find!r}")
            return 1
        for r in sorted(hits, key=lambda x: x["name"]):
            print(f"  {r['name']:<34} {r['purpose']}")
        return 0

    text = render(rows)
    if a.check:
        current = README.read_text(encoding="utf-8") if README.is_file() else ""
        if current == text:
            print(f"toolbox: tools/README.md lists all {len(rows)} tools and "
                  f"is current")
            return 0
        print("toolbox: tools/README.md is STALE. Run "
              "`python tools/toolbox.py --write`", file=sys.stderr)
        return 1
    if a.write:
        README.write_text(text, encoding="utf-8", newline="\n")
        print(f"toolbox: wrote tools/README.md, {len(rows)} tools")
        return 0
    print(text)
    return 0


def self_test() -> int:
    import tempfile
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "lane-one.py").write_text(
            '#!/usr/bin/env python3\n"""lane-one.py: does the first thing."""\n'
            'import sys\n--self-test\n', encoding="utf-8")
        (d / "lane-two.py").write_text(
            '"""lane-two.py: does the second thing."""\n', encoding="utf-8")
        (d / "sieve.py").write_text(
            '"""sieve.py: drops what is spent."""\n', encoding="utf-8")
        (d / "bare.py").write_text("import os\n", encoding="utf-8")
        (d / "drivers.cmd").write_text(
            "REM drivers.cmd: opens the view.\n", encoding="utf-8")
        (d / "notes.txt").write_text("ignored\n", encoding="utf-8")

        rows = survey(d)
        by = {r["name"]: r for r in rows}
        want("only tool suffixes are surveyed",
             set(by) == {"lane-one.py", "lane-two.py", "sieve.py", "bare.py",
                         "drivers.cmd"})
        want("the purpose comes from the docstring, with the name stripped",
             by["lane-one.py"]["purpose"] == "does the first thing.")
        want("a .cmd's purpose comes from its first REM line",
             by["drivers.cmd"]["purpose"] == "opens the view.")
        # NEVER INVENTED. Red-proven by returning the filename as a fallback:
        # a tool with no description then reads as documented.
        want("a tool with no docstring says so rather than being guessed at",
             by["bare.py"]["purpose"] == "(no docstring)")
        want("a self-test is detected", by["lane-one.py"]["self_test"] is True)
        want("and its absence is detected",
             by["lane-two.py"]["self_test"] is False)

        # A GROUP NEEDS MORE THAN ONE MEMBER, so a lone tool does not invent a
        # section of one.
        want("a shared prefix becomes a group",
             by["lane-one.py"]["group"] == GROUPS["lane"])
        want("a lone tool falls into the catch-all",
             by["sieve.py"]["group"] == OTHER)

        out = render(rows)
        want("the index says how many tools it lists", "5 tools" in out)
        want("it names a tool with no self-test in bold, as a finding",
             "| `lane-two.py` |" in out and "**no**" in out)
        want("it warns that it is generated", "GENERATED" in out)
        want("every surveyed tool appears",
             all(f"`{n}`" in out for n in by))
        # STABLE OUTPUT, or --check would report drift on every run.
        want("rendering twice gives the same bytes", render(survey(d)) == out)

    real_rows = survey(HERE)
    want("the real tools directory surveys without error",
         bool(real_rows) and any(r["name"] == Path(__file__).name
                                 for r in real_rows))

    if fails:
        print(f"\ntoolbox self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\ntoolbox self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
