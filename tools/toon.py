#!/usr/bin/env python3
"""toon.py: encode and decode the framework's own storage format, losslessly.

    python tools/toon.py encode <file.json>     JSON in, TOON out
    python tools/toon.py decode <file.toon>     TOON in, JSON out
    python tools/toon.py check <file.toon>      does it round-trip?
    python tools/toon.py --self-test

    from toon import encode, decode             as a library, from beside it

WHY THIS EXISTS (2026-09-16): "the toon repo should be in there ... plug the
toon encode decode in please."

The gap was measured: structured data was being assembled as `.toon` text in
multiple places, while a tested Python encoder/decoder in a separate repository
was not linked from this toolkit:

    const toon = `- lane: null\\n  order: ${JSON.stringify(...)}\\n  ...`

That is the repeated-implementation failure: the format is written in many
places, so one spelling can differ from another without notice. A hand-built
encoder also cannot DECODE, which is why every consumer of these files parses
them with a bespoke regex, and a regex that misses returns None quietly
(`rules/shared/memory-integrity.md`: one tool must never scrape another's
prose).

The encoder and decoder cover the four forms (inline, tabular, keyed tabular,
list) and run with nothing beside this file. Other tools can use it with
`from toon import encode`.

**LOSSLESSNESS IS THE ONLY CLAIM THAT MATTERS**, so the self-test asserts it
against a REAL repo file rather than a fixture invented to pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


# ---------------------------------------------------------------------------
# The encoder: Python objects to TOON
# ---------------------------------------------------------------------------

COMMA = ","
TAB = "\t"
PIPE = "|"

_NUMBER_RE = re.compile(
    r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$"
)


def _needs_quoting(s: str, delimiter: str = COMMA) -> bool:
    if s == "":
        return True
    if s in ("true", "false", "null"):
        return True
    if s == "-" or s.startswith("-"):
        return True
    if s == "#" or s.startswith("#"):
        return True
    if _NUMBER_RE.match(s):
        return True
    if s != s.strip():
        return True
    for ch in s:
        if ch in ('"', "\\", ":", "[", "]", "{", "}", ","):
            return True
        if ord(ch) < 0x20:
            return True
    if delimiter in s:
        return True
    return False


def _escape_string(s: str) -> str:
    result = []
    for ch in s:
        if ch == "\\":
            result.append("\\\\")
        elif ch == '"':
            result.append('\\"')
        elif ch == "\n":
            result.append("\\n")
        elif ch == "\r":
            result.append("\\r")
        elif ch == "\t":
            result.append("\\t")
        elif ord(ch) < 0x20:
            result.append(f"\\u{ord(ch):04x}")
        else:
            result.append(ch)
    return "".join(result)


def _encode_value(value: Any, delimiter: str = COMMA) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if _needs_quoting(value, delimiter):
            return f'"{_escape_string(value)}"'
        return value
    raise TypeError(f"Cannot encode type {type(value).__name__}")


def _collect_keys(objects: list[dict]) -> list[str] | None:
    if not objects:
        return None
    ref_keys = None
    for obj in objects:
        if not isinstance(obj, dict):
            return None
        keys = list(obj.keys())
        if ref_keys is None:
            ref_keys = keys
        elif keys != ref_keys:
            return None
    return ref_keys


def _is_tabular_eligible(objects: list[dict]) -> bool:
    """Check if all objects have the same keys with primitive or
    uniform-nested values (no lists, no mixed shapes)."""
    keys = _collect_keys(objects)
    if not keys or len(keys) == 0:
        return False
    for obj in objects:
        for k in keys:
            val = obj.get(k)
            if isinstance(val, list):
                return False
            if isinstance(val, dict):
                sub_vals = [o.get(k) for o in objects]
                if not all(isinstance(v, dict) for v in sub_vals):
                    return False
                sub_keys = _collect_keys(sub_vals)
                if not sub_keys:
                    return False
                for sv in sub_vals:
                    for sk in sub_keys:
                        if isinstance(sv.get(sk), (dict, list)):
                            return False
    return True


def _format_nested_fields(keys: list[str], objects: list[dict]) -> str:
    parts = []
    for key in keys:
        nested_vals = [obj.get(key) for obj in objects]
        if nested_vals and all(isinstance(v, dict) for v in nested_vals):
            sub_keys = _collect_keys(nested_vals)
            if sub_keys and _is_tabular_eligible(nested_vals):
                nested_str = ",".join(sub_keys)
                parts.append(f"{key}{{{nested_str}}}")
                continue
        parts.append(key)
    return ",".join(parts)


def _flatten_value(val: Any, delimiter: str = COMMA) -> list[str]:
    if isinstance(val, dict):
        result = []
        for k in val:
            result.extend(_flatten_value(val[k], delimiter))
        return result
    return [_encode_value(val, delimiter)]


def _is_uniform_value_map(obj: dict) -> bool:
    """Check if an object's values are all uniform dicts (for keyed tabular)."""
    if not obj:
        return False
    vals = list(obj.values())
    if not all(isinstance(v, dict) for v in vals):
        return False
    keys = _collect_keys(vals)
    if not keys or len(keys) == 0:
        return False
    # Check all values are primitive
    for v in vals:
        for k in keys:
            if isinstance(v.get(k), (dict, list)):
                return False
    return True


