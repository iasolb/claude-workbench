#!/usr/bin/env bash
# SessionStart hook: print non-README Markdown files from urgent/ in full.
# WORKBENCH_CONF defaults to ~/.claude/workbench.conf and defines REPO_PATH;
# MACHINE_BRANCH optionally selects a remote branch to include.
case "$(uname)" in MINGW*|MSYS*|CYGWIN*) exit 0 ;; esac
CONF="${WORKBENCH_CONF:-$HOME/.claude/workbench.conf}"
[[ -f "$CONF" ]] || exit 0
value() { sed -n "s/^$1=//p" "$CONF" | tr -d '\r' | head -n 1; }
REPO="$(value REPO_PATH)"
[[ -d "$REPO/urgent" ]] || exit 0
BRANCH="$(value MACHINE_BRANCH)"
[[ -n "$BRANCH" ]] || BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
shopt -s nullglob
NAMES=()
for file in "$REPO"/urgent/*.md; do
    name="$(basename "$file")"
    [[ "$name" == README.md ]] || NAMES+=("$name")
done
if [[ -n "$BRANCH" ]]; then
    while IFS= read -r entry; do
        name="$(basename "$entry")"
        [[ "$name" == README.md || "$name" != *.md ]] && continue
        found=0
        for have in "${NAMES[@]}"; do [[ "$have" == "$name" ]] && found=1 && break; done
        [[ $found -eq 1 ]] && continue
        git -C "$REPO" cat-file -e "origin/$BRANCH:urgent/$name" 2>/dev/null && NAMES+=("$name")
    done < <(git -C "$REPO" ls-tree --name-only "origin/$BRANCH" urgent/ 2>/dev/null)
fi
[[ ${#NAMES[@]} -gt 0 ]] || exit 0
IFS=$'\n' NAMES=($(printf '%s\n' "${NAMES[@]}" | sort)); unset IFS
printf '[urgent] %s item(s) in urgent/. Read them before the ordinary task list. Fold each item into its permanent home and remove it in the same session.\n' "${#NAMES[@]}"
for name in "${NAMES[@]}"; do
    printf '[urgent] --- urgent/%s ---\n' "$name"
    if [[ -f "$REPO/urgent/$name" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do printf '[urgent] %s\n' "$line"; done < "$REPO/urgent/$name"
    else
        git -C "$REPO" show "origin/$BRANCH:urgent/$name" 2>/dev/null | while IFS= read -r line || [[ -n "$line" ]]; do printf '[urgent] %s\n' "$line"; done
    fi
done
exit 0
