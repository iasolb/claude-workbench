#!/usr/bin/env python3
"""Cut a generated order from a verified scratch worktree, file list included.

    python tools/order-from-worktree.py --verified <scratch worktree> --base <real checkout> \
        --slug <slug> --title "<title>" --name "<plain words>" --gate "<gate>" \
        --intro <intro.md> --repo <owner>/<repo> [--commands a,b]

WHY (A3, second occurrence, 2026-09-27). The no-driver flow is: build and test
the change in a scratch worktree, then `new-order.py` with a `--writes` list and
`spec-from-verified.py` with one `--whole`/`--diff`/`--delete` per file. Both
lists were typed by hand from `git status`, twice in one night, the second time
for about a hundred files. A hand-typed list drops a file silently, and the
lander then refuses the lane (exit 14) or, worse, the file never ships.

So the list comes from git: every changed path in the verified worktree, new
files as WHOLE, modified ones as DIFF, deleted ones as DELETE. Anything else (a
rename, a conflict) is REFUSED rather than guessed. The order is left DRAFT:
promoting it is the read-it-first gate, never this tool's.

Exit: 0 order written, 1 a step failed, 2 the worktree holds a change this
cannot classify, 3 nothing to order.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ORDERS = TOOLS.parent / "queue" / "orders"

LANGS = {".py": "python", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml", ".sh": "bash",
         ".md": "markdown", ".html": "html", ".js": "javascript", ".json": "json",
         ".toml": "toml", ".ps1": "powershell", ".txt": "text", ".css": "css"}


class Unclassified(Exception):
    """A status line this tool will not guess about."""


def lang(path: str) -> str:
    return LANGS.get(Path(path).suffix.lower(), "")


def classify(porcelain: str) -> tuple[list[str], list[str], list[str]]:
    """(whole, diff, delete) from `git status --porcelain --untracked-files=all`."""
    whole: list[str] = []
    diff: list[str] = []
    delete: list[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if code == "??" or code.strip() == "A":
            whole.append(path)
        elif "D" in code and "R" not in code:
            delete.append(path)
        elif code.strip() in ("M", "MM", "AM"):
            (whole if "A" in code else diff).append(path)
        else:
            raise Unclassified(f"{code!r} {path}")
    return whole, diff, delete


def spec_args(whole: list[str], diff: list[str], delete: list[str]) -> list[str]:
    args: list[str] = []
    for p in whole:
        args += ["--whole", f"{p}:{lang(p)}"]
    for p in diff:
        args += ["--diff", f"{p}:{lang(p)}"]
    for p in delete:
        args += ["--delete", p]
    return args


def new_order_file(before: set[Path], after: set[Path], slug: str) -> Path | None:
    """The one order file that appeared, read off the directory, never off
    new-order.py's printed prose. None unless exactly one matches the slug."""
    made = [p for p in after - before if p.name.endswith(f"-{slug}.md")]
    return made[0] if len(made) == 1 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="order-from-worktree")
    ap.add_argument("--self-test", action="store_true")
    for name in ("verified", "base", "slug", "title", "name", "gate", "intro", "repo"):
        ap.add_argument(f"--{name}")
    ap.add_argument("--order", help="fill this EXISTING draft instead of cutting a new one "
                                    "(a first run that stopped at the spec step)")
    ap.add_argument("--commands", default="git-status-short")
    ap.add_argument("--machine", default="pc")
    ap.add_argument("--model", default="opencode/gpt-5-nano")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    needed = ("verified", "base", "intro") if a.order else \
        ("verified", "base", "slug", "title", "name", "gate", "intro")
    need = [n for n in needed if not getattr(a, n)]
    if need:
        ap.error("required: " + ", ".join("--" + n for n in need))

    status = subprocess.run(["git", "-C", a.verified, "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True)
    if status.returncode != 0:
        print(f"order-from-worktree: git status failed: {status.stderr.strip()}")
        return 1
    try:
        whole, diff, delete = classify(status.stdout)
    except Unclassified as exc:
        print(f"order-from-worktree: REFUSED, cannot classify {exc}")
        return 2
    if not (whole or diff or delete):
        print("order-from-worktree: the verified worktree has no changes")
        return 3

    py = sys.executable
    if a.order:
        order = Path(a.order)
        if not order.is_file():
            print(f"order-from-worktree: no such order {order}")
            return 1
    else:
        before = set(ORDERS.glob("*.md"))
        cmd = [py, str(TOOLS / "new-order.py"), "--slug", a.slug, "--title", a.title,
               "--name", a.name, "--gate", a.gate, "--model", a.model, "--machine", a.machine,
               "--commands", a.commands, "--writes", ",".join(whole + diff + delete),
               "--skip-path-check"]
        if a.repo:
            cmd += ["--repo", a.repo]
        if subprocess.run(cmd).returncode != 0:
            print("order-from-worktree: new-order.py failed")
            return 1
        order = new_order_file(before, set(ORDERS.glob("*.md")), a.slug)
        if order is None:
            print(f"order-from-worktree: could not find the one new order for slug {a.slug}")
            return 1
    spec = [py, str(TOOLS / "spec-from-verified.py"), "--order", str(order), "--verified", a.verified,
            "--base", a.base, "--intro", a.intro] + spec_args(whole, diff, delete)
    if subprocess.run(spec).returncode != 0:
        print(f"order-from-worktree: spec-from-verified.py failed; {order} is still a placeholder draft")
        return 1
    print(f"order-from-worktree: {order.name}: {len(whole)} whole, {len(diff)} diff, "
          f"{len(delete)} delete, left DRAFT")
    return 0


def self_test() -> int:
    total = passed = 0

    def want(label: str, cond: bool) -> None:
        nonlocal total, passed
        total += 1
        if cond:
            passed += 1
        else:
            print(f"self-test FAIL: {label}")

    # Real `git status --porcelain --untracked-files=all` lines, slice 2 of the
    # MySQL retirement, 2026-09-27.
    real = (" M api/schema/models.py\n"
            "D  database/CreateSchema.sql\n"
            "D  database/migrations/00_2026-07-28_unknown-rows.sql\n"
            " M tests/unit/test_sites.py\n"
            "?? database/migrations_pg/01_mysql-foreign-keys.sql\n")
    whole, diff, delete = classify(real)
    want("an untracked file is WHOLE", whole == ["database/migrations_pg/01_mysql-foreign-keys.sql"])
    want("a modified file is DIFF", diff == ["api/schema/models.py", "tests/unit/test_sites.py"])
    want("a staged deletion is DELETE", delete == ["database/CreateSchema.sql",
                                                   "database/migrations/00_2026-07-28_unknown-rows.sql"])
    want("an unstaged deletion is DELETE", classify(" D gone.py\n")[2] == ["gone.py"])
    want("a staged new file is WHOLE", classify("A  new.py\n")[0] == ["new.py"])
    try:
        classify("R  old.py -> new.py\n")
        want("a rename is refused", False)
    except Unclassified:
        want("a rename is refused", True)
    try:
        classify("UU both.py\n")
        want("a conflict is refused", False)
    except Unclassified:
        want("a conflict is refused", True)
    want("the spec args carry each file's language",
         spec_args(["a.sql"], ["b.py"], ["c.sh"]) ==
         ["--whole", "a.sql:sql", "--diff", "b.py:python", "--delete", "c.sh"])
    want("an unknown suffix gets no language", lang("Dockerfile") == "")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "180-old.md").write_text("x", encoding="utf-8")
        before = set(d.glob("*.md"))
        (d / "182-farm-schema.md").write_text("x", encoding="utf-8")
        want("the new order is found off the directory",
             new_order_file(before, set(d.glob("*.md")), "farm-schema") == d / "182-farm-schema.md")
        want("a slug that did not appear finds nothing",
             new_order_file(before, set(d.glob("*.md")), "other") is None)
    want("an empty worktree classifies to nothing", classify("") == ([], [], []))

    print(f"order-from-worktree self-test: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
