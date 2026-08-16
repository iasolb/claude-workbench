# tools/

Small, standard-library-only helpers. All three are read-only: none of them
writes to your repo, and none of them can approve or suppress a permission
prompt.

## parse-check.ps1

Syntax-checks one or more `.ps1` files by building an AST, without executing
them. Use it after editing a hook.

```
<repo>\tools\parse-check.ps1 hooks\notify.ps1
```

A hook is invoked by the harness, never by a session, so a syntax error in one
is SILENT: the hook dies, SessionStart prints nothing, and the next session
reads the quiet as "nothing to report". That is the failure this exists to
prevent. The `.sh` half of a hook pair needs no script: `bash -n <path>`.

## guard-matrix.sh

The case matrix for `hooks/shell-form-guard.{sh,ps1}`: roughly a hundred
commands with the expected verdict for each, run through BOTH mirrors, which
must agree with each other and with the expectation. Invoke it bare by its
absolute path (never `bash <path>`, which is one of the forms the guard
itself denies):

```
/c/Users/you/claude-workbench/tools/guard-matrix.sh
```

It prints one line per case and ends with `failures=N`. It fails loudly and
first if the guard does not parse, because an unparseable guard exits with
empty output, which the harness reads as "allow": a broken guard would
otherwise show up as a wall of confusing failures instead of one true one.

Paths inside the cases are placeholders. Every rule keys on the FORM of a
command, never on a particular directory, so swapping in your own paths does
not change what is asserted.

## test-nudge-matrix.sh

The case matrix for `hooks/test-nudge.{sh,ps1}`. Builds throwaway git repos
under a temp dir and hands them to the hooks via `TEST_NUDGE_REPO`, so it runs
green even though `test-nudge` ships with no repo declared. Same invocation
style, same `failures=N` ending.

## Machinery ships with its test

Both matrices exist because a hook that nobody runs is a hook nobody knows is
working. If you add a rule to the guard, add its cases in the same commit,
including at least one case that must still be ALLOWED: every guard rule here
that was ever loosened was loosened because it produced a false block.
