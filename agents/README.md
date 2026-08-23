# agents/

Custom subagent definitions (see the Claude Code docs on subagents). The
installer symlinks this directory into `~/.claude` so any agent you add
here is versioned and follows you across machines.

Five subagents ship, all read-only or approval-gated by design:

| Agent | What it is for |
|---|---|
| `web-qa-crawler` | Drives a web app through its flows in the sandboxed browser and reports friction points. Never edits. |
| `qa-cleanup-crew` | Applies only the fixes the owner explicitly approved in a QA report. Never decides what gets touched. |
| `abstraction-pattern-reviewer` | Reviews class/module design against the house abstraction conventions. Read-only. |
| `api-wrapper-reviewer` | Reviews API-wrapper projects against the house conventions. Read-only. |
| `ux-auditor` | Checks a screen against its audience's UX pattern. Read-only, and it never blocks styling freedom. |
| `intake` | Writes one complete job order from an approved objective, including literal commands and permission pre-flight. Stops without launching or executing work. |
| `fast-lane` | Fixes Claude process bugs and permission-list gaps. It is the only agent allowed to edit the allow/deny list. |
| `cleanup-crew` | Keeps the queue, memory, reports, rules, and mirrored workload issue consistent without touching the live Fast Lane inbox. |
| `pathfinder` | Turns actionable standing priorities into at most one small order per goal and records blockers without inventing work. |

Before first use, open `web-qa-crawler.md` and add the owner's deployed
staging hosts to the authorized-targets gate, and open `qa-cleanup-crew.md`
and replace the projects-root placeholder. Both list `<...>` placeholders
for exactly this.
