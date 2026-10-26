"""Exercise the public GitHub-agent Stop hook against stub tool results."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

HOOK = pathlib.Path(__file__).resolve().parent / "agents-in-flight.sh"
CALL = 'output="$("$PYTHON" "$TOOL" 2>&1)"'


def main() -> int:
    source = HOOK.read_text(encoding="utf-8")
    if CALL not in source:
        print("FAIL  hook invocation changed; update this test to stub the public tool")
        return 1
    bash = shutil.which("bash")
    if not bash:
        print("SKIP  bash is unavailable")
        return 0

    # (tool exit, second consecutive stop?, hook exit). In flight blocks every
    # stop; an UNKNOWN state blocks once, then passes, so it can never loop.
    cases = ((0, False, 0), (3, False, 2), (3, True, 2), (4, False, 2), (4, True, 0), (9, False, 2))
    failures = 0
    with tempfile.TemporaryDirectory() as directory:
        stub_tool = pathlib.Path(directory) / "tool.sh"
        stub_tool.write_text("#!/bin/sh\nexit \"$1\"\n", encoding="utf-8")
        for tool_exit, active, expected in cases:
            stub_hook = pathlib.Path(directory) / f"hook-{tool_exit}.sh"
            replacement = f'output="$(sh "${{TOOL}}" {tool_exit} 2>&1)"'
            stub_hook.write_text(source.replace(CALL, replacement), encoding="utf-8", newline="\n")
            payload = json.dumps({"stop_hook_active": active})
            result = subprocess.run([bash, str(stub_hook)], input=payload,
                                     capture_output=True, text=True,
                                     env={**os.environ, "AGENTS_IN_FLIGHT_TOOL": str(stub_tool)})
            ok = result.returncode == expected
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  tool exit {tool_exit}, {'second' if active else 'first'} stop: "
                  f"hook exit {result.returncode}, expected {expected}")
    print(f"\n{'PASS' if not failures else 'FAIL'}: {failures} failing")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
