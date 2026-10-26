#!/usr/bin/env python3
"""Execute a notebook in place, keys injected, home path scrubbed, or refuse.

    python tools/run-notebook.py <notebook.ipynb> [--env-file <.env>] [--src <dir> ...]
                                 [--cwd <dir>] [--timeout 900]
    python tools/run-notebook.py --self-test

WHY (A3, second occurrence, 2026-10-17). The otter tour notebook was executed
end to end that morning, then research-kit's third notebook needed the same,
with three more teaching notebooks behind it. A bare `jupyter nbconvert
--execute` cannot do the three things each run needs under the shell guard:

1. API keys come from a .env file into the kernel's environment, and their
   values are never printed. `--env-file` reads KEY=VALUE lines.
2. A notebook that must show a fix not yet on PyPI runs against the checkout,
   and `PYTHONPATH=... jupyter` is a leading assignment the permission matcher
   never matches. `--src` prepends each directory to the kernel's PYTHONPATH.
3. research-kit is a PUBLIC repo and a pull prints its save path absolutely
   (`Saved -> C:\\Users\\<name>\\...`). Every output has the home directory, in
   every spelling, replaced by `~`, and the notebook is NOT written if the
   home path or the bare username survives anywhere in it.

The kernel's working directory is the notebook's folder unless `--cwd` says
otherwise, which is where the notebook's own relative paths land.

Exit: 0 executed and written, 1 a cell raised (the notebook is left untouched),
2 refused before running (bad arguments), 3 the home path survived the scrub
(not written).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def read_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines of a .env file. Comments, blanks and junk are skipped."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def home_pattern(home: str) -> re.Pattern[str]:
    """Every spelling of `home` an output can carry, longest first."""
    posix = home.replace("\\", "/")
    spellings = {home, posix, home.replace("\\", "\\\\")}
    if re.match(r"^[A-Za-z]:/", posix):
        spellings.add(f"/{posix[0].lower()}{posix[2:]}")  # Git Bash: the drive as a first segment
    alternation = "|".join(re.escape(s) for s in sorted(spellings, key=len, reverse=True))
    return re.compile(alternation, re.IGNORECASE)


def scrub(value, pattern: re.Pattern[str]):
    """(value with every home spelling replaced by ~, replacements made),
    walking the strings inside an output's dicts and lists. Containers keep
    their own type: nbformat's writer needs its NotebookNode back, and a plain
    dict in its place crashed the first real run (2026-10-17)."""
    if isinstance(value, str):
        return pattern.subn("~", value)
    if isinstance(value, list):
        n = 0
        for i, item in enumerate(value):
            value[i], k = scrub(item, pattern)
            n += k
        return value, n
    if isinstance(value, dict):
        n = 0
        for key, item in value.items():
            value[key], k = scrub(item, pattern)
            n += k
        return value, n
    return value, 0


def leaks(text: str, pattern: re.Pattern[str], username: str) -> list[str]:
    """Lines of the serialised notebook still carrying the home path or the
    bare username. Empty means clean."""
    word = re.compile(rf"(?<![A-Za-z0-9]){re.escape(username)}(?![A-Za-z0-9])",
                      re.IGNORECASE) if username else None
    return [ln.strip()[:120] for ln in text.splitlines()
            if pattern.search(ln) or (word and word.search(ln))]


def scrub_notebook(nb: dict, pattern: re.Pattern[str]) -> int:
    """Scrub every code cell's outputs in place; returns the replacements made."""
    total = 0
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code" and cell.get("outputs"):
            cell["outputs"], n = scrub(cell["outputs"], pattern)
            total += n
    return total


