"""Validate the public subagent definition files."""

from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
REQUIRED = ("name", "description", "tools", "model")
ADDED = {"intake", "fast-lane", "cleanup-crew", "pathfinder"}


def parse(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("unterminated frontmatter")
    fields = {}
    for line in text[4:marker].splitlines():
        if ":" not in line:
            raise ValueError("invalid frontmatter line")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, text[marker + 5 :]


def validate(path):
    fields, body = parse(path)
    for key in REQUIRED:
        if not fields.get(key):
            raise ValueError(f"missing {key}")
    if fields["name"] != path.stem:
        raise ValueError("name does not match filename stem")
    tools = [item.strip() for item in fields["tools"].split(",")]
    if not tools or any(not item for item in tools):
        raise ValueError("tools is not a non-empty comma-separated list")
    if len(body.strip()) < 200:
        raise ValueError("body is shorter than 200 characters")
    if "\u2014" in path.read_text(encoding="utf-8") or "\u2013" in path.read_text(encoding="utf-8"):
        raise ValueError("contains em dash or en dash")


def self_test():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.md"
        path.write_text(
            "---\nname: fixture\ndescription: test\ntools: Read\nmodel: sonnet\n---\n" + "x" * 200,
            encoding="utf-8",
        )
        validate(path)


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
            print(f"OK {path.stem}")
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
