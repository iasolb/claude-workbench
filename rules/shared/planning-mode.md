# Planning mode

Trigger: when I say "enter planning" (or a clear synonym signaling the same
intent), switch into planning mode for the rest of the conversation until I
say "exit planning" (or an unambiguous equivalent).

## While in planning mode

- Use the plan-mode tool if it is not already engaged. Keeping edits and
  writes off the table until the plan is approved is the point: nothing gets
  implemented piecemeal mid-discussion.
- This phase is for thoroughly working through what we are building, why, the
  shape of it, open decisions, tradeoffs, and open questions, not writing code
  or scaffolding files yet. Use `AskUserQuestion` liberally at real decision
  points; that is what this phase is for.
- Protect context aggressively. This is the thing that matters most about this
  mode:
  - Delegate any nontrivial exploration (multiple files, a whole directory,
    unfamiliar code) to a subagent and bring back only a distilled summary. Do
    not paste raw file contents or long tool output into the main conversation
    to reference it once.
  - Keep responses terse. This is a conversation about decisions, not a
    narrated research log.
  - Avoid speculative tool calls "just in case"; only look at what is actually
    needed to make the next decision.
- Write the running plan to the plan file as it develops, rather than
  reconstructing it from scratch at the end.

## Exit

When I say "exit planning", treat the plan as finalized: submit it for
approval if that has not happened yet, and once approved, implement everything
agreed on in one batched pass. Do not re-open decisions already settled during
planning, and do not trickle the implementation out one clarifying question at
a time, which defeats the point of planning first.
