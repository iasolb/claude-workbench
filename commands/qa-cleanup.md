---
description: Apply the QA findings the owner marked approved in a crawler report (skips everything unapproved)
argument-hint: [path to the QA report]
---

Use the Agent tool with `subagent_type: qa-cleanup-crew` to apply approved
fixes from the report at `$ARGUMENTS` (if no path was given, look for the
most recent `reports/qa-*.md` and confirm it is the right one rather than
guessing).

First, read the report yourself and count the findings marked
`- [x] APPROVED`. If there are none, say so and stop: do not launch the
agent, and do not suggest which ones should be approved unless asked. The
report is the owner's to mark.

When there are approved findings, tell the agent the project's commit
policy (allowed or leave-uncommitted) and how to run the project's tests,
so it does not have to rediscover either. Remind it of the workdir
boundary if the project spans more than one repo.

Relay its report back: what was fixed, what turned out bigger than
advertised and was skipped, what it spotted but did not touch. That last
list is the input to the next approval round, so keep it intact rather
than trimming it.