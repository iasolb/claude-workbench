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

THE GATE'S FLOOR IS READ, NEVER TYPED (2026-10-17). The change was untypeable
but the gate was still a string, and order 299 went red on it alone: its
verified run printed 205 with the new assertions already counted, and the
session typed `--min 209` from arithmetic. So a counted gate (a gatefloor gate,
a bare `--self-test`, a pytest run) is RUN in the verified worktree first and
its passing count read with `gatefloor.read_count`, never scraped here. A bare
gate is written wrapped in gatefloor at that count; a typed floor that differs,
a run with no readable count, and a red run are all REFUSED.
`store:findings/a_generated_order_s_gate_is_still_typed_by_hand_and_it_went`.

Exit: 0 order written, 1 a step failed, 2 the worktree holds a change this
cannot classify, 3 nothing to order, 4 the gate was refused against the
verified run.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ORDERS = TOOLS.parent / "queue" / "orders"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import gatefloor  # noqa: E402  the one count reader; a second regex here would drift
import gatelib  # noqa: E402

# `<python> <path>/gatefloor.py --min N -- <the gate it wraps>`
FLOOR_GATE = re.compile(r"^(?P<wrap>\S+\s+\S*gatefloor\.py)\s+--min\s+(?P<min>\d+)\s+--\s+"
                        r"(?P<inner>\S.*)$")
# A gate whose output carries a passing count: a self-test, or a pytest run.
COUNTED_GATE = re.compile(r"(?:^|\s)--self-test(?:\s|$)|(?:^|[\s/\\])pytest(?:\.exe)?(?:\s|$)")

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


def commit_message(order: Path, title: str | None) -> str:
    """"order 208: <title>", the subject every order commit already wears."""
    number = order.stem.split("-", 1)[0]
    return f"order {number}: {title or order.stem}"


def commit_order(order: Path, title: str | None, repo: Path | None = None) -> bool:
    """Commit the order file alone, by pathspec (another session shares this
    working tree). WHY (A3/A4, 2026-10-03): go.py cuts a lane from the last
    commit and HOLDS an order whose spec was never committed, so every
    generated order needed a commit by hand before it could launch; three in
    one session. Promoting stays the read-it-first gate: a status flip is a
    stamp go.py ignores, so committing the draft costs that gate nothing.

    A file identical to what is committed IS committed (2026-10-09): a re-ship
    that refills an unchanged spec has nothing to commit, and git's refusal
    used to read as "not committed" and stop the ship."""
    repo = repo or TOOLS.parent
    try:
        rel = order.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False
    if subprocess.run(["git", "-C", str(repo), "add", "--", rel]).returncode != 0:
        return False
    if subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--", rel],
                      capture_output=True).returncode == 0:
        return True
    return subprocess.run(["git", "-C", str(repo), "commit", "-m",
                           commit_message(order, title), "--", rel]).returncode == 0


def new_order_file(before: set[Path], after: set[Path], slug: str) -> Path | None:
    """The one order file that appeared, read off the directory, never off
    new-order.py's printed prose. None unless exactly one matches the slug."""
    made = [p for p in after - before if p.name.endswith(f"-{slug}.md")]
    return made[0] if len(made) == 1 else None


def resolve_order(arg: str, orders: Path = ORDERS) -> Path | None:
    """`--order` as a path, or as the order NUMBER this tool printed. A bare
    number was refused as "no such order 215" (2026-10-04) while 215 sat in
    queue/orders/, so a second run had to be retyped by path."""
    path = Path(arg)
    if path.is_file():
        return path
    if arg.isdigit():
        found = sorted(orders.glob(f"{int(arg)}-*.md"))
        return found[0] if len(found) == 1 else None
    return None


def with_writes(text: str, writes: list[str]) -> str:
    """The order with `writes:` set to exactly what the verified change touches,
    and no `reads:` (2026-10-09). A draft cut before the work guesses its files,
    and a reads list makes go.py cut a SPARSE lane where a gate that names the
    fast suite finds no tests/unit; both were hand-edited on the first farm
    draft refilled that night."""
    head, sep, rest = text.partition("\n---")
    lines = [ln for ln in head.splitlines() if not ln.startswith("reads:")]
    lines = [f"writes: {','.join(writes)}" if ln.startswith("writes:") else ln for ln in lines]
    return "\n".join(lines) + sep + rest


