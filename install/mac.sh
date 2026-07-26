#!/usr/bin/env bash
# Symlinks this repo's tracked config into ~/.claude and writes
# ~/.claude/workbench.conf so the hooks know where the repo lives.
# Repo paths resolve relative to this script's location, but run it FROM
# the directory you launch `claude` from: the memory symlink is keyed to
# the invocation directory (see the project-key note below).
# Existing targets are backed up with a .bak suffix before being replaced,
# never silently overwritten.
#
# Usage:
#   install/mac.sh                 single-machine setup (no branch sync)
#   install/mac.sh --sync pc       multi-machine: merge origin/pc at session
#                                  start (comma-separate multiple branches)
set -euo pipefail

sync_branches=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --sync)
            sync_branches="${2:-}"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: install/mac.sh [--sync <branch>[,<branch>...]]" >&2
            exit 1
            ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"
claude_dir="$HOME/.claude"

# Preflight: confirm we can actually create a symlink at the target location
# before touching any real config. macOS/Linux don't need elevation for this
# the way Windows does, but a read-only home dir or odd permission setup
# should still fail loudly up front instead of mid-removal.
mkdir -p "$claude_dir"
preflight_target="$claude_dir/.symlink-test-$$"
if ! ln -s "$repo_root" "$preflight_target" 2>/dev/null; then
    echo "Cannot create symlinks in $claude_dir. Nothing has been touched." >&2
    echo "Check permissions on $claude_dir and re-run." >&2
    exit 1
fi
rm -f "$preflight_target"

items=(CLAUDE.md settings.json commands agents rules hooks)

replace_with_symlink() {
    local target="$1" source="$2" label="$3"

    if [ -L "$target" ]; then
        rm "$target"
    elif [ -e "$target" ]; then
        mv "$target" "$target.bak"
        echo "Backed up existing $label to $label.bak"
    fi

    ln -s "$source" "$target"
    echo "Linked $label"
}

for item in "${items[@]}"; do
    replace_with_symlink "$claude_dir/$item" "$repo_root/$item" "$item"
done

# Cross-machine session memory: link this project's memory dir to the
# repo's memory/ so memories written on any machine sync through git.
# Claude Code derives the project key from the session's working directory,
# NOT the home dir: a session launched from /Users/you/claude gets key
# "-Users-you-claude". So run this script from whatever directory you
# actually launch `claude` from on this machine, and re-run it if that
# changes. (Same fix windows.ps1 got after the bare-C:\ gotcha.)
memory_source="$repo_root/memory"
project_key="$(pwd | tr '/' '-')"
memory_target="$claude_dir/projects/$project_key/memory"

mkdir -p "$(dirname "$memory_target")"
replace_with_symlink "$memory_target" "$memory_source" "memory"

# Record where the repo lives and how this machine syncs, so the hooks never
# need hard-coded paths. If the repo ever moves, re-run this installer.
machine_branch="$(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
cat > "$claude_dir/workbench.conf" <<EOF
REPO_PATH=$repo_root
MACHINE_BRANCH=$machine_branch
SYNC_BRANCHES=$sync_branches
EOF
echo "Wrote workbench.conf (branch: ${machine_branch:-unknown}, sync: ${sync_branches:-none})"
