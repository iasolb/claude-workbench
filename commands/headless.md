---
description: Prepare bounded issue work to proceed while the owner is unavailable
---

# /headless

Use this when work is expected to continue without live supervision. Remote
execution is safe only when the issue is self-contained and its checks can
decide whether the change succeeded.

## Before handing work off

- Confirm the repository, exact file scope, acceptance behavior, and a runnable
  gate. Put all required context in the issue; do not depend on another session
  to supply missing instructions.
- Make the issue small enough for one coherent change. Do not combine unrelated
  work or make one job depend on another job's unpublished output.
- State what the executor may change and what it must leave alone. An unlisted
  target is out of scope.
- Check that required credentials, dependencies, and CI checks are available.
  A missing credential or decision is a blocker, not a reason to guess.
- Create issue-backed work only after the configured store and target
  repository are known. The tool records the job before opening and assigning
  its issue:

      python tools/issue-job.py open --repo <owner>/<repository> --slug <slug> --name "<title>" --tier nano --body-file <spec-file> --gate "<gate-command>"
- Check configured activity with `python tools/agents-in-flight.py`. A failed
  query means the activity is unknown.

## While away

Let the issue's CI and pull request carry the result. Do not ask an unattended
executor to merge, release, alter permissions, rewrite history, or touch
production unless those actions were explicitly authorized. Review the diff,
checks, and reported evidence when available.

When returning, use `/reenter` and refresh issue state with
`python tools/issue-job.py sync`.