def heading_title(text: str) -> str | None:
    """The title in an order's `# Order N: <title>` heading, or None. A refill
    (`--order`) is given no --title, so its commit subject fell back to the file
    stem, "order 305: 305-otter-readme-plots" (2026-10-17)."""
    m = re.search(r"^# Order \d+: (.+)$", text, flags=re.M)
    return m.group(1).strip() if m else None


def field(text: str, key: str) -> str | None:
    """One frontmatter value of an order, or None."""
    for ln in text.partition("\n---")[0].splitlines():
        if ln.startswith(f"{key}:"):
            return ln.split(":", 1)[1].strip() or None
    return None


class GateRefused(Exception):
    """The gate cannot go into an order as typed; the message says why."""


def run_verified(gate: str, verified: str, repo: str | None) -> tuple[int, str]:
    """Run a gate in the verified worktree the way run-gate.py runs one in a lane."""
    proc = subprocess.run(gatelib.resolve(gate, repo=repo), shell=True, cwd=verified,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=1800)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def wrap_floor(gate: str, count: int, repo: str | None) -> str:
    """`gate` under gatefloor at `count`. In the bank the wrapper is the lane's
    own copy; for another repo it is a real dependency named absolutely, which
    gatelib keeps (order 173)."""
    interp = gate.split(None, 1)[0]
    if interp not in gatelib.SYSTEM_PYTHON:
        interp = Path(sys.executable).as_posix()
    in_bank = not repo or repo.rstrip("/").endswith("ai-memory-bank")
    floor = "tools/gatefloor.py" if in_bank else (TOOLS / "gatefloor.py").as_posix()
    return f"{interp} {floor} --min {count} -- {gate.strip()}"


