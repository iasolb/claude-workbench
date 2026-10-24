"""Read this machine's workbench settings from its user configuration.

Set WORKBENCH_CONF to override the default ~/.claude/workbench.conf. The file
may contain REPO_PATH, MACHINE_BRANCH, and comma-separated SYNC_BRANCHES.
"""

from __future__ import annotations

import os
from pathlib import Path

CONF_ENV = "WORKBENCH_CONF"
DEFAULT_CONF = Path.home() / ".claude" / "workbench.conf"


def conf_path() -> Path:
    override = os.environ.get(CONF_ENV)
    return Path(override).expanduser() if override else DEFAULT_CONF


def read_conf(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=value settings. An unavailable optional file means no values."""
    out: dict[str, str] = {}
    try:
        text = (path or conf_path()).read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def repo_root(path: Path | None = None) -> Path:
    """Configured repository, or this tool's containing checkout."""
    value = read_conf(path).get("REPO_PATH")
    return Path(value).expanduser() if value else Path(__file__).resolve().parent.parent


def machine_branch(path: Path | None = None) -> str:
    return read_conf(path).get("MACHINE_BRANCH", "")


def sync_branches(path: Path | None = None) -> list[str]:
    raw = read_conf(path).get("SYNC_BRANCHES", "")
    return [branch.strip() for branch in raw.split(",") if branch.strip()]


def _self_test() -> int:
    import tempfile

    checks = 0
    failures: list[str] = []

    def want(condition: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        conf = root / "workbench.conf"
        conf.write_text(
            "# comment\nREPO_PATH=/workspace/project\n\n"
            "MACHINE_BRANCH=primary\nSYNC_BRANCHES=secondary,tertiary\n",
            encoding="utf-8",
        )
        want(repo_root(conf) == Path("/workspace/project"), "configured repository is read")
        want(machine_branch(conf) == "primary", "configured branch is read")
        want(sync_branches(conf) == ["secondary", "tertiary"], "branch list is split")
        want("# comment" not in str(read_conf(conf)), "comments are ignored")
        missing = root / "missing.conf"
        want(read_conf(missing) == {}, "missing config is an empty mapping")
        want(repo_root(missing) == Path(__file__).resolve().parent.parent,
             "missing config falls back to this checkout")
        want(sync_branches(missing) == [], "missing branch list is empty")
        empty = root / "empty.conf"
        empty.write_text("SYNC_BRANCHES=\n", encoding="utf-8")
        want(sync_branches(empty) == [], "an empty list has no blank item")

    print(f"workbench self-test: {checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
