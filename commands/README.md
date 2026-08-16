# commands/

Custom slash commands (see the Claude Code docs on custom commands). The
installer symlinks this directory into `~/.claude` so any command you add
here is versioned and follows you across machines.

Ten commands ship: five personas (`/quarterback`, `/intake`, `/driver`,
`/fast-lane`, `/cleanup-crew`), five lightweight lookups (`/debug`,
`/explain`, `/find`, `/research`, `/scaffold`), plus four QA/review tools:

| Command | What it is for |
|---|---|
| `/qa-crawl` | Drive any web app through its flows and report friction. Read-only. |
| `/qa-cleanup` | Apply only the approved findings from a `/qa-crawl` report. |
| `/review-abstraction` | Review class/module design against the house conventions. |
| `/review-api-wrapper` | Review an API-wrapper project against the house conventions. |
| `/audit-ux` | Audit a screen against its audience's UX pattern. |

The QA/review commands delegate to the subagents in `agents/`; ship those
alongside, or edit the references out.