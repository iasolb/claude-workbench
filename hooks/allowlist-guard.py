"""Deny shell commands that no configured permission rule covers.

Called from a pre-tool hook with a JSON payload on stdin. Unmatched commands
are refused with a rewrite hint; matched commands are left to normal policy.
Errors fail open so malformed hook input cannot prevent a session from starting.

--corpus <prompt-log> <transcript.jsonl...> compares past prompts with the
configured rules and reports false blocks and missed prompts.

(2026-10-25: repeated permission prompts motivated this guard.)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOLS = ("Bash", "PowerShell")
FREE_FIRST_WORDS = {
    "Bash": {"rm", "rmdir", "mkdir", "touch", "mv", "cp", "sleep"},
    "PowerShell": {"set-location", "stop-process", "start-sleep", "sleep"},
}
REDIRECT = re.compile(r"\s+(\d?>>?|&>|\d>&\d)\s*(\S+)?(?=\s|$)")


def settings_files(cwd: str | None) -> list[Path]:
    home = Path.home() / ".claude"
    paths = [home / "settings.json", home / "settings.local.json"]
    if cwd:
        project = Path(cwd) / ".claude"
        paths.extend((project / "settings.json", project / "settings.local.json"))
    return paths


def load_rules(cwd: str | None) -> dict[str, dict[str, list[str]]]:
    rules = {tool: {kind: [] for kind in ("allow", "deny", "ask")} for tool in TOOLS}
    for path in settings_files(cwd):
        try:
            permissions = json.loads(path.read_text(encoding="utf-8")).get("permissions", {})
        except (OSError, ValueError):
            continue
        for kind in rules[TOOLS[0]]:
            for entry in permissions.get(kind, []) or []:
                for tool in TOOLS:
                    if entry == tool:
                        rules[tool][kind].append("*")
                    elif entry.startswith(tool + "(") and entry.endswith(")"):
                        rules[tool][kind].append(entry[len(tool) + 1:-1])
    return rules


def _glob(pattern: str, command: str) -> bool:
    expression = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, command, flags=re.DOTALL) is not None


def rule_matches(pattern: str, command: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return command == prefix or _glob(prefix + "*", command)
    if "*" in pattern:
        if pattern.endswith(" *") and _glob(pattern[:-2], command):
            return True
        return _glob(pattern, command)
    return command == pattern


def split_segments(command: str) -> list[str]:
    """Split shell segments outside quotes; compound forms are checked too."""
    segments, current, quote, index = [], [], None, 0
    while index < len(command):
        char = command[index]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(command):
                current.append(command[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char in "|;&":
            pair = command[index:index + 2]
            if pair in ("||", "&&"):
                index += 1
            elif char == "&" and (
                (index + 1 < len(command) and command[index + 1] == ">")
                or (index > 0 and command[index - 1] in "<>")
            ):
                current.append(char)
                index += 1
                continue
            elif char == "&":
                current.append(char)
                index += 1
                continue
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def unmatched_segments(tool: str, command: str, rules) -> list[str]:
    allow, deny, ask = rules[tool]["allow"], rules[tool]["deny"], rules[tool]["ask"]
    missing = []
    for segment in split_segments(command.strip()):
        segment = REDIRECT.sub("", segment).strip()
        words = segment.split()
        first = words[0] if words else ""
        if first == "cd" or first.lower() in FREE_FIRST_WORDS[tool]:
            continue
        if tool == "PowerShell" and segment[:1] in "'\"" and segment[-1:] == segment[:1]:
            continue
        if any(rule_matches(rule, segment) for rule in allow + deny + ask):
            continue
        missing.append(segment)
    return missing


def hook() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tool = payload.get("tool_name", "")
        command = (payload.get("tool_input") or {}).get("command", "")
        if tool not in TOOLS or not command.strip():
            return
        rules = load_rules(payload.get("cwd"))
        missing = unmatched_segments(tool, command, rules)
        if not missing:
            return
        other = "Bash" if tool == "PowerShell" else "PowerShell"
        if not unmatched_segments(other, command, rules):
            missing = [part + f" (this command is allowed on {other})" for part in missing]
        reason = (
            "BLOCKED by allowlist-guard: no permission rule covers "
            + " AND ".join(repr(part[:160]) for part in missing)
            + ". Rewrite it using a configured command form. If no configured form "
            "can do the work, report the exact command to the permission-list maintainer."
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
    except Exception:
        return


def corpus(prompt_log: str, transcripts: list[str]) -> int:
    prompted = set()
    for line in Path(prompt_log).read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 6 and parts[4] in TOOLS:
            prompted.add((parts[4], parts[5].strip()))
    rules = load_rules(str(Path.cwd()))
    uses, blocked = {}, set()
    for transcript in transcripts:
        for raw in Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                message = json.loads(raw).get("message") or {}
            except ValueError:
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("name") in TOOLS:
                    command = (item.get("input") or {}).get("command", "")
                    if command:
                        uses[item["id"]] = (item["name"], command.strip())
                elif item.get("type") == "tool_result":
                    text = json.dumps(item.get("content"))
                    if "hook error" in text or "BLOCKED by" in text:
                        blocked.add(item.get("tool_use_id"))
    seen, false_blocks, catches, misses = set(), [], [], []
    for uid, (tool, command) in uses.items():
        if uid in blocked or (tool, command) in seen:
            continue
        seen.add((tool, command))
        refused = bool(unmatched_segments(tool, command, rules))
        if (tool, command) in prompted:
            (catches if refused else misses).append((tool, command))
        elif refused:
            false_blocks.append((tool, command, unmatched_segments(tool, command, rules)))
    print(f"commands {len(seen)}  prompted {len(catches) + len(misses)}  caught {len(catches)}  "
          f"missed {len(misses)}  false_blocks {len(false_blocks)}")
    for tool, _command, segments in false_blocks:
        print(f"FALSE\t{tool}\t{segments[0][:140]}")
    for tool, command in misses:
        print(f"MISS\t{tool}\t{command[:140]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--corpus":
        sys.exit(corpus(sys.argv[2], sys.argv[3:]))
    hook()