def execute(path: Path, cwd: Path, timeout: int):
    """The executed notebook, or raises nbclient's CellExecutionError."""
    import nbformat
    from nbclient import NotebookClient

    nb = nbformat.read(str(path), as_version=4)
    NotebookClient(nb, timeout=timeout, record_timing=False,
                   resources={"metadata": {"path": str(cwd)}}).execute()
    return nb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run-notebook")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("notebook", nargs="?")
    ap.add_argument("--env-file")
    ap.add_argument("--src", action="append", default=[],
                    help="a source directory to put first on the kernel's PYTHONPATH")
    ap.add_argument("--cwd", help="the kernel's working directory (default: the notebook's)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per cell")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.notebook:
        ap.error("a notebook is required")

    path = Path(a.notebook).resolve()
    if not path.is_file() or path.suffix != ".ipynb":
        print(f"run-notebook: REFUSED, {path} is not a notebook file")
        return 2
    cwd = Path(a.cwd).resolve() if a.cwd else path.parent
    if not cwd.is_dir():
        print(f"run-notebook: REFUSED, --cwd {cwd} is not a directory")
        return 2
    for src in a.src:
        if not Path(src).is_dir():
            print(f"run-notebook: REFUSED, --src {src} is not a directory")
            return 2

    if a.env_file:
        env_path = Path(a.env_file)
        if not env_path.is_file():
            print(f"run-notebook: REFUSED, --env-file {env_path} does not exist")
            return 2
        loaded = read_env(env_path.read_text(encoding="utf-8"))
        os.environ.update(loaded)
        print(f"run-notebook: {len(loaded)} key(s) from the env file: {', '.join(sorted(loaded))}")
    if a.src:
        parts = [str(Path(s).resolve()) for s in a.src]
        if os.environ.get("PYTHONPATH"):
            parts.append(os.environ["PYTHONPATH"])
        os.environ["PYTHONPATH"] = os.pathsep.join(parts)
        print(f"run-notebook: kernel imports first from {', '.join(parts[:len(a.src)])}")

    try:
        nb = execute(path, cwd, a.timeout)
    except Exception as exc:  # nbclient's CellExecutionError, or a kernel that never started
        print(f"run-notebook: a cell FAILED, {path.name} left untouched:\n{exc}")
        return 1

    pattern = home_pattern(str(Path.home()))
    replaced = scrub_notebook(nb, pattern)
    import nbformat
    text = nbformat.writes(nb)
    found = leaks(text, pattern, Path.home().name)
    if found:
        print(f"run-notebook: REFUSED to write, the home path survived the scrub in "
              f"{len(found)} line(s):")
        for ln in found[:10]:
            print(f"  {ln}")
        return 3
    path.write_text(text, encoding="utf-8")
    cells = sum(1 for c in nb.cells if c.cell_type == "code")
    print(f"run-notebook: {path.name}: {cells} code cell(s) executed, "
          f"{replaced} home path(s) scrubbed, written")
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

    env = read_env("# keys\nFRED_API_KEY=abc123\n\nexport CENSUS_API_KEY = \"q w\"\njunk\n=nokey\n")
    want("a .env gives its keys, comments, blanks, junk and keyless lines skipped",
         env == {"FRED_API_KEY": "abc123", "CENSUS_API_KEY": "q w"})

    # A fixture home, built from parts so no machine path sits in this file.
    home = "\\".join(("C:", "Users", "someone"))
    posix = home.replace("\\", "/")
    doubled = home.replace("\\", "\\\\")
    pat = home_pattern(home)
    # The shape of the line census-loader 0.1.3 printed in the otter tour,
    # 2026-10-17, under the fixture home.
    saved = f"\n  Saved → {home}\\Documents\\otter-practice\\data\\states.pkl\n"
    got, n = scrub(saved, pat)
    want("the save path loses its home directory",
         got == "\n  Saved → ~\\Documents\\otter-practice\\data\\states.pkl\n" and n == 1)
    for spelling in (f"{posix}/x", f"{home.lower()[:-7]}SOMEONE\\x", f"{doubled}\\\\x",
                     f"/c{posix[2:]}/x"):
        got, n = scrub(spelling, pat)
        want(f"the spelling {spelling!r} is scrubbed", n == 1 and "someone" not in got.lower())
    got, n = scrub(f"{home}else\\x and plain text", pat)
    want("only the home directory is a match, so a longer name is counted once", n == 1)
    got, n = scrub("nothing personal here", pat)
    want("text with no home path is untouched", got == "nothing personal here" and n == 0)

    nb = {"cells": [
        {"cell_type": "markdown", "source": f"{home} in prose"},
        {"cell_type": "code", "source": "x", "outputs": [
            {"output_type": "stream", "name": "stdout", "text": ["a\n", f"Saved -> {home}\\d.csv\n"]},
            {"output_type": "execute_result", "data": {"text/plain": [f"'{doubled}\\\\d'"]}},
            {"output_type": "error", "traceback": [f"File {posix}/site.py"]},
        ]},
    ]}
    want("stream text, a repr in text/plain and a traceback are all scrubbed",
         scrub_notebook(nb, pat) == 3)
    class Node(dict):
        """Stands in for nbformat's NotebookNode, a dict subclass."""
    node = Node(output_type="stream", text=f"{home}\\x")
    got, n = scrub([node], pat)
    want("an output keeps its own container type through the scrub",
         type(got[0]) is Node and got[0]["text"] == "~\\x" and n == 1)
    want("markdown is not an output and is left for the leak check",
         nb["cells"][0]["source"] == f"{home} in prose")
    serial = json.dumps(nb, indent=1)
    want("the leak check finds the home path left in prose", len(leaks(serial, pat, "someone")) == 1)
    elsewhere = home_pattern(home.replace("someone", "nobody"))
    want("the leak check finds a bare username too",
         leaks("pytest-of-someone/tmp", elsewhere, "someone") != [])
    want("the leak check does not fire on a word that merely contains the name",
         leaks("someoneelse", elsewhere, "someone") == [])

    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "notes.txt"
        bad.write_text("x", encoding="utf-8")
        want("a file that is not a notebook is refused before anything runs", main([str(bad)]) == 2)
        nbfile = Path(tmp) / "n.ipynb"
        nbfile.write_text("{}", encoding="utf-8")
        want("a missing env file is refused before anything runs",
             main([str(nbfile), "--env-file", str(Path(tmp) / "absent.env")]) == 2)
        want("a missing source directory is refused before anything runs",
             main([str(nbfile), "--src", str(Path(tmp) / "absent")]) == 2)

    print(f"run-notebook self-test: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
