# Troubleshooting

## The repo moved and things quietly stopped working

Symlinks don't follow the things they point at. If you move or rename your
workbench clone, every symlink in `~/.claude` still points at the old path:
settings load stale (or not at all), hooks stop firing, and, worst of all,
**memory writes land in a dead directory git never sees**. This fails
silently; nothing errors, memories just stop syncing.

Fix: re-run the installer from the repo's new location. It re-points every
symlink and rewrites `~/.claude/workbench.conf`. The SessionStart doctor
exists precisely to catch this: if a session starts printing
`[workbench] DOCTOR:` lines, read them, they say exactly what is broken and
how to fix it.

## Windows: installer fails at the symlink preflight

Creating symlinks on Windows requires either Developer Mode (Settings >
Privacy & Security > For Developers) or an elevated (Run as Administrator)
PowerShell. The installer tests this up front and touches nothing if the
test fails, so just enable one of the two and re-run.

## Windows: memory link points at the "wrong" project

Claude Code keys auto-memory to the directory a session is launched from.
On macOS/Linux the installer assumes your home directory; on Windows it uses
the directory you run the installer from. If you launch `claude` from
somewhere else (say, always from `C:\`), run the installer from there, or
re-run it after changing your habits. The doctor check will tell you when
the link and your actual launch directory disagree.

## Hooks aren't firing

`settings.json` must be the symlink into the repo for the hook wiring to be
active. Check `~/.claude/settings.json` is a link (not a stale `.bak`
promotion or a hand-written copy), and that `~/.claude/hooks` resolves into
the repo. Re-running the installer fixes both.

## Merge conflicts in multi-machine mode

The SessionStart hook merges the other machines' branches and deliberately
leaves conflicts in the worktree for you (or Claude) to resolve immediately:
keep both sides' facts, dedupe `MEMORY.md`, commit, push. One-fact-per-file
memory keeps this rare; `MEMORY.md` is the usual (and easy) conflict.

## "fetch failed (offline?)" at session start

Harmless. The hook works from local state and says so; the next online
session converges as usual.
