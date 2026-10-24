#!/usr/bin/env python3
"""prose-stats.py: word count and dash count for one or more prose files.

Exists because an order asked a driver for before-and-after word counts as its
evidence, and a driver has NO allowed way to take that measurement: `python -c`
is blocked, `powershell -Command` is blocked, and a pipeline into `wc` is
blocked. Three runs of the brevity order died on this. One of them invented its
own counting script, which was the correct instinct and was thrown away as junk.

An order may only require a measurement a driver can actually take. This is the
allowed form: a named script at an absolute path.

usage: prose-stats.py <path> [<path> ...]
       prose-stats.py --git <repo> <ref> <path>   (count a file at a git ref)
       prose-stats.py --self-test

HTML and markdown tags are stripped before counting, so the number is words a
reader sees rather than markup.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TAG_RE = re.compile(r"<[^>]+>")
# Built from code points, not written literally: house style forbids these
# characters in every file, and the lint is right to flag a file that contains
# one even when the file's job is to count them.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


def stats(text, strip_tags=True):
    """Words a reader sees, plus the dash count house style forbids."""
    body = TAG_RE.sub(" ", text) if strip_tags else text
    return {
        "words": len(body.split()),
        "dashes": text.count(EM_DASH) + text.count(EN_DASH),
        "lines": len(text.splitlines()),
        "chars": len(text),
    }


def render(label, s):
    return "%s: %d words, %d lines, %d chars, %d forbidden dash(es)" % (
        label, s["words"], s["lines"], s["chars"], s["dashes"])


def at_ref(repo, ref, path):
    """The file's text as of a git ref, for the BEFORE half of a comparison."""
    out = subprocess.run(
        ["git", "-C", str(repo), "show", "%s:%s" % (ref, path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "").strip() or "git show failed")
    return out.stdout


def self_test():
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)

    s = stats("one two three")
    check("counts plain words", s["words"] == 3)
    check("no dashes in plain text", s["dashes"] == 0)

    s = stats("<p>one <b>two</b></p>")
    check("tags are not counted as words", s["words"] == 2)

    s = stats("a %s b %s c" % (EM_DASH, EN_DASH))
    check("em and en dashes are both counted", s["dashes"] == 2)
    check("dashes are still separate words to the splitter", s["words"] == 5)

    s = stats("line one\nline two\n")
    check("counts lines", s["lines"] == 2)

    # A dash inside a tag still counts: house style forbids the character
    # anywhere, and stripping tags first would have hidden it.
    s = stats('<img alt="a %s b">' % EM_DASH)
    check("a dash inside markup is still reported", s["dashes"] == 1)

    check("render names the label", render("x", stats("a")).startswith("x: 1 words"))

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "f.md"
        p.write_text("hello there %s friend" % EM_DASH, encoding="utf-8")
        text = p.read_text(encoding="utf-8")
        check("reads a real file", stats(text)["words"] == 4)
        check("reports its dash", stats(text)["dashes"] == 1)

    for name in failures:
        print("FAIL: " + name, file=sys.stderr)
    if failures:
        print("self-test: %d failure(s)" % len(failures), file=sys.stderr)
        return 1
    print("self-test: all 10 checks passed")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    if args[0] == "--self-test":
        return self_test()
    if args[0] == "--git":
        if len(args) != 4:
            print("usage: prose-stats.py --git <repo> <ref> <path>",
                  file=sys.stderr)
            return 2
        _, repo, ref, path = args
        try:
            text = at_ref(repo, ref, path)
        except RuntimeError as exc:
            print("prose-stats: %s" % exc, file=sys.stderr)
            return 4
        print(render("%s@%s" % (path, ref), stats(text)))
        return 0
    rc = 0
    for raw in args:
        p = Path(raw)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print("prose-stats: cannot read %s: %s" % (p, exc), file=sys.stderr)
            rc = 4
            continue
        print(render(str(p), stats(text)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
