# Cost

Choose the least expensive route that meets the task's quality and timing
requirements. Prefer built-in tools and standard-library scripts; use a free
service when it is sufficient, and a paid service when its advantage is clear
and relevant.

## Measure before sizing work

- Ask for the applicable usage ceiling and current position before planning
  work that consumes a metered service. Never assume a budget or infer it from
  past usage.
- Use the provider's current usage view as the authority. For supported
  opencode exports, `tools/run-cost.py` reads reported run usage; its
  `--self-test` option verifies the parser without requiring a live account.
- A missing value is unknown, not zero. State when a limit or cost could not be
  measured.
- Keep volatile balances and limits out of standing rules; record them with
  the work that used them.

## Route work deliberately

- Use a script for deterministic work, an inexpensive model for clear bounded
  edits, and a stronger model only when the task needs its additional judgment.
- A subagent moves work, but may not move it to a different billing pool.
  Confirm how the selected platform charges delegated work.
- Reuse verified context and existing tools. Do not spend model usage
  rediscovering an answer already available from a tool.
- Repeated mechanical work belongs in a script or workflow, not repeated
  model calls.

## Test cost-sensitive workflows

Before sending real work through a new pipeline, run its smallest harmless
end-to-end example. Verify the executor, gate, result recording, and handoff;
a passing unit test of one component does not prove the full route works.

For the public workbench's available checks, use `tools/health.py` to measure
framework indicators and `tools/agents-in-flight.py` to inspect configured
GitHub-agent activity. These tools report their own limitations; do not treat
an unavailable measurement as a healthy zero.
