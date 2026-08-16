# claude-workbench

A template for versioning your global Claude Code configuration in git:
instructions, rules, settings, hooks, and Claude's persistent memory, with
optional cross-machine sync so Claude remembers the same things everywhere
you work.

## What you get

- **Versioned `~/.claude`**: your `CLAUDE.md`, `settings.json`, `rules/`,
  `commands/`, `agents/`, and `hooks/` live in a git repo and are symlinked
  into place, so every edit is tracked and portable. Credentials, caches,
  and session state stay out of the repo by construction: only the symlinked
  items are versioned.
- **Persistent memory that survives machines**: Claude Code's auto-memory
  directory is symlinked into the repo's `memory/`, so facts Claude saves on
  one machine reach the others through git.
- **Session hooks**: a SessionStart hook runs doctor checks (is the memory
  symlink healthy? is the checkout on the right branch?) and, in
  multi-machine mode, merges the other machines' branches so memory
  converges at the start of every session. A SessionEnd hook stages (never
  commits) whatever changed, and a Notification hook raises a desktop
  notification when Claude is waiting on you.
- **An optional priority queue**: a `queue/` directory holding your standing
  priorities and the in-flight state of whatever's being worked. A
  SessionStart hook prints it into context so fresh sessions start on the
  right work without prompting, and a Stop hook nudges Claude to write the
  arc state back before long sessions end.

Everything works on macOS, Linux, and Windows. Each hook ships in both `.sh`
and `.ps1` form; `settings.json` wires up both and each script no-ops on the
other platform.

## Quick start (single machine)

1. Click **Use this template** on GitHub and create a **private** repo.
   Private matters: your instructions and Claude's memory about you will
   live here.
2. Clone it wherever you keep long-lived tooling, for example
   `~/claude/claude-workbench`.
3. Run the installer:
   - macOS/Linux: `install/mac.sh`
   - Windows (needs Developer Mode or an elevated prompt, and run it from
     the directory you usually launch `claude` from): `install\windows.ps1`
4. Edit `CLAUDE.md`, read through `rules/` and keep the ones you want, and add
   a matching `@rules/<topic>.md` import line to `CLAUDE.md` for each survivor.

The installer symlinks each tracked item into `~/.claude` (existing files
are backed up with a `.bak` suffix, never silently overwritten), links your
project's auto-memory directory to `memory/`, and writes
`~/.claude/workbench.conf` recording where the repo lives. If you ever move
the repo, just re-run the installer.

## Layout

- `CLAUDE.md`: global instructions, loaded in every session
- `settings.json`: permissions and hooks
- `rules/`: standing rules, one file per topic, imported by `CLAUDE.md`;
  split into `shared/` (every tool reads them) and `claude/` (Claude Code
  only), see below
- `commands/`: custom slash commands, including the five personas and five
  lightweight commands described below
- `agents/`: custom subagents (empty to start)
- `hooks/`: every session hook, each shipped as a `.sh`/`.ps1` pair
- `tools/`: the syntax checker and the two hook case matrices (see
  `tools/README.md`)
- `memory/`: Claude's persistent memory (`MEMORY.md` index plus one file per
  fact, written by Claude)
- `examples/queue/`: starter queue files (see "The priority queue" below)
- `install/`: the per-platform installers
- `docs/`: troubleshooting

## The priority queue (optional)

A lightweight standing to-do that survives sessions and machines, designed
around one habit: a fresh session should start on the right work without you
re-explaining where things stand.

- `queue/global.md` holds numbered priorities, hand-ordered by you. The
  format contract: each item's first line is a self-contained headline,
  because the SessionStart hook prints only the headlines.
- `queue/current.md` holds the in-flight state of the item being worked (the
  "arc"), so a fresh session resumes mid-task without re-deriving anything.
  On completion it's cleared back to a stub and durable outcomes move to
  `memory/`.
- `hooks/queue-print.*` (SessionStart) prints headlines + the current arc
  into context; `hooks/queue-nudge.*` (Stop) nudges Claude, at most once per
  session, when a substantial session ends without `current.md` being
  updated.

Opt in by copying `examples/queue/` to `queue/` in the repo root and
committing; the hooks stay silent until that directory exists. Claude
maintains the files from there: it annotates and removes items as work
completes and keeps the arc current, while the ordering stays yours.

## The rules

