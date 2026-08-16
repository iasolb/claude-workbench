Debug the problem below. This is a lightweight command, not a persona: no
turn report, no wrap-up ritual, just find the cause and fix it (or explain
the fix if you are not the one applying it). Problem, if one was given:
$ARGUMENTS

- Scope is wherever this session already has access: the repo(s) granted to
  it, nothing wider. If the bug needs a stack this environment does not
  have (dev DB, staging, ssh, a real browser), say so and stop rather than
  guessing at the fix blind.
- Reproduce before you theorize: read the actual error, the actual failing
  test, the actual log line. A plausible-sounding cause with no reproduction
  is a guess (memory-integrity rule 2 in miniature).
- Fix the root cause, not the symptom. If the real fix is out of scope
  (needs a decision that is not yours, needs a stack you do not have), say
  exactly what you found and what unblocks it; do not paper over it.
- A question about file contents or location is a Grep/Glob/Read call, never
  a shell one-liner (rules/claude/permission-loops.md). Every shell call you do
  need still comes from `docs/command-forms.md` in a repo that has one.
