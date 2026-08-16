---
description: Drive any website or web app through its flows and report friction points per flow (read-only, never fixes)
argument-hint: [base URL] [flows to walk, or a path to a flow map]
---

Use the Agent tool with `subagent_type: web-qa-crawler` to QA the target in
`$ARGUMENTS`.

Before launching, make sure the agent is handed two things, since it will
stop and ask if either is missing:

- **Base URL**: where the app is actually running. If the owner named a
  project rather than a URL, work out the running URL from the project (a
  staging host, a docker-compose port, a dev server) and pass that. If it is
  not running, say so rather than launching the agent against nothing.
- **A flow map**: the journeys to walk. The owner may give these inline
  ("signup, checkout"), point at a file, or give none. If none were given,
  propose a short flow list from what you know about the app and confirm it
  in one question, rather than letting the agent invent its own scope.

Pass along anything else already known from this conversation that saves the
agent rediscovery: audience, test credentials, what "done" means for a flow,
areas recently changed and therefore worth extra attention, and known issues
it should not re-report. Also tell it where to write the report.

Relay the agent's findings back directly, keeping the per-flow structure and
severities intact. Do not summarize them into something vaguer, and do not
start fixing anything: fixes go through `/qa-cleanup` after the owner marks
findings approved.