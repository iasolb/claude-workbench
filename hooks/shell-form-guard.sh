#!/bin/sh
# PreToolUse guard for common shell forms that cannot be represented safely by
# command-prefix permission rules. Reads one hook JSON payload and emits only a
# deny decision; malformed input fails open. Optional environment:
# SHELL_FORM_ALLOWED_PYTHON_VERSION (default 313) controls the absolute Python
# version allowed by the version-specific check.
PY=""
for candidate in python3 python py; do
    path="$(command -v "$candidate" 2>/dev/null)" || continue
    [ -n "$path" ] || continue
    if [ "$("$path" -c 'print(9)' 2>/dev/null)" = 9 ]; then PY="$path"; break; fi
done
[ -n "$PY" ] || exit 0
GUARD_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export SHELL_FORM_GUARD_DIR="$GUARD_DIR"
"$PY" -c '
import json, os, re, sys
try:
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command") or ""
    tool = payload.get("tool_name") or ""
except Exception:
    sys.exit(0)
if not command.strip():
    sys.exit(0)
unquoted = re.sub(r"\"[^\"]*\"", "", command)
unquoted = re.sub(chr(39) + "[^" + chr(39) + "]*" + chr(39), "", unquoted)
head = re.sub(r"^(\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", command.strip())
words = head.split()
first = words[0] if words else ""
bare = "" if re.search(r"[/\\\\]", first) else re.sub(r"(?i)\.exe$", "", first).lower()
base = re.sub(r"(?i)\.exe$", "", re.split(r"[/\\\\]", first)[-1]).lower()
reasons = []
if "\n" in command: reasons.append("multiple command lines can bypass command-prefix rules")
if "&&" in unquoted or "||" in unquoted: reasons.append("chained commands must be separate calls")
if ";" in unquoted: reasons.append("semicolon-separated commands must be separate calls")
if bare in ("python", "python3", "py", "pytest", "pip", "pip3") and not re.search(r"-m\s+venv\b", command): reasons.append("use a configured absolute interpreter or a named script")
if bare == "npm" and "--prefix" not in command: reasons.append("use npm with an explicit --prefix")
if "\\$" in command: reasons.append("remove escaped dollar syntax or quote literal text correctly")
if re.search(r"\\[ \t]", command): reasons.append("remove backslash-escaped whitespace")
if "&" in unquoted.replace("&&", "").replace(">&", "").replace("&>", ""): reasons.append("do not use a shell call operator or background marker")
if bare in ("powershell", "pwsh", "cmd"): reasons.append("invoke the inner command directly instead of a shell wrapper")
if base in ("python", "python3", "py", "node", "ruby", "perl", "bash", "sh", "zsh", "powershell", "pwsh", "cmd") and (re.search(r"(^|\s)-c(\s|$)", unquoted) or re.search(r"(?i)(^|\s)-command(\s|$)", unquoted)): reasons.append("put inline code in a named script")
if re.search(r"(?<![$\w])\{[^\s{}]*,[^\s{}]*\}", unquoted) and tool == "Bash": reasons.append("list paths instead of using brace expansion")
if re.search(r"--(format|pretty)=([%{]|\s|$)", unquoted): reasons.append("use a plain format option or separate its value with a space")
if re.search(r"(^|\s)git(\s|$)", unquoted) and re.search(r"(^|\s)commit(\s|$)", unquoted) and not re.search(r"\s--(\s|$)", unquoted) and "--amend" not in unquoted and "--no-edit" not in unquoted: reasons.append("add an explicit pathspec after git commit --")
if re.search(r"(?i)(^|\s)opencode(\.exe)?\s+debug\s+config\b", unquoted): reasons.append("do not print resolved configuration that may contain secrets")
if re.search(r"(?i)[/\\]opencode(\.exe)?$", first): reasons.append("invoke opencode by its configured command name")
allowed = os.environ.get("SHELL_FORM_ALLOWED_PYTHON_VERSION", "313")
match = re.search(r"(?i)Programs[/\\]Python[/\\]Python(\d+)[/\\]python(\.exe)?$", first)
if match and match.group(1) != allowed: reasons.append("configure the installed Python version or use a virtual environment")
if tool == "PowerShell" and re.search(r"-Confirm:\$(true|false)\b", command, re.I): reasons.append("omit the explicit confirmation flag")
if tool == "PowerShell" and command.lstrip().startswith("("): reasons.append("use a direct property-selection command")
if tool == "PowerShell" and re.search(r"[A-Za-z]:[/\\][^\s,]*,\s*[\"\x27]?[A-Za-z]:[/\\]", command): reasons.append("pass one path per call")
if tool == "PowerShell" and re.search(r"(?i)(^|\s)(curl|wget)(\s|$)", first): reasons.append("use the HTTP client supported by this shell")
if tool == "PowerShell" and first.startswith("["): reasons.append("use a named command or script instead of an inline type expression")
if tool == "Bash" and first == "bash" and len(words) > 1 and not words[1].startswith("-"): reasons.append("invoke the script directly; bash -n remains suitable for syntax checks")
if re.search(r"\s+(\d?>>?|&>>?)\s*[^\s|;&<>]+", unquoted): reasons.append("capture output from the tool result instead of redirecting to a file")
if re.search(r"[A-Za-z]:\\\\", command): reasons.append("use single path separators")
if "|" in unquoted and bare in ("grep", "rg", "cat", "head", "tail", "find", "ls"): reasons.append("use the dedicated file-search or file-read tool")
if re.search(r"--?in-place\b|(^|\s)-[A-Za-z]*i(?:\s|\.)", unquoted) and re.search(r"(^|\s)(sed|perl|ruby)(\s|$)", unquoted): reasons.append("use a file-editing tool instead of an in-place stream edit")
if tool == "PowerShell" and re.search(r"(?i)(^|\s)select-string\b[^|]*\|", unquoted): reasons.append("use the dedicated file-search tool instead of piping Select-String")
if not reasons:
    sys.exit(0)
reason = "BLOCKED by shell-form-guard. Rewrite the command as a single configured call. " + "; ".join(reasons)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
' || exit 0
exit 0
