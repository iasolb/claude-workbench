# Git / GitHub

The shape here is one table plus one principle: **writes that other people can
see are gated, writes that only I can see are not.** Fill in the table with
your own repos; what matters is that it is written down rather than worked out
per commit.

## Who may do what

| Repo | Commit and push | Open a PR | Merge a PR |
|---|---|---|---|
| the config/memory repo (this one) | Claude | not used, it is direct commit-and-push | not used |
| project repos you have granted | Claude | Claude | **you only** |
| everything else | you | you | you |

- **Read-only git is always fine, unprompted:** `status`, `diff`, `log`,
  `gh pr view`, `gh pr list`.
- **Remote-mutating calls stay yours** unless the table says otherwise: issue
  creation and comments, releases, and merges. Enforce it at the permission
  layer in `settings.json`, not just in prose, and remember that a deny
  written in `Bash(...)` form cannot match the same command routed through an
  MCP tool. Keep git and forge calls on the plain shell tools.
- **Force-push and history rewriting are never covered by intent.** Ask first.
- **A branch push is NOT a pull request.** If a driver can only push, the
  handoff sentence is "open the PR", never "merge the PR". Check whether one
  exists before you claim it does; it is one read-only call.
- **Link the PR directly, every time.** Its full URL, in chat, in the card
  Report and in the completion email. A PR named in prose is useless to
  someone reading on a phone.

## App repos: feature branch, then a PR

```
every card    feature branch --PR--> main
```

- **Name the branch for THE WORK, not the card**: `security-csrf-and-cookies`,
  not `order-11`. Branch names get read on a phone.
- Cut the branch at CLAIM time, before the first commit, off the card's
  declared base. If a card names a non-default base, check that base is
  current with `main` first and say so in the Report: a stale base is how a
  card silently loses the work it depends on.
- One-line commit subjects.

### The unattended lane: ONE branch for the whole night

The rule above is the AWAKE rule, and it depends on somebody merging each PR
as it lands. At 02:00 nobody is merging, so a chained lane either blocks or
reaches for a merge it is not allowed to do.

```
authorized unattended lane   ONE branch, every card commits onto it --one PR--> main
```

- The lane branch is cut ONCE, off `main`, by the first card, and named for
  the NIGHT'S WORK rather than any card. Every later card checks it out and
  commits onto it. Nothing merges, so nothing can prompt, and card N+1 has
  card N's code by construction.
- **One commit per card, subject written so the night reads as a list.** That
  is what you give up by not getting a PR per card, so the messages carry it.
- One PR at the end, opened by the last card, with its full URL in the
  completion email.
- A card that goes wrong still ENDS the lane. The branch stays, the PR is
  opened for what did land, and the Report says where it stopped.
- Deliberately not the default. Which mode applies is a fact the card carries,
  never something a driver works out at runtime.

## Several sessions share one working tree. That is normal, not an incident.

- **Commit by pathspec, always:**
  `git -C <repo> commit -m "<one line>" -- <path> <path>`.
  Plain `git commit` commits the whole INDEX, including files another session
  staged, and `git add -A` stages their in-progress edits.
- **Pathspec is necessary and NOT sufficient.** `commit -- <path>` commits the
  WORKING TREE state of that path, so a file another session is mid-edit on
  gets committed half-finished under your message. Pathspec beats the index,
  not the shared file. The fix is ownership: do not commit paths you do not
  own. Need to change one anyway? Make the edit, leave it UNCOMMITTED for its
  owner, and say so in the wrap-up.
- **`commit -- <path>` SILENTLY SKIPS UNTRACKED FILES**, and says nothing
  about it. A pass that CREATES files runs `git add <path>` first, then
  commits by pathspec. `git status --short` before you claim done: a `??` line
  is the only tell you get.
- **`commit -- <path>` also silently drops a `--chmod` mode change**, because
  it re-reads the working tree and a filesystem without an exec bit reports
  none. Stage the mode, then use an index-based commit (`commit --amend
  --no-edit` on a commit you have not pushed) and check the mode in the
  output.
- Re-read a config file immediately before editing it. An earlier read in the
  same session is stale.

## Branches

Each machine lives permanently on its own branch so pushes never collide, and
a SessionStart hook fetches and merges the sibling branches, so memory
converges every session. Conflicts are yours to resolve immediately: keep BOTH
sides' facts, dedupe the index file, commit, push. Any session that touches
memory or config commits and pushes on its machine branch; never leave things
staged or unpushed.

Branch create/delete, reset, add, stash, or cleaning up local state I have
messed up: fine when actually needed, but say what you are about to do first,
same as any hard-to-reverse action.