def _encode_keyed_tabular(
    obj: dict, key: str, indent: str, delimiter: str
) -> str:
    """Encode an object of uniform objects in keyed tabular form."""
    vals = list(obj.values())
    keys = _collect_keys(vals)
    if not keys:
        return ""
    fields_str = ",".join(keys)
    rows = []
    for entry_key, entry_val in obj.items():
        cells = [_encode_value(entry_val.get(k), delimiter) for k in keys]
        rows.append(
            f"{indent}  {_encode_value(entry_key, delimiter)}: "
            + delimiter.join(cells)
        )
    header = f"{key}[{len(obj)}:]{{{fields_str}}}"
    body = "\n".join(rows)
    return f"{header}:\n{body}"


def _encode(
    value: Any, indent: str, delimiter: str, depth: int
) -> str:
    """Core recursive encoder."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return _encode_value(value, delimiter)

    if isinstance(value, dict):
        if not value:
            return ""
        lines = []
        for key, val in value.items():
            if isinstance(val, dict) and _is_uniform_value_map(val):
                lines.append(_encode_keyed_tabular(val, key, indent, delimiter))
            elif isinstance(val, list):
                lines.append(_encode_array_with_key(key, val, indent, delimiter))
            elif isinstance(val, dict):
                val_str = _encode(val, indent + "  ", delimiter, depth + 1)
                lines.append(f"{key}:\n{_indent_block(val_str, indent + '  ')}")
            else:
                lines.append(f"{key}: {_encode_value(val, delimiter)}")
        return "\n".join(lines)

    if isinstance(value, list):
        return _encode_array_body(value, indent, delimiter, depth)

    raise TypeError(f"Cannot encode type {type(value).__name__}")


def _encode_array_with_key(
    key: str, arr: list, indent: str, delimiter: str
) -> str:
    """Encode a field whose value is an array, with the key in the header."""
    if not arr:
        return f"{key}: []"

    # All primitives -> inline
    if all(not isinstance(v, (dict, list)) for v in arr):
        cells = [_encode_value(v, delimiter) for v in arr]
        return f"{key}[{len(arr)}]: {delimiter.join(cells)}"

    # All uniform dicts -> tabular
    keys = _collect_keys(arr)
    if keys and _is_tabular_eligible(arr):
        fields_str = _format_nested_fields(keys, arr)
        rows = []
        for obj in arr:
            cells = []
            for k in keys:
                cells.extend(_flatten_value(obj.get(k), delimiter))
            rows.append(f"{indent}  {delimiter.join(cells)}")
        header = f"{key}[{len(arr)}]{{{fields_str}}}"
        body = "\n".join(rows)
        return f"{header}:\n{body}"

    # List form
    parts = []
    for item in arr:
        item_str = _encode(item, indent + "  ", delimiter, 1)
        # Indent nested content under the "- "
        indented = _indent_block(item_str, indent + "  ")
        parts.append(f"{indent}  - {indented.lstrip()}")
    return f"{key}[{len(arr)}]:\n" + "\n".join(parts)


def _encode_array_body(
    arr: list, indent: str, delimiter: str, depth: int
) -> str:
    """Encode a bare array (without a key, for root arrays)."""
    if not arr:
        return "[]"

    if all(not isinstance(v, (dict, list)) for v in arr):
        cells = [_encode_value(v, delimiter) for v in arr]
        return f"[{len(arr)}]: {delimiter.join(cells)}"

    keys = _collect_keys(arr)
    if keys and _is_tabular_eligible(arr):
        fields_str = _format_nested_fields(keys, arr)
        rows = []
        for obj in arr:
            cells = []
            for k in keys:
                cells.extend(_flatten_value(obj.get(k), delimiter))
            rows.append(f"{indent}  {delimiter.join(cells)}")
        header = f"[{len(arr)}]{{{fields_str}}}"
        body = "\n".join(rows)
        return f"{header}:\n{body}"

    parts = []
    for item in arr:
        item_str = _encode(item, indent + "  ", delimiter, depth + 1)
        parts.append(f"{indent}  - {item_str}")
    return f"[{len(arr)}]:\n" + "\n".join(parts)


def _indent_block(text: str, indent: str) -> str:
    lines = text.split("\n")
    return "\n".join(indent + line if line else "" for line in lines)


def encode(value: Any, delimiter: str = COMMA) -> str:
    """Encode a Python object to TOON format."""
    return _encode(value, "", delimiter, 0)


# ---------------------------------------------------------------------------
# The decoder: TOON to Python objects
# ---------------------------------------------------------------------------

def _unescape_string(s: str) -> str:
    result = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            next_ch = s[i + 1]
            if next_ch == "\\":
                result.append("\\")
            elif next_ch == '"':
                result.append('"')
            elif next_ch == "n":
                result.append("\n")
            elif next_ch == "r":
                result.append("\r")
            elif next_ch == "t":
                result.append("\t")
            elif next_ch == "u" and i + 5 < len(s):
                hex_str = s[i + 2 : i + 6]
                try:
                    result.append(chr(int(hex_str, 16)))
                    i += 4
                except ValueError:
                    result.append("\\u")
            else:
                result.append(ch)
            i += 2
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def _parse_value(raw: str) -> Any:
    s = raw.strip()
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "":
        return ""
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return _unescape_string(s[1:-1])
    try:
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except ValueError:
        pass
    return s


def _split_csv(line: str, delimiter: str = ",") -> list[str]:
    """Split a CSV line respecting quoted fields."""
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == "\\" and i + 1 < len(line):
                current.append(ch)
                current.append(line[i + 1])
                i += 2
                continue
            elif ch == '"':
                in_quotes = False
                current.append(ch)
            else:
                current.append(ch)
        else:
            if ch == '"':
                in_quotes = True
                current.append(ch)
            elif ch == delimiter:
                fields.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        i += 1
    fields.append("".join(current).strip())
    return fields


def _strip_comments(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        result.append(line)
    return result


def _indent_level(line: str) -> int:
    stripped = line.lstrip(" ")
    return (len(line) - len(stripped)) // 2


# ---- Field spec parsing ----

def _parse_field_spec(spec: str) -> list[str]:
    """Parse 'id,customer{name,country},total' into ['id', 'customer{name,country}', 'total']."""
    fields = []
    depth = 0
    current = []
    for ch in spec:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        fields.append("".join(current))
    return fields


def _count_leaves(specs: list[str]) -> int:
    count = 0
    for spec in specs:
        if "{" in spec and spec.endswith("}"):
            sub = spec[spec.index("{") + 1 : -1]
            count += _count_leaves(_parse_field_spec(sub))
        else:
            count += 1
    return count


# ---- Rebuild nested structure from flat fields ----

def _rebuild(flat: list[str], specs: list[str], delimiter: str) -> dict[str, Any]:
    result = {}
    idx = 0
    for spec in specs:
        if "{" in spec and spec.endswith("}"):
            name = spec[: spec.index("{")]
            sub_specs = _parse_field_spec(spec[spec.index("{") + 1 : -1])
            n = _count_leaves(sub_specs)
            sub_flat = flat[idx : idx + n]
            result[name] = _rebuild(sub_flat, sub_specs, delimiter)
            idx += n
        else:
            if idx < len(flat):
                result[spec] = _parse_value(flat[idx])
            else:
                result[spec] = None
            idx += 1
    return result


# ---- Token-level parser ----

def _match_array_header(s: str):
    """Try to match 'key[N]{fields}' or 'key[N]{fields}' or 'key[N:]{fields}'.
    Returns (key, count, is_keyed, fields_str, rest) or None."""
    # key[N]...: rest, so find the bracket and brace positions manually
    # to handle nested braces like {id,customer{name,country},total}
    m_key = re.match(r'^([a-zA-Z_][\w]*)\[(\d+)(:?)\]', s)
    if not m_key:
        # Root array
        m_root = re.match(r'^\[(\d+)(:?)\]', s)
        if not m_root:
            return None
        key = None
        count = int(m_root.group(1))
        is_keyed = m_root.group(2) == ":"
        rest_start = m_root.end()
    else:
        key = m_key.group(1)
        count = int(m_key.group(2))
        is_keyed = m_key.group(3) == ":"
        rest_start = m_key.end()

    after = s[rest_start:]
    # Parse field spec with nested braces
    fields_str = None
    if after.startswith("{"):
        depth = 0
        end = 0
        for i, ch in enumerate(after):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            fields_str = after[1 : end - 1]  # strip outer braces
            after = after[end:]

    # After fields, expect ": " then rest
    after = after.lstrip()
    if after.startswith(":"):
        after = after[1:].lstrip()

    rest = after.strip()
    return key, count, is_keyed, fields_str or None, rest


def decode(text: str, delimiter: str = ",") -> Any:
    """Decode a TOON string to a Python object."""
    lines = _strip_comments(text.strip().split("\n"))
    lines = [l for l in lines if l.strip()]
    if not lines:
        return {}
    result, _ = _parse_block(lines, 0, delimiter, 0)
    return result


def _parse_block(
    lines: list[str], start: int, delimiter: str, min_indent: int
) -> tuple[dict[str, Any], int]:
    """Parse lines into a dict, starting at `start`, until indent drops below `min_indent`."""
    result = {}
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip(" ")
        if not stripped:
            i += 1
            continue
        ind = _indent_level(line)
        if ind < min_indent:
            break

        # Try array header
        hdr = _match_array_header(stripped)
        if hdr is not None:
            key, count, is_keyed, fields_str, rest_of_line = hdr
            if key is None:
                # root array (no key), which should not happen in _parse_block
                i += 1
                continue

            if count == 0:
                result[key] = []
                i += 1
                continue

            # Inline array (no fields, values on same line)
            if fields_str is None and rest_of_line:
                vals = _split_csv(rest_of_line, delimiter)
                result[key] = [_parse_value(v) for v in vals]
                i += 1
                continue

            # Inline array (no fields, values on next lines as list form)
            if fields_str is None and not rest_of_line:
                arr, i = _parse_list_form(lines, i + 1, count, ind + 1, delimiter)
                result[key] = arr
                continue

            # Keyed tabular
            if is_keyed and fields_str:
                spec = _parse_field_spec(fields_str)
                obj, i = _parse_keyed_rows(lines, i + 1, count, ind + 1, spec, delimiter)
                result[key] = obj
                continue

            # Tabular
            if fields_str:
                spec = _parse_field_spec(fields_str)
                arr, i = _parse_tabular_rows(lines, i + 1, count, ind + 1, spec, delimiter)
                result[key] = arr
                continue

            i += 1
            continue

        # Simple key: value
        m = re.match(r'^([a-zA-Z_][\w]*):\s*(.*)', stripped)
        if m:
            key = m.group(1)
            rest = m.group(2).strip()

            if rest == "":
                # Nested object
                if i + 1 < len(lines):
                    next_ind = _indent_level(lines[i + 1])
                    if next_ind > ind:
                        nested, i = _parse_block(lines, i + 1, delimiter, next_ind)
                        result[key] = nested
                        continue
                result[key] = {}
                i += 1
                continue

            # Empty array: key: []
            if rest == "[]":
                result[key] = []
                i += 1
                continue

            # Value on same line
            result[key] = _parse_value(rest)
            i += 1
            continue

        # Unrecognized line, so skip it
        i += 1

    return result, i


def _parse_list_form(
    lines: list[str], start: int, count: int, min_indent: int, delimiter: str
) -> tuple[list, int]:
    """Parse list form: lines starting with '- '."""
    result = []
    i = start
    while i < len(lines) and len(result) < count:
        stripped = lines[i].lstrip(" ")
        if not stripped:
            i += 1
            continue
        ind = _indent_level(lines[i])
        if ind < min_indent:
            break
        if stripped.startswith("- "):
            val_part = stripped[2:]
            # AN OBJECT LIST ITEM IS PARSED AS AN OBJECT. This branch used to
            # detect the object and then fall through to `_parse_value`, which
            # returns the raw TEXT, so `[{"k": "v"}]` decoded to `["k: v"]`.
            # It was silently lossy, and the comment here said "unlikely in
            # list form" while the encoder emits exactly this shape for any
            # ragged or non-uniform array. Measured 2026-09-16 against the
            # reference implementation (`@toon-format/cli` v4.1.1), which
            # decodes the same bytes to real objects.
            if re.match(r'^[A-Za-z_][\w]*:', val_part):
                # Continuation lines belong to THIS item: anything indented
                # deeper than the item's own line and not starting a new item.
                #
                # `_indent_level` returns a DEPTH (spaces // 2), not a space
                # count, so the synthetic first line is written at
                # `depth * 2` spaces. Getting that wrong is silent: the block
                # parses to an EMPTY object rather than failing, which is how
                # the first attempt at this fix read as working.
                depth = ind + 1
                block = [" " * (depth * 2) + val_part]
                j = i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if not nxt.strip():
                        j += 1
                        continue
                    if _indent_level(nxt) < depth:
                        break
                    if nxt.lstrip(" ").startswith("- "):
                        break
                    block.append(nxt)
                    j += 1
                obj, _ = _parse_block(block, 0, delimiter, depth)
                result.append(obj)
                i = j
                continue
            result.append(_parse_value(val_part))
        i += 1
    return result, i


def _parse_tabular_rows(
    lines: list[str], start: int, count: int, min_indent: int,
    field_spec: list[str], delimiter: str
) -> tuple[list[dict], int]:
    """Parse tabular rows into list of dicts."""
    result = []
    i = start
    while i < len(lines) and len(result) < count:
        stripped = lines[i].lstrip(" ")
        if not stripped:
            i += 1
            continue
        ind = _indent_level(lines[i])
        if ind < min_indent:
            break
        if ind == min_indent:
            cells = _split_csv(stripped.rstrip(), delimiter)
            obj = _rebuild(cells, field_spec, delimiter)
            result.append(obj)
        i += 1
    return result, i


def _parse_keyed_rows(
    lines: list[str], start: int, count: int, min_indent: int,
    field_spec: list[str], delimiter: str
) -> tuple[dict, int]:
    """Parse keyed tabular rows into a dict of dicts."""
    result = {}
    i = start
    while i < len(lines) and len(result) < count:
        stripped = lines[i].lstrip(" ")
        if not stripped:
            i += 1
            continue
        ind = _indent_level(lines[i])
        if ind < min_indent:
            break
        if ind == min_indent and ": " in stripped:
            colon_pos = stripped.index(": ")
            entry_key = stripped[:colon_pos]
            rest = stripped[colon_pos + 2:]
            cells = _split_csv(rest, delimiter)
            obj = _rebuild(cells, field_spec, delimiter)
            result[entry_key] = obj
        i += 1
    return result, i


# ---------------------------------------------------------------------------
# The check, the command line and the self-test
# ---------------------------------------------------------------------------

KEY_TOKEN = re.compile(r"^\s*-?\s*([A-Za-z_][\w]*)\s*:", re.MULTILINE)


def round_trips(text: str) -> tuple[bool, str]:
    """(did it survive, why not). The only property worth asserting.

    **THE FIRST VERSION OF THIS FUNCTION LIED, and that is worth keeping in
    view.** It compared `decode(text)` against `decode(encode(decode(text)))`,
    which is blind to loss in the FIRST decode: whatever the decoder threw
    away was equally absent from both sides, so the two agreed and it printed
    "round-trips losslessly". Measured 2026-09-16 on a real file the framework
    had written, where the decoder silently dropped a `run_id` field and the
    check called it lossless. That is precisely "ask what this would print if
    the thing were broken" (`rules/shared/memory-integrity.md` rule 2).

    So the check now also asserts CONTAINMENT against the original TEXT: every
    key the source names must survive into the re-encoding. Keys rather than
    whole text, because a re-encode may legitimately pick a different FORM for
    the same data (inline versus tabular), and comparing text would report that
    as drift.
    """
    try:
        first = decode(text)
    except Exception as exc:                                    # noqa: BLE001
        return False, f"could not decode at all: {type(exc).__name__}: {exc}"
    try:
        reencoded = encode(first)
        again = decode(reencoded)
    except Exception as exc:                                    # noqa: BLE001
        return False, f"decoded, then failed to re-encode: {exc}"
    if first != again:
        return False, "re-encoding changed the data"
    lost = sorted(set(KEY_TOKEN.findall(text))
                  - set(KEY_TOKEN.findall(reencoded)))
    if lost:
        return False, ("the decoder DROPPED key(s) the source names: "
                       + ", ".join(lost))
    return True, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="toon")
    ap.add_argument("verb", nargs="?", choices=("encode", "decode", "check"))
    ap.add_argument("path", nargs="?")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.verb or not a.path:
        ap.error("give a verb and a path, or --self-test")
    # A relative path is read from where the command runs, like any command's.
    path = Path(a.path).resolve()
    if not path.is_file():
        print(f"toon: no such file: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    if a.verb == "encode":
        print(encode(json.loads(text)))
        return 0
    if a.verb == "decode":
        print(json.dumps(decode(text), indent=2))
        return 0
    ok, why = round_trips(text)
    if ok:
        print(f"toon: {path.name} round-trips losslessly")
        return 0
    print(f"toon: {path.name} does NOT round-trip: {why}", file=sys.stderr)
    return 1


def self_test() -> int:
    fails, checks = [], 0

    def want(label, cond):
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    want("a flat object survives", decode(encode({"a": 1, "b": "x"}))
         == {"a": 1, "b": "x"})
    want("a nested object survives",
         decode(encode({"counts": {"scanned": 50}}))
         == {"counts": {"scanned": 50}})
    want("a primitive array survives (the inline form)",
         decode(encode({"tags": ["admin", "ops", "dev"]}))
         == {"tags": ["admin", "ops", "dev"]})
    # THE TABULAR FORM IS THE WHOLE POINT of the format: uniform objects
    # collapse to a header plus rows, which is where the token saving is.
    rows = {"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}]}
    out = encode(rows)
    want("uniform objects encode to the TABULAR form, not a list",
         "users[2]{id,name}" in out)
    want("and the tabular form decodes back to the same rows",
         decode(out) == rows)
    want("a ragged array still survives, in the list form",
         decode(encode({"items": [1, "two", {"k": "v"}]}))
         == {"items": [1, "two", {"k": "v"}]})
    want("an empty object survives", decode(encode({})) == {})

    # VALUES THAT BREAK NAIVE HAND-BUILT TOON, which is the argument for using
    # this instead of a format string: a comma inside a value would split a
    # tabular row, and a colon would read as a key separator.
    tricky = {"note": "a, comma and a: colon", "path": "C:/workspace/example"}
    want("a comma inside a value does not split a row",
         decode(encode(tricky)) == tricky)
    want("a colon inside a value does not read as a key separator",
         decode(encode({"k": "a: b"})) == {"k": "a: b"})

    # AGAINST A REAL FILE THE FRAMEWORK ACTUALLY WROTE, because a fixture
    # invented to pass proves only that the test agrees with the code.
    # AN OBJECT INSIDE A LIST. Red-proven by restoring the original
    # `_parse_value(val_part)` branch in `_parse_list_form` above: this returns
    # `['k: v']` instead of `[{'k': 'v'}]` and the two assertions below fail.
    want("an object inside a list decodes to an OBJECT, not its text",
         decode(encode({"runs": [{"k": "v"}, {"j": "w"}]}))
         == {"runs": [{"k": "v"}, {"j": "w"}]})
    want("a ragged array mixing scalars and objects survives",
         decode(encode({"items": [1, "two", {"k": "v"}]}))
         == {"items": [1, "two", {"k": "v"}]})
    # A MULTI-KEY OBJECT IN A LIST, which needs the continuation lines to be
    # gathered into the SAME item rather than starting a new one.
    multi = {"runs": [{"id": 1, "ok": True}, {"id": 2, "ok": False}]}
    want("a multi-key object in a list keeps all of its keys",
         decode(encode(multi)) == multi)

    # THE CHECK ITSELF MUST NOT LIE. This is the exact text that was reported
    # as lossless while `run_id` was being dropped.
    bad = '- run_id: "probe"\n  action: "land-then-go"\n  ok: false\n'
    ok, why = round_trips(bad)
    want("a source whose keys do not survive is reported as NOT round-tripping",
         ok is False and "DROPPED" in why and "run_id" in why)
    want("and a genuinely lossless source still passes",
         round_trips(encode({"a": 1, "b": [1, 2]}))[0] is True)

    real = REPO.parent / "state" / "routines" / "gmail-digest.toon"
    if real.is_file():
        ok, why = round_trips(real.read_text(encoding="utf-8"))
        if not ok:
            print(f"       {why}")
        want(f"a real .toon this framework wrote round-trips ({real.name})", ok)
        data = decode(real.read_text(encoding="utf-8"))
        want("and its nested key decodes to the right value",
             isinstance(data, dict)
             and data.get("counts", {}).get("scanned") == 50)
    else:
        print("  note  no real .toon on this machine to test against, so the "
              "losslessness claim is UNVERIFIED against production output")

    if fails:
        print(f"\ntoon self-test: {len(fails)} of {checks} FAILED")
        return 1
    print(f"\ntoon self-test: {checks} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
