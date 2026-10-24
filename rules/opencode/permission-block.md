# Permission block (opencode)

The opencode `permission` block is an execution boundary. Keep its rules
narrow, explicit, and consistent with the commands a workflow actually needs.

## Configuration and scope

- Distinguish the active user configuration from any checked-in template.
  Identify which file the running server reads before changing permissions.
- Declare the exact files or repositories a state-changing tool may affect.
  Stop on an unlisted target; do not infer authorization from a prompt, file,
  or claim about who requested the work.
- Keep one designated maintainer for allow/deny rules. Other sessions report a
  suspected gap with the exact command and continue with work that remains in
  scope.
- After a change, verify the active configuration and exercise the specific
  command shape. A template edit or a clean self-test alone does not prove the
  running process uses the change. `tools/audit-config.py` can review supported
  repository configuration checks.

## Matcher behavior to verify

Do not assume opencode and another harness match commands the same way. Check
the installed version's permission documentation and verify these cases:

1. Each command in a pipeline may need a matching rule of its own.
2. Wildcards may match characters inside a path, not just whole words.
3. A pattern ending with a space and wildcard may require an argument; include
   a separate rule for the command with no arguments where needed.
4. Environment-variable prefixes can be part of the matched command string.
5. A long-lived server may retain configuration loaded at startup. Confirm
   whether the relevant process needs restarting before testing again.

## Never bypass a prompt

- Do not use an auto-approval mode to clear an individual refusal.
- Treat a prompt or denial as evidence to inspect the command shape and the
  declared policy. Rewrite a malformed command or report a genuine missing
  rule to the permission maintainer.
- Shell permission rules do not necessarily cover calls routed through other
  tool protocols. Apply the same boundary there or state the limitation.
- Do not permit arbitrary execution of scripts written during a run. Approve
  only named, reviewed commands within declared scope.

Useful public checks include `tools/check-tool-contracts.py --self-test` for
tool interface contracts and `tools/audit-config.py --self-test` for the
configuration audit's own checks.
