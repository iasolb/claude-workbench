"""Build the deployment-gate change digest for a commit range.

Sections 1 and 2 are placeholders a planning session fills in. Sections
3 and 4 are machine-written here and must never be hand-edited: the
gate verdict comes from exit codes only, so it stays a measurement and
never becomes an opinion.

Standard library only. Read-only with respect to the repo except for
its own output file.
"""

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

def _git_bash_spelling(path: Path) -> str:
    """A Windows path in the POSIX spelling Git Bash and the tables use."""
    posix = str(path).replace("\\", "/")
    if len(posix) >= 2 and posix[1] == ":":
        return "/%s%s" % (posix[0].lower(), posix[2:])
    return posix


def _default_gate() -> str:
    """The repo's lint tool, in the Git Bash spelling the tables use.

    Built from the repo root and the running interpreter so no machine path
    or account name is baked in. The lint lives in the repo's own tools dir.
    """
    lint = Path(__file__).resolve().parent.parent / "tools" / "lint.py"
    if os.name == "nt":
        return "%s %s --errors-only" % (_git_bash_spelling(sys.executable),
                                        _git_bash_spelling(lint))
    return "%s %s --errors-only" % (sys.executable, lint)


DEFAULT_GATE = _default_gate()

SECTION_1_PLACEHOLDER = (
    "TO BE WRITTEN BY A PLANNING SESSION. Plain words, visible behavior, "
    "written for QA and not for code review. Delete this line when filled."
)
SECTION_2_PLACEHOLDER = (
    "TO BE WRITTEN BY A PLANNING SESSION. What else touches the changed "
    "code, and what to poke at if something looks wrong. "
    "Delete this line when filled."
)

MAX_GATE_LINES = 100


def run_git(repo: Path, args):
    """Run one git command and return stdout; raise on any failure."""
    proc = subprocess.run(
        ["git", "-C", str(repo)] + args, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "git {} failed: {}".format(
                " ".join(args), (proc.stderr or "").strip()
            )
        )
    return proc.stdout


def find_bash():
    """A Windows bash that is not the WSL stub in System32.

    Gates are written in the POSIX spelling the permission tables use,
    so on Windows they are executed through Git Bash. WSL's bash maps
    paths under /mnt/c and would break them. Returns None elsewhere,
    where the plain shell already speaks POSIX.
    """
    if os.name != "nt":
        return None
    fixed = Path(r"C:\Program Files\Git\bin\bash.exe")
    if fixed.exists():
        return str(fixed)
    for entry in os.get_exec_path():
        cand = Path(entry) / "bash.exe"
        if cand.exists() and "system32" not in str(cand).lower():
            return str(cand)
    return None


