# Git and GitHub (opencode)

## Keep repository changes reviewable

- Work from the repository and base branch named in the task. Use one branch
  per independent change unless the task explicitly defines a shared branch.
- Before editing, check the working tree and read the relevant configuration.
  Do not overwrite changes already present.
- Stage and commit only the paths the task owns. Avoid broad staging commands
  in a shared checkout.
- Open one pull request for one coherent change. Describe the user-visible
  result, the gate run, and any remaining limitation.
- Do not merge, release, rewrite history, change visibility, or delete remote
  work unless the task explicitly grants that action.

## Keep the public copy safe

Before publishing, inspect every changed file for personal names and handles,
email addresses, hostnames, IP literals, employer or school names, secrets,
and absolute home paths. Prefer configurable values with documented defaults.
Run the publishing repository's privacy gate over the complete public tree
before opening a pull request. `tools/portability-check.py` checks portability
assumptions in the public toolkit.

## Verify work and follow agent activity

- Run the task's gate and preserve its actual output and exit code. A clean
  diff is not evidence that tests passed.
- For an issue-backed job, `python tools/issue-job.py sync` reconciles issue
  state with the configured job store.
- `python tools/agents-in-flight.py` reports configured agent runs and pull
  requests, or reports that it could not check. Review the pull request diff
  and checks before any authorized merge.
- Use `python tools/run-gate.py --self-test` and
  `python tools/check-confined.py --self-test` to verify those tools' own
  behavior where they are part of the checkout.

## Multiple sessions

Assume another session may be active. Check status before and after each
change, keep commits path-scoped, and resolve conflicts by preserving both
independent changes. Never select a side merely to make a conflict disappear.
