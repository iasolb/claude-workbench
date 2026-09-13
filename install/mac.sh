#!/usr/bin/env bash
# Installs THIS repo (the private memory bank) into ~/.claude on a Mac.
#
# WHY THIS EXISTS, and why it is not the workbench's installer (2026-09-01):
# claude-workbench/install/mac.sh links from the WORKBENCH root, which is the
# public generalized mirror. Running it here would replace Ian's authoritative
# CLAUDE.md, settings.json and rules/ with the template's placeholders. The
# machines run THIS repo's content, so this repo needs its own installer.
#
# It was written after the workspace moved to /Users/ian/dev/ai-kit and every
# ~/.claude symlink was left dangling, silently: no CLAUDE.md, no rules, no
# permission list, no hooks, for an unknown number of sessions. That repair was
# done by hand, which is golden rule A3's definition of evidence that a routine
# is missing. This is the routine.
#
# Safe to re-run. Existing symlinks are replaced; real files are moved aside to
# .bak rather than overwritten.
#
# Usage:
#   install/mac.sh                          link config, key memory to $PWD
#   install/mac.sh --sync windows,phone     merge those branches at session start
#   install/mac.sh --project-dir /Users/ian/dev [--project-dir ...]
#
# --project-dir is the one that bites. Claude Code derives a project key from
# the directory `claude` was LAUNCHED from, not from $HOME, so the memory
# symlink is per launch directory. Moving the workspace makes a NEW key whose
# memory dir is empty and NOT in git, so memory writes land outside version
# control and quietly never sync. Pass every directory you launch from.
set -euo pipefail

sync_branches=""
project_dirs=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sync)
            sync_branches="${2:-}"
            shift 2
            ;;
        --project-dir)
            project_dirs+=("${2:-}")
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: install/mac.sh [--sync <branch>[,<branch>...]] [--project-dir <dir>]..." >&2
            exit 1
            ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"
claude_dir="$HOME/.claude"

# A submodule's .git is a FILE, not a directory. Testing for a directory is the
# bug that made six hooks exit 0 in silence on this machine; -e covers a plain
# clone and a submodule both.
if [ ! -e "$repo_root/.git" ]; then
    echo "Not a git repo: $repo_root. Nothing has been touched." >&2
    exit 1
fi

# Confirm we can create a symlink at the target before touching real config.
mkdir -p "$claude_dir"
preflight_target="$claude_dir/.symlink-test-$$"
if ! ln -s "$repo_root" "$preflight_target" 2>/dev/null; then
    echo "Cannot create symlinks in $claude_dir. Nothing has been touched." >&2
    echo "Check permissions on $claude_dir and re-run." >&2
    exit 1
fi
rm -f "$preflight_target"

items=(CLAUDE.md settings.json commands agents rules hooks)

# Every item must exist in the repo before anything is unlinked. A partial
# install leaves the framework half-loaded, which is worse than not running.
missing=()
for item in "${items[@]}"; do
    [ -e "$repo_root/$item" ] || missing+=("$item")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing in $repo_root: ${missing[*]}. Nothing has been touched." >&2
    exit 1
fi

# Resolve the branch BEFORE touching anything. This check found its own bug in
# self-test: run after the linking, a detached checkout got fully linked and
# then aborted, leaving exactly the half-install this script warns about.
machine_branch="$(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
if [ "$machine_branch" = "HEAD" ] || [ -z "$machine_branch" ]; then
    echo "REFUSING: $repo_root is in DETACHED HEAD. Nothing has been touched." >&2
    echo "  A submodule checkout does this, and it can sit hundreds of commits" >&2
    echo "  behind its branch while looking healthy. Check out the machine" >&2
    echo "  branch and re-run: git -C \"$repo_root\" checkout mac && git -C \"$repo_root\" pull" >&2
    exit 1
fi

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

# Default to the invocation directory, matching the workbench installer's
# behaviour, but always also cover the repo itself so a session started inside
# the bank writes memory into git rather than into a stray directory.
if [ ${#project_dirs[@]} -eq 0 ]; then
    project_dirs=("$PWD")
fi
project_dirs+=("$repo_root")

for dir in "${project_dirs[@]}"; do
    abs="$(cd "$dir" 2>/dev/null && pwd)" || {
        echo "Skipping --project-dir $dir: not a directory" >&2
        continue
    }
    project_key="$(printf '%s' "$abs" | tr '/' '-')"
    memory_target="$claude_dir/projects/$project_key/memory"
    mkdir -p "$(dirname "$memory_target")"
    replace_with_symlink "$memory_target" "$repo_root/memory" "memory ($abs)"
done

# Record where the repo lives and how this machine syncs, so the hooks never
# need hard-coded paths. If the repo moves, re-run this installer.
cat > "$claude_dir/workbench.conf" <<EOF
REPO_PATH=$repo_root
MACHINE_BRANCH=$machine_branch
SYNC_BRANCHES=$sync_branches
EOF
echo "Wrote workbench.conf (branch: ${machine_branch:-unknown}, sync: ${sync_branches:-none})"

# Prove the install rather than assume it: a dangling symlink is exactly the
# failure this script exists to prevent, and it is invisible without a check.
broken=()
for item in "${items[@]}"; do
    [ -e "$claude_dir/$item" ] || broken+=("$item")
done
if [ ${#broken[@]} -gt 0 ]; then
    echo "FAILED: these links do not resolve: ${broken[*]}" >&2
    exit 1
fi
echo "Verified: all ${#items[@]} links resolve."
