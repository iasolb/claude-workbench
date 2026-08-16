---
description: Audit a screen/template against its audience's UX pattern (field/mobile = dead-simple mobile-first, desktop = dense) and protect styling freedom
argument-hint: [template or route file being audited]
---

Use the Agent tool with `subagent_type: ux-auditor` to audit the screen at
`$ARGUMENTS` (if no path was given, ask which screen/template to audit
rather than guessing). Tell the agent which audience the screen is meant to
serve if that's already known from this conversation, otherwise let it work
that out from the code. Relay its findings back directly, don't re-summarize
them into something vaguer.