def settle_gate(gate: str, verified: str, repo: str | None) -> tuple[str, int | None]:
    """(the gate to write, the floor read from the verified run, or None when
    the gate prints no count). Raises GateRefused."""
    g = (gate or "").strip()
    if not g:
        raise GateRefused("there is no gate, and a missing gate is not a pass")
    m = FLOOR_GATE.match(g)
    if m:
        inner, typed = m["inner"], int(m["min"])
    elif COUNTED_GATE.search(g):
        inner, typed = g, None
    else:
        return g, None
    rc, out = run_verified(inner, verified, repo)
    tail = "\n".join(out.splitlines()[-15:])
    if rc != 0:
        raise GateRefused(f"the gate FAILED in the verified worktree (exit {rc}); an "
                          f"order is never cut from a red run:\n{tail}")
    count = gatefloor.read_count(out)
    if count is None:
        raise GateRefused("the gate passed in the verified worktree but printed no "
                          "passing count gatefloor can read, so no floor can be set. "
                          f"Fix the tool's summary line first:\n{tail}")
    if count < 1:
        raise GateRefused("the gate passed in the verified worktree with 0 passing, "
                          "which proves nothing")
    if typed is not None and typed != count:
        raise GateRefused(f"the typed floor is {typed} but the verified run passed "
                          f"{count}. Pass the gate WITHOUT the gatefloor wrapper and "
                          f"this tool writes the floor from the run")
    return (g if typed is not None else wrap_floor(g, count, repo)), count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="order-from-worktree")
    ap.add_argument("--self-test", action="store_true")
    for name in ("verified", "base", "slug", "title", "name", "gate", "repo"):
        ap.add_argument(f"--{name}")
    ap.add_argument("--intro", help="a FILE holding the order's opening prose, never the prose itself")
    ap.add_argument("--order", help="fill this EXISTING draft instead of cutting a new one "
                                    "(a first run that stopped at the spec step): its path "
                                    "or its number")
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
    if a.intro and not Path(a.intro).is_file():
        print("order-from-worktree: --intro is a FILE holding the prose, and "
              f"{a.intro[:60]!r} is not one")
        return 2
    if a.order:
        order = resolve_order(a.order)
        if order is None:
            print(f"order-from-worktree: no such order {a.order} (a path, or a "
                  f"number with exactly one file in {ORDERS})")
            return 1
        text = order.read_text(encoding="utf-8")
        filed, repo = field(text, "gate"), field(text, "repo")
        # A --gate given with --order REPLACES the filed one. It used to be
        # dropped without a word, so a refill looked as if it took the new gate
        # and kept the old one (2026-10-17, order 307).
        typed = a.gate or filed
    else:
        filed, typed, repo = None, a.gate, a.repo
    try:
        gate, floor = settle_gate(typed or "", a.verified, repo)
    except GateRefused as exc:
        print(f"order-from-worktree: REFUSED, {exc}")
        return 4
    print("order-from-worktree: " + (f"gate floor {floor}, read from the verified run"
                                     if floor is not None else
                                     "the gate prints no count, so it is kept as typed"))
    if a.order:
        if filed and gate != filed:
            text = text.replace(filed, gate)
        order.write_text(with_writes(text, whole + diff + delete), encoding="utf-8")
    else:
        before = set(ORDERS.glob("*.md"))
        cmd = [py, str(TOOLS / "new-order.py"), "--slug", a.slug, "--title", a.title,
               "--name", a.name, "--gate", gate, "--model", a.model, "--machine", a.machine,
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
    if not commit_order(order, a.title or heading_title(order.read_text(encoding="utf-8"))):
        print(f"order-from-worktree: {order.name} is written but NOT committed; go.py "
              f"holds an uncommitted spec, so commit it before launching")
        return 1
    print(f"order-from-worktree: {order.name}: {len(whole)} whole, {len(diff)} diff, "
          f"{len(delete)} delete, committed, left DRAFT")
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
    want("the commit subject is the order's number and title",
         commit_message(Path("208-farm-baskets.md"), "Baskets at a place")
         == "order 208: Baskets at a place")
    want("an order cut without a title commits under its stem",
         commit_message(Path("209-farm-x.md"), None) == "order 209: 209-farm-x")
    with tempfile.TemporaryDirectory() as tmp:
        want("an order outside this repo is never committed",
             commit_order(Path(tmp) / "210-elsewhere.md", "x") is False)
    draft = ("---\norder: 249\nstatus: draft\nwrites: app/a.py,static/b.js\n"
             "reads: app/c.py\nlane: ../worktrees/x\n---\n\n# body\nwrites: in prose\n")
    synced = with_writes(draft, ["app/a.py", "app/c.py", "tests/t.py"])
    want("a refill sets writes: to the files the change touched",
         "\nwrites: app/a.py,app/c.py,tests/t.py\n" in synced)
    want("and drops reads:, which would make go.py cut a sparse lane",
         "reads:" not in synced)
    want("and touches nothing past the frontmatter",
         synced.endswith("---\n\n# body\nwrites: in prose\n") and "lane: ../worktrees/x" in synced)
    # Red-proven 2026-10-09 by dropping the diff --quiet branch: the refill fails.
    with tempfile.TemporaryDirectory() as tmp:
        r = Path(tmp)
        for args in (["init", "-q"], ["config", "user.email", "t"], ["config", "user.name", "t"],
                     ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git", "-C", str(r), *args], capture_output=True)
        o = r / "211-x.md"
        o.write_text("spec one\n", encoding="utf-8")

        def commits() -> int:
            out = subprocess.run(["git", "-C", str(r), "rev-list", "--count", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            return int(out) if out.isdigit() else 0

        want("a new order is committed", commit_order(o, "x", repo=r) is True and commits() == 1)
        want("refilling it with an IDENTICAL spec counts as committed, with no new commit",
             commit_order(o, "x", repo=r) is True and commits() == 1)
        o.write_text("spec two\n", encoding="utf-8")
        want("a changed spec is committed again", commit_order(o, "x", repo=r) is True
             and commits() == 2)
    # Red-proven 2026-10-04 by dropping the isdigit branch: the number check fails.
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "215-farm-pitch.md").write_text("x", encoding="utf-8")
        want("--order takes the NUMBER the first run printed",
             resolve_order("215", d) == d / "215-farm-pitch.md")
        want("and a path still works", resolve_order(str(d / "215-farm-pitch.md"), d)
             == d / "215-farm-pitch.md")
        want("a number with no order file resolves to nothing", resolve_order("216", d) is None)

    # THE FLOOR IS READ FROM THE VERIFIED RUN (2026-10-17). Real gates, run in a
    # stand-in verified worktree. The summary line is after-lane.py's real
    # shape; 205 is order 299's verified count and 209 the floor it was typed.
    py = Path(sys.executable).as_posix()
    with tempfile.TemporaryDirectory() as tmp:
        v = Path(tmp)
        (v / "tools").mkdir()

        def tool(name: str, body: str) -> str:
            (v / "tools" / f"{name}.py").write_text(body, encoding="utf-8")
            return f"{py} tools/{name}.py --self-test"

        def settled(gate: str, repo: str | None = None) -> tuple[str, object]:
            try:
                return settle_gate(gate, str(v), repo)
            except GateRefused as exc:
                return "REFUSED", str(exc)

        ok = tool("ok", "print('self-test: 205 check(s), 0 failure(s)')\n")
        floored = f"{py} tools/gatefloor.py --min 205 -- {ok}"
        # Red-proven 2026-10-17: returning the bare gate unwrapped fails this one.
        want("a bare self-test gate is written under gatefloor at the count the "
             "verified run printed", settled(ok) == (floored, 205))
        want("a typed floor equal to the verified count is kept as typed",
             settled(floored) == (floored, 205))
        # Red-proven 2026-10-17 by dropping the typed != count check.
        typo = settled(f"{py} tools/gatefloor.py --min 209 -- {ok}")
        want("a typed floor that differs from the verified count is REFUSED, "
             "naming both numbers (order 299)",
             typo[0] == "REFUSED" and "209" in str(typo[1]) and "205" in str(typo[1]))
        # Red-proven 2026-10-17 by dropping the None check: settle_gate then dies
        # comparing None to 0, and the self-test with it.
        want("a passing gate with no readable count is REFUSED, never kept",
             settled(tool("mute", "print('done')\n"))[0] == "REFUSED")
        # It prints a count, so a tool reading output while ignoring the exit
        # code would accept it. Red-proven 2026-10-17 by dropping the rc check.
        want("a gate that FAILS in the verified worktree is REFUSED",
             settled(tool("red", "import sys\nprint('3 assertions passed')\n"
                                 "sys.exit(1)\n"))[0] == "REFUSED")
        # Red-proven 2026-10-17 by dropping the count < 1 check.
        want("a gate that passes nothing is REFUSED",
             settled(tool("none", "print('0 assertions passed')\n"))[0] == "REFUSED")
        # If it were run it would fail on the missing file and be refused.
        want("a gate that prints no count is kept as typed and never run",
             settled(f"{py} tools/missing.py") == (f"{py} tools/missing.py", None))
        want("a missing gate is REFUSED", settled("")[0] == "REFUSED")
        want("a pytest gate is a counted gate",
             COUNTED_GATE.search(f"{py} -m pytest tests/unit -q") is not None)
        farm = wrap_floor("C:/ew/.venv/Scripts/python.exe -m pytest tests/unit -q", 12,
                          "owner/farm-app")
        want("another repo's gate is wrapped by the bank's gatefloor, named absolutely",
             farm == f"{py} {(TOOLS / 'gatefloor.py').as_posix()} --min 12 -- "
                     "C:/ew/.venv/Scripts/python.exe -m pytest tests/unit -q")

        # Wired into main: a refill whose draft carries order 299's typed floor
        # exits 4 and leaves the draft alone. Red-proven 2026-10-17 by deleting
        # the settle_gate call in main.
        subprocess.run(["git", "-C", str(v), "init", "-q"], capture_output=True)
        intro = v / "intro.md"
        intro.write_text("intro\n", encoding="utf-8")
        draft = v / "299-x.md"
        body = (f"---\norder: 299\nstatus: draft\nwrites: x\ngate: {py} tools/gatefloor.py "
                f"--min 209 -- {ok}\n---\n\n# body\n")
        draft.write_text(body, encoding="utf-8")
        rc = main(["--verified", str(v), "--base", str(v), "--intro", str(intro),
                   "--order", str(draft)])
        want("main refuses a draft whose typed floor the verified run contradicts, "
             "exit 4, draft untouched",
             rc == 4 and draft.read_text(encoding="utf-8") == body)

        # A --gate given WITH --order is the gate settled. Red-proven 2026-10-17
        # by reading the filed gate again: the refusal then names 209, not 300.
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            rc = main(["--verified", str(v), "--base", str(v), "--intro", str(intro),
                       "--order", str(draft),
                       "--gate", f"{py} tools/gatefloor.py --min 300 -- {ok}"])
        want("a --gate given with --order is the one settled, never silently dropped",
             rc == 4 and "300" in said.getvalue() and "209" not in said.getvalue())

    # A refill's commit subject, from the heading order 305 really carried.
    refilled = "---\norder: 305\n---\n\n# Order 305: otter: the README describes plotting\n"
    want("a refill's title is read off its heading",
         heading_title(refilled) == "otter: the README describes plotting")
    want("an order with no heading gives no title", heading_title("---\n---\nbody\n") is None)

    print(f"order-from-worktree self-test: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
