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

Before first use, open `web-qa-crawler.md` and add the owner's deployed
staging hosts to the authorized-targets gate, and open `qa-cleanup-crew.md`
and replace the projects-root placeholder. Both list `<...>` placeholders
for exactly this.