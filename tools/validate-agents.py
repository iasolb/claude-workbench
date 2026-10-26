"""Validate the public subagent definition files."""

from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
REQUIRED = ("name", "description", "tools", "model")
ADDED = {"intake", "fast-lane", "cleanup-crew", "pathfinder"}
# Retired personas kept as one-line pointers (agents/README.md). Declared by
# name, never inferred from a file's shape: an agent that lost its tools line
# by accident must still fail.
POINTERS = {"intake", "cleanup-crew", "pathfinder", "judge", "lane-lander", "lane-tender"}
POINTER_TARGET = "commands/quarterback.md"


def parse(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("unterminated frontmatter")
    fields = {}
    for line in text[4:marker].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue  # YAML blank and comment lines
        if ":" not in line:
            raise ValueError("invalid frontmatter line")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, text[marker + 5 :]


def validate_pointer(path, fields, body):
    for key in ("name", "description"):
        if not fields.get(key):
            raise ValueError(f"missing {key}")
    if fields.get("tools"):
        raise ValueError("a pointer declares tools, so it would run as an agent")
    if POINTER_TARGET not in body:
        raise ValueError(f"pointer does not name {POINTER_TARGET}")


def validate(path, pointers=POINTERS):
    fields, body = parse(path)
    if fields.get("name") != path.stem:
        raise ValueError("name does not match filename stem")
    text = path.read_text(encoding="utf-8")
    if chr(0x2014) in text or chr(0x2013) in text:  # em dash, en dash
        raise ValueError("contains em dash or en dash")
    if path.stem in pointers:
        validate_pointer(path, fields, body)
        return
    for key in REQUIRED:
        if not fields.get(key):
            raise ValueError(f"missing {key}")
    tools = [item.strip() for item in fields["tools"].split(",")]
    if not tools or any(not item for item in tools):
        raise ValueError("tools is not a non-empty comma-separated list")
    if len(body.strip()) < 200:
        raise ValueError("body is shorter than 200 characters")


def self_test():
    def rejects(path, pointers=POINTERS):
        try:
            validate(path, pointers)
        except ValueError:
            return True
        return False

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.md"
        path.write_text(
            "---\nname: fixture\ndescription: test\ntools: Read\nmodel: sonnet\n---\n" + "x" * 200,
            encoding="utf-8",
        )
        validate(path)
        path.write_text(
            "---\nname: fixture\ndescription: test\ntools: Read\nmodel: sonnet\n"
            "# reason: a comment line\n---\n" + "x" * 200,
            encoding="utf-8",
        )
        validate(path)
        path.write_text(
            "---\nname: fixture\ndescription: test\ntools: Read\nmodel: sonnet\njunk\n---\n" + "x" * 200,
            encoding="utf-8",
        )
        assert rejects(path), "a non-comment line without a colon is still invalid"
        pointer = "---\nname: fixture\ndescription: retired\n---\n\nPointer: see commands/quarterback.md.\n"
        path.write_text(pointer, encoding="utf-8")
        validate(path, pointers={"fixture"})
        assert rejects(path, pointers=set()), "an undeclared pointer is an agent missing its tools"
        path.write_text(pointer.replace("commands/quarterback.md", "nowhere"), encoding="utf-8")
        assert rejects(path, pointers={"fixture"}), "a pointer must name where it points"
        path.write_text(pointer.replace("retired\n", "retired\ntools: Read\n"), encoding="utf-8")
        assert rejects(path, pointers={"fixture"}), "a pointer that declares tools would run as an agent"


def main():
    self_test()
    checked = 0
    failures = 0
    names = set()
    for path in sorted(AGENTS.glob("*.md")):
        if path.name == "README.md":
            continue
        checked += 1
        try:
            validate(path)
            names.add(path.stem)
            print(f"OK {path.stem}" + (" (pointer)" if path.stem in POINTERS else ""))
        except (OSError, ValueError) as error:
            print(f"FAIL {path.stem}: {error}")
            failures += 1
    missing = sorted(ADDED - names)
    if missing:
        print(f"FAIL required agents missing: {', '.join(missing)}")
        failures += 1
    print(f"Checked {checked} agent files")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
