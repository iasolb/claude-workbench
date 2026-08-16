#!/usr/bin/env bash
# SessionStart hook: print everything in urgent/ into session context, in
# full, on every machine. That directory is the maintainer's fast lane from
# a phone: new standing rules and organization jobs that must not wait for a
# session to go looking. macOS side; Windows runs urgent-print.ps1.
# Prints [urgent] lines and always exits 0: a broken printer must never
# block a session.

case "$(uname)" in
    MINGW*|MSYS*|CYGWIN*) exit 0 ;;
esac

CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
REPO="$(sed -n 's/^REPO_PATH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$REPO" && -d "$REPO/urgent" ]] || exit 0
BRANCH="$(sed -n 's/^MACHINE_BRANCH=//p' "$CONF" | tr -d '\r' | head -n 1)"
[[ -n "$BRANCH" ]] || BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"

# Notes come from TWO independent sources, deduped by filename: the working tree,
# and this machine's own remote branch. A session-start merge that no-ops on a
# dirty tree leaves a pushed note absent from the tree, and a tree-only glob then
# prints nothing, which swallows the fast lane silently: a sync fix cannot
# deliver itself. The tree still wins for a note that exists locally and has not
# been pushed yet. Every git call fails open: the tree list stands, and the hook
# still exits 0, because a SessionStart hook that errors can block a session.
shopt -s nullglob
NAMES=()
for f in "$REPO"/urgent/*.md; do
    name="$(basename "$f")"
    [[ "$name" == "README.md" ]] && continue
    NAMES+=("$name")
done

if [[ -n "$BRANCH" ]]; then
    while IFS= read -r entry; do
        name="$(basename "$entry")"
        [[ "$name" == "README.md" || "$name" != *.md ]] && continue
        for have in "${NAMES[@]}"; do
            [[ "$have" == "$name" ]] && continue 2
        done
        git -C "$REPO" cat-file -e "origin/$BRANCH:urgent/$name" 2>/dev/null || continue
        NAMES+=("$name")
    done < <(git -C "$REPO" ls-tree --name-only "origin/$BRANCH" urgent/ 2>/dev/null)
fi

[[ "${#NAMES[@]}" -gt 0 ]] || exit 0
IFS=$'\n' NAMES=($(printf '%s\n' "${NAMES[@]}" | sort)); unset IFS

echo "[urgent] ${#NAMES[@]} item(s) in urgent/. This is the fast lane from a phone: read them NOW, before the queue, and honor any rule they state. Clear an item by folding it into its permanent home (rules/, memory/, a job card) and deleting the file, in the same session you act on it."
for name in "${NAMES[@]}"; do
    echo "[urgent] --- urgent/$name ---"
    if [[ -f "$REPO/urgent/$name" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            echo "[urgent] $line"
        done < "$REPO/urgent/$name"
    else
        git -C "$REPO" show "origin/$BRANCH:urgent/$name" 2>/dev/null |
            while IFS= read -r line || [[ -n "$line" ]]; do
                echo "[urgent] $line"
            done
    fi
done

exit 0