def run_gate(cmd):
    """Run one gate; return (exit code display, combined output).

    A gate that cannot be executed at all records n/a, which the
    verdict treats as a failure. A gate that ran and failed is an
    ordinary non-zero exit code.
    """
    bash = find_bash()
    try:
        if bash is not None:
            proc = subprocess.run(
                [bash, "-c", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        else:
            proc = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
    except OSError as exc:
        return "n/a", "could not execute gate: {}\n".format(exc)
    return str(proc.returncode), proc.stdout or ""


def clip(text):
    """Keep the LAST 100 lines of raw output; report whether cut."""
    lines = text.splitlines()
    if len(lines) <= MAX_GATE_LINES:
        return text.rstrip("\n"), False
    return "\n".join(lines[-MAX_GATE_LINES:]), True


def head_label(repo, head):
    """Branch name of head, or its short sha when detached."""
    name = run_git(repo, ["rev-parse", "--abbrev-ref", head]).strip()
    if name == "HEAD":
        name = run_git(repo, ["rev-parse", "--short", head]).strip()
    return name


def date_span(repo, base, head, count):
    if count == 0:
        return "no commits"
    # --format ends each line with the unit separator byte, which git's
    # short dates can never contain, so the caller's delimiter -- not
    # whitespace -- is what the parse leans on (rules/shared/memory-integrity.md
    # rule 2).
    days = sorted(
        set(
            line.rstrip("\x1f")
            for line in run_git(
                repo, ["log", "--date=short", "--format=%ad%x1f",
                       "{}..{}".format(base, head)]
            ).splitlines()
            if line.strip()
        )
    )
    if len(days) == 1:
        return days[0]
    return "{} .. {}".format(days[0], days[-1])


def gates_section(gates):
    parts = []
    failures = 0
    for cmd in gates:
        code, output = run_gate(cmd)
        if code != "0":
            failures += 1
        body, was_cut = clip(output)
        chunk = ["### `{}`".format(cmd), "exit code: {}".format(code)]
        if was_cut:
            chunk.append("(output truncated to the last {} lines)".format(
                MAX_GATE_LINES))
        chunk.extend(["", "```", body, "```", ""])
        parts.append("\n".join(chunk))
    if failures:
        verdict = "VERDICT: FAIL ({} of {} gates non-zero)".format(
            failures, len(gates))
    else:
        verdict = "VERDICT: PASS (all gates exit 0)"
    return "\n".join(parts) + verdict + "\n"


def parse_log_lines(text):
    """(hash, subject) pairs from `git log --format=%h%x1f%s`.

    The unit separator byte, never whitespace, splits the fields: commit
    subjects routinely contain spaces, so a space split would tear them
    (rules/shared/memory-integrity.md rule 2). A line without the separator
    is skipped rather than guessed at.
    """
    pairs = []
    for line in text.splitlines():
        if "\x1f" not in line:
            continue
        h, _, s = line.partition("\x1f")
        pairs.append((h.strip(), s.strip()))
    return pairs


def files_section(repo, base, head):
    stat = run_git(repo, ["diff", "--stat", "{}..{}".format(base, head)])
    stat = stat.strip() or "(no file changes)"
    log = run_git(
        repo, ["log", "--format=%h%x1f%s", "{}..{}".format(base, head)]
    )
    commits = "\n".join(
        "- {} {}".format(h, s) for h, s in parse_log_lines(log)
    ) or "(no commits)"
    return "{}\n\nCommits:\n{}\n".format(stat, commits)


def build_digest(repo, base, head, gates):
    count = int(run_git(repo, ["rev-list", "--count",
                               "{}..{}".format(base, head)]).strip())
    label = head_label(repo, head)
    header = (
        "# Change digest: {label} -> {base}\n"
        "\n"
        "{count} commits, {span}. Generated by tools/digest.py, do not "
        "hand-edit\nsections 3 and 4.\n".format(
            label=label, base=base, count=count,
            span=date_span(repo, base, head, count))
    )
    sections = [
        header,
        "## 1. What you would notice\n\n{}\n".format(SECTION_1_PLACEHOLDER),
        "## 2. What could break\n\n{}\n".format(SECTION_2_PLACEHOLDER),
        "## 3. Gate results (machine-written, exit codes are the verdict)\n"
        "\n" + gates_section(gates),
        "## 4. Files and lines\n\n" + files_section(repo, base, head),
    ]
    return label, "\n".join(sections)


def default_out(repo, label):
    today = datetime.date.today().strftime("%Y-%m-%d")
    return repo / "reports" / "digests" / "{}-{}.md".format(today, label)


def run_self_test() -> int:
    """The parsers are proven against a fixture repo git itself writes, never
    against text this test author invented (rules/shared/memory-integrity.md
    rule 2)."""
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="digest-selftest-"))
    problems: list[str] = []
    try:
        repo = root / "repo"
        repo.mkdir()

        def _git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(repo), *args], check=True,
                capture_output=True, text=True, timeout=60)

        _git("init", "-q")
        _git("checkout", "-q", "-b", "main")
        ident = ["-c", "user.name=fixture", "-c", "user.email=fixture.invalid"]
        for subject in ("first", "second subject with spaces", "third"):
            _git(*ident, "commit", "--allow-empty", "-m", subject)

        log = run_git(repo, ["log", "--format=%h%x1f%s", "main"])
        pairs = parse_log_lines(log)
        if len(pairs) != 3:
            problems.append(f"expected 3 commit lines, got {len(pairs)}")
        elif any(len(h) != 7 for h, _ in pairs):
            problems.append(f"short-hash length wrong: {pairs!r}")
        elif pairs[1][1] != "second subject with spaces":
            problems.append(f"subject lost its spaces: {pairs[1]!r}")

        span = date_span(repo, "HEAD~2", "HEAD", 3)
        if span == "no commits" or "-" not in span:
            problems.append(f"date span wrong against real output: {span!r}")

        if problems:
            for p in problems:
                print(f"FAIL: {p}", file=sys.stderr)
            return 1
        print("PASS: digest parsers ran against a real git fixture repo")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="main", help="base ref")
    parser.add_argument("--head", default="HEAD", help="head ref")
    parser.add_argument(
        "--repo", default=None,
        help="absolute path to the repository (default: this script's repo)",
    )
    parser.add_argument("--out", default=None, help="absolute output path")
    parser.add_argument(
        "--gate", action="append", default=[DEFAULT_GATE],
        help="gate command; repeatable, added to the default lint gate",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo = Path(args.repo).resolve() if args.repo else \
        Path(__file__).resolve().parent.parent

    try:
        label, doc = build_digest(repo, args.base, args.head, args.gate)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2

    out = Path(args.out).resolve() if args.out else default_out(repo, label)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
