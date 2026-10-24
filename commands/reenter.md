---
description: Resume work from a written handoff without re-deriving its findings
---

# /reenter

Continue from a handoff left by a previous session.

1. Read the handoff file named by the user, or the clearly identified latest
   handoff in the repository. Treat its recorded measurements as the previous
   session's evidence, not as current state for facts that can change.
2. Run the handoff's first-action steps in their stated order. If it has no
   first-action steps, inspect the repository and issue state before continuing.
3. Refresh issue-backed job state with `python tools/issue-job.py sync` when
   configured, and check active agent work with
   `python tools/agents-in-flight.py`.
4. Verify mutable facts that affect the next action: branch status, open pull
   requests, current checks, and any stated usage limit.
5. Continue only the unfinished work named by the handoff. Preserve settled
   decisions unless new evidence contradicts them, and record that evidence.

Do not repeat completed work or report an agent as idle when its status could
not be checked.
