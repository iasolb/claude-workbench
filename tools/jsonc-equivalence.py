"""Compare an active JSONC config with its preserved original.

Configuration locations can be overridden with OPENCODE_CONFIG,
OPENCODE_CONFIG_BACKUP, and OPENCODE_CONFIG_NOTES. Defaults are
~/.config/opencode/opencode.jsonc, the system temporary directory's
opencode/config-original.jsonc, and opencode-config-notes.md beside the active
config. The original file is the portable baseline; no owner's config hash or
size is embedded in this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

CONFIG = Path(os.environ.get(
    "OPENCODE_CONFIG", str(Path.home() / ".config" / "opencode" / "opencode.jsonc")
)).expanduser()
BACKUP = Path(os.environ.get(
    "OPENCODE_CONFIG_BACKUP",
    str(Path(tempfile.gettempdir()) / "opencode" / "config-original.jsonc"),
)).expanduser()
NOTES = Path(os.environ.get(
    "OPENCODE_CONFIG_NOTES", str(CONFIG.parent / "opencode-config-notes.md")
)).expanduser()


def comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("//")]


def strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    ) + "\n"


def comment_text(line: str) -> str:
    text = line.lstrip()[2:]
    return text[1:] if text.startswith(" ") else text


def make_notes(original: str) -> str:
    lines = original.splitlines()
    notes = [
        "This document holds the explanations removed from the global opencode config.",
        "The config beside it now keeps only what changes behaviour.",
        "",
    ]
    pending: list[str] = []
    context: list[str] = []
    for index, line in enumerate(lines + ["}"]):
        if line.lstrip().startswith("//"):
            pending.append(comment_text(line))
            continue
        if pending:
            key = next_line_key(lines[index] if index < len(lines) else "")
            path = context + ([key] if key else [])
            notes.append("### " + " > ".join(f'"{part}"' for part in path or ["General"]))
            notes.extend(pending)
            notes.append("")
            pending = []
        update_context(line, context)
    return "\n".join(notes).rstrip() + "\n"


def next_line_key(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith('"') and '"' in stripped[1:]:
        return stripped[1 : stripped.find('"', 1)]
    return None


def update_context(line: str, context: list[str]) -> None:
    stripped = line.strip()
    if stripped.startswith('"') and ": {" in stripped:
        key = next_line_key(line)
        if key:
            context.append(key)
    closing = stripped.count("}")
    for _ in range(closing):
        if context:
            context.pop()


def canonical(text: str) -> str:
    return json.dumps(json.loads(strip_comments(text)), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / "fixture.jsonc"
        fixture.write_text('{\n  "url": "https://example.test//keep",\n  // remove me\n  "value": 7\n}\n', encoding="utf-8")
        stripped = strip_comments(fixture.read_text(encoding="utf-8"))
        assert "https://example.test//keep" in stripped
        assert "remove me" not in stripped
        data = json.loads(stripped)
        assert data["value"] == 7
        altered = stripped.replace('"value": 7', '"value": 8')
        assert canonical(altered) != canonical(stripped)
        print("self-test: marker in string preserved: PASS")
        print("self-test: deleted value caught: PASS")


def run_gate() -> None:
    live = CONFIG.read_text(encoding="utf-8")
    original = BACKUP.read_text(encoding="utf-8")
    live_comments = comment_lines(live)
    original_comments = comment_lines(original)
    notes = NOTES.read_text(encoding="utf-8").splitlines()
    note_set = set(notes)
    print(f"comments absent: {'PASS' if not live_comments else 'FAIL'}")
    try:
        json.loads(strip_comments(live))
        parses = True
    except json.JSONDecodeError:
        parses = False
    print(f"json parses: {'PASS' if parses else 'FAIL'}")
    digest = hashlib.sha256(canonical(live).encode("utf-8")).hexdigest()
    original_digest = hashlib.sha256(canonical(original).encode("utf-8")).hexdigest()
    print(f"config equivalence: {'PASS' if digest == original_digest else 'FAIL'} ({digest})")
    print(f"backup bytes: {len(original.encode('utf-8'))}")
    print(f"backup comments: {len(original_comments)}")
    texts = [comment_text(line) for line in original_comments if comment_text(line)]
    missing = [text for text in texts if text not in note_set]
    print(f"comment text in notes: {'PASS' if not missing else 'FAIL'} ({len(texts)} checked)")
    print(f"bytes before: {len(original.encode('utf-8'))}")
    print(f"bytes after: {len(live.encode('utf-8'))}")
    print(f"comment lines moved: {len(original_comments)}")
    if not parses or live_comments or missing or digest != original_digest:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--strip", action="store_true")
    parser.add_argument("--notes", action="store_true")
    args = parser.parse_args()
    if args.backup:
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CONFIG, BACKUP)
        print(f"backup: {BACKUP.stat().st_size} bytes")
    elif args.self_test:
        run_self_test()
    elif args.strip:
        original = CONFIG.read_text(encoding="utf-8")
        NOTES.write_text(make_notes(original), encoding="utf-8")
        CONFIG.write_text(strip_comments(original), encoding="utf-8")
        print(f"stripped: {len(comment_lines(original))} comment lines")
    elif args.notes:
        original = BACKUP.read_text(encoding="utf-8")
        NOTES.write_text(make_notes(original), encoding="utf-8")
        print(f"notes regenerated: {len(comment_lines(original))} comment lines")
    else:
        run_gate()


if __name__ == "__main__":
    main()