`rules/` ships nine standing rules, one file per topic, each imported by
`CLAUDE.md` with an `@rules/<topic>.md` line. They cover memory integrity, git
and PR authority, the job-card pipeline, permission loops, how Claude should
talk to you, session workspace layout, documentation depth, dev-tooling
decisions, and planning mode. `rules/README.md` has a one-line summary of each
and the split into `shared/` and `claude/`.

They are **opinionated on purpose**: a rule file that hedges gives a session
nothing to act on. Read them as a starting position, keep what fits, edit what
does not, and delete the rest. Only files you import from `CLAUDE.md` are
loaded, so an unimported rule costs nothing but disk.

Every one of them is a rule that exists because something went wrong without
it. The incident is not in the file, but the failure SHAPE it prevents is,
because that is the part that transfers.

## The personas (optional)

`commands/` ships a working division of labour rather than a description of
one. **A session is exactly ONE persona**, chosen when you start it. That is
the whole point: each one has a different reading list, a different scope, and
a different idea of when it is finished, and a session that tries to be two of
them does neither job well.

| Command | What it is for |
|---|---|
| `/quarterback` | The senior seat. Structural faults, security and data integrity, tradeoffs talked through with you. Runs the cleanup pass inline at the end of its turn. |
| `/intake` | Turns a request into a job card, including the permission pre-flight that names every command the card will run. Never works the card it writes. |
| `/driver` | Works ONE card, runs its test gate, pastes real output as evidence, then stops. |
| `/fast-lane` | Process bugs and permission holes. The only writer of the allow/deny list, and the lane your screenshots go to. Meant to run in parallel with the others. |
| `/cleanup-crew` | Keeps the queue, the memory files and the persona logs honest: converge, prune, guard priority, fold yesterday's logs into one. |

Five lightweight commands sit alongside them and are NOT personas, so they
have no turn report and no wrap-up ritual: `/debug`, `/explain`, `/find`
(your own docs), `/research` (the open web), and `/scaffold` (structure and
stubs only, never implementations).

The personas reference `rules/claude/jobs.md`, `rules/claude/permission-loops.md`,
`rules/shared/style.md` and `docs/command-forms.md`. Ship those alongside them, or
edit the references out: a persona that points at a rule file you do not have
is a pointer with nothing to point at.

## Multi-machine sync (advanced)

Each machine lives permanently on its own branch, and every session starts
by merging the other machines' branches. To set it up:

1. On your first machine, create and switch to a branch named for it (for
   example `git switch -c desktop`), then install with the other machines'
   branch names: `install/mac.sh --sync laptop` (Windows:
   `install\windows.ps1 -Sync laptop`).
2. Push the branch: `git push -u origin desktop`.
3. Repeat on each other machine with its own branch name and sync list.

The installer records the current branch as this machine's home branch, so
create the branch before installing (or re-run the installer after).

### Why a branch per machine

With every machine committing to one shared branch, two concurrent sessions
race: both pull, both commit, one push is rejected mid-session and somebody
has to rebase at the worst possible time. With a branch per machine, pushes
never collide. Convergence happens at the only moment it is actually needed,
session start, where the sync hook fetches and merges the other branches
(and reports conflicts for resolution instead of failing silently). Git's
merge machinery does the deduplication, and one-fact-per-file memory keeps
merges conflict-free in practice; the `MEMORY.md` index is the only file two
machines regularly both touch.

## Giving Claude git autonomy here (optional)

The hooks only ever stage. If you want Claude to run the full sync loop
itself (merge, resolve conflicts, commit, push) in this repo and nowhere
else, add scoped allow rules to `settings.json`, adjusting the path to your
clone, in whichever spellings match how you run Claude Code:

```json
"Bash(git -C ~/claude/claude-workbench:*)",
"PowerShell(git -C $env:USERPROFILE\\claude\\claude-workbench:*)"
```

The `deny` block that ships in `settings.json` blocks remote-mutating `gh`
commands (PR/issue/release creation) regardless, and `rules/claude/git-github.md` is
the matching rule, with a table to fill in for your own repos. Whether to grant
this at all is a judgment call; everything works with you running `git commit`
and `git push` yourself.

## What stays out of the repo

`~/.claude` also holds credentials, session transcripts, shell snapshots,
and caches. None of that is symlinked from the repo, so none of it can be
committed. The repo never contains secrets unless you put them there; keep
API keys and tokens in their usual homes, not in `settings.json` or rules.

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md).

## License

MIT.
