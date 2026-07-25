# Git / GitHub

GitHub-write commands (`gh pr create`, `gh pr merge`, `gh issue create`/
`comment`, `gh release create`, any other remote-mutating `gh` call) are
always mine to run, never yours, everywhere, no exceptions. This is enforced
at the permission layer in `settings.json`, not just prose.

**The workbench repo is the one exception, for both `git commit` and `git
push`:** in my private workbench copy, you run the full sync loop yourself:
merge the other machine's branch, resolve conflicts, commit (drafting
messages from the diff, you have better scope on what actually changed), and
push. Scoped allow rules in `settings.json` (`git -C <path-to-workbench>
...`) make this prompt-free there and nowhere else.

Everywhere else, `git commit` and `git push` are mine to run. Read-only git
(`git status`, `git diff`, `git log`, `gh pr view`) is always fine
unprompted. If a task needs a push to be "done", stop short of it: leave
things committed and tell me the command to run.
