---
description: Begin a work session with its goal, current state, and available checks
---

# /start

Use this as the consistent opening for a new work session.

1. Confirm the repository, requested goal, and allowed file scope. If the goal
   is already explicit, do not ask the user to repeat it.
2. Check cost and time constraints before choosing a metered service. Use
   `python tools/run-cost.py --self-test` to verify the run-cost reader; use the
   provider's current usage page for live account limits.
3. Inspect the working tree and current branch before editing. Preserve all
   existing changes.
4. Read the relevant open issues and pull requests. For issue-backed jobs, run
   `python tools/issue-job.py sync` when the store is available.
5. Check whether another configured agent is working or awaiting review with
   `python tools/agents-in-flight.py`. Treat a failed check as unknown, not
   idle.
6. Ask only for information no repository file or tool can establish. If the
   task is clear, start it instead of turning the opening into a planning
   ritual.

For a measured framework overview, `python tools/health.py` reports supported
health indicators. Its output describes only the sources it could read.
