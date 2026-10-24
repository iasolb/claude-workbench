#!/usr/bin/env bash
# Source from hooks that contact Git remotes. This module disables interactive
# credential prompts and bounds slow transfers without requiring GNU timeout.
#
# Exports GIT_NOHANG_ERR from git_remote with git's original exit status.
# file_mtime accepts one path and prints its modification time in epoch seconds;
# an unreadable path prints zero, so callers must not treat that as a valid age.

export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/usr/bin/true
export SSH_ASKPASS=/usr/bin/true
export SSH_ASKPASS_REQUIRE=never
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

GIT_NOHANG=(-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15)

git_remote() {
    GIT_NOHANG_ERR="$(git "${GIT_NOHANG[@]}" "$@" 2>&1)"
}

file_mtime() {
    case "$(uname 2>/dev/null)" in
        Darwin|*BSD) stat -f %m "$1" 2>/dev/null || printf '0\n' ;;
        *)           stat -c %Y "$1" 2>/dev/null || printf '0\n' ;;
    esac
}
