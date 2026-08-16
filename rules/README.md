# rules/

Standing rules, one file per topic, each imported by `CLAUDE.md` with an
`@rules/<topic>.md` line. One-topic-per-file keeps rules easy to update and
easy for Claude to cite. This README is not imported; only files referenced
from `CLAUDE.md` are loaded.

Nine rules ship here, in the voice of the repo owner speaking to Claude. They
are opinionated on purpose: a rule file that hedges gives a session nothing to
act on. **Read them as a starting position, not as law.** Keep what fits, edit
what does not, delete the rest, and add your `@rules/...` import lines to
`CLAUDE.md` for whatever survives.

| File | What it settles |
|---|---|
| `memory-integrity.md` | How a memory repo stays honest: the owner outranks memory, capability claims carry their evidence, one fact lives in one place, finished work is deleted rather than annotated. |
| `git-github.md` | Who may commit, push, open a PR and merge, per repo. Branch-per-machine sync, the unattended-lane branching model, and the traps of several sessions sharing one working tree. |
| `jobs.md` | The card pipeline: how a request becomes a self-contained card, how a card gets worked and evidenced, what may run unattended, and the hard rule that Claude never authors its own cards. |
| `permission-loops.md` | Why a command prompts and what actually fixes it. What the permission matcher does, the six failure modes in diagnosis order, and the forms no allowlist entry can ever rescue. |
| `style.md` | How Claude talks to you: no internal ids standing in for meaning, questions asked in forms, links given directly, and every turn ending in one concrete action. |
| `session-dirs.md` | The three-folder session workspace: your drop zone, Claude's scratch space, and where deliverables land. |
| `documentation.md` | Docstring and comment depth, and where long-form rationale belongs instead. |
| `dev-practices.md` | The tooling decisions that are decisions rather than defaults, and why they need writing down. |
| `planning-mode.md` | What changes when you say "enter planning": no edits, delegate exploration, decisions not narration, then one batched implementation pass. |

The starters that used to live in `examples/rules/` are folded into
`git-github.md`, `session-dirs.md` and `documentation.md`; that directory is
gone rather than left as a second, staler copy.
