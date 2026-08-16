---
description: Review an API-wrapper/loader project against the established house conventions (naming layer, catalog, discovery helpers, rate-limit batching, guard patterns)
argument-hint: [path to the project or file being reviewed]
---

Use the Agent tool with `subagent_type: api-wrapper-reviewer` to review the
code at `$ARGUMENTS` (if no path was given, ask which project/file to review
rather than guessing). Pass along any context from this conversation the
agent would need (what's new/changed, what it's meant to do, and where the
project's reference wrapper implementations live if they are not
discoverable). Relay its findings back directly, don't re-summarize them
into something vaguer.