# /EOD - End of Day wrap-up

Run this at the end of your day or when you want a clean slate for tomorrow.
It is the Cleanup Crew's watcher hygiene loop, stated as one runnable pass:
converge, sync, tidy, memory-feed, commit, push. Nothing here is new; the
steps come from `rules/claude/jobs.md` (Watcher hygiene loop) and
`commands/cleanup-crew.md`.

Focus of this run, if one was given:
$ARGUMENTS

## Steps

### 1. Converge first

Fetch and merge sibling branches before touching anything:

- `git fetch origin`
- Merge `origin/windows` into current branch (if on mac or phone)
- Merge `origin/mac` into current branch (if on windows or phone)
- Merge `origin/phone` into current branch (if on windows or mac)
- Resolve any conflicts, commit the merge

### 2. Inbox is truth

Every Pending line in `queue/inbox.md` must match its card's frontmatter.
A blocker whose dep landed flips the same pass. Done items compress into
Done with a card pointer (the card's Report is the record). Done older
than about two weeks gets deleted, git is the archive.

### 3. Refresh the workload issue

Refresh https://github.com/iasolb/ai-memory-bank/issues/1 to match the
current queue: same order, current work only, no ideas, no done history,
refresh date bumped. This is the permanent home, the number stops moving.

### 4. Priority guard

List order is work order. Anything the owner stated about priority was
already applied when they said it; absent a statement, new asks slot in
by the priority preamble the queue declares, never appended to the bottom.

### 5. Memory feed

- Durable facts and corrections land in the right memory file
- Expired transients get deleted on sight
- No new file without a rule-7 reason
- Corrections grep for every copy across the repo before the pass ends

### 6. Structural findings, if there are any

**There is NO fold. The per-persona `turns.md` files were deleted
2026-08-21** and nothing writes one, so there is nothing to drain.
`reports/personas/_LOG.md` survives and **any session appends to it
directly**; it is no longer one persona's file. Only a genuine structural
fault with more than one instance earns a place, and where a permanent home
exists you write a pointer, never a copy. A loose end that cannot be
honestly verified closed moves to "Unresolved, carried forward".

**NEVER TOUCH `reports/personas/fast-lane/INBOX.md`.** A live drop-box,
not a log.

### 7. No bloat, golden rules

- "If it can be a pointer it should be" (memory-integrity rule 3)
- "Everything the owner reads is decoded" (style.md)
- Fewest words that stay unambiguous
- Deduplicated facts go to their canonical file

### 8. Propagate to public surfaces

Any new rules, commands, hooks, or patterns established in the memory bank
during the day get folded into the public workbenches. The memory bank is
the source; the workbenches receive. Check:

- `rules/` changes that apply to both Claude and opencode (git-github,
  jobs, style, dev-practices) → update the matching rule in
  `claude-workbench/rules/` or `opencode-workbench/rules/`
- New commands → add to the appropriate workbench `commands/`
- Hook changes → update both `.sh` and `.ps1` copies in
  `claude-workbench/hooks/`
- Install script changes (new npm packages, new symlinked items) → update
  `install/` in both workbenches
- Format conventions (like TOON) → record in the workbench README or
  instructions

Commit each workbench separately with `-- <pathspec>`, never plain
`git commit`. Push each workbench's own branch.

### 9. Commit and push the memory bank

- **NEVER `git add -A`, and never a plain `git commit`.** Other sessions run
  on this working tree all day; both forms sweep up their staged and
  in-progress files. Add untracked files you created BY PATH, then
  `git -C <repo> commit -m "<one line>" -- <path> <path>`
  (`rules/claude/git-github.md`).
- **Check the branch before committing** and check what a push would publish
  with `git log origin/<branch>..<branch>`: a push has no pathspec, so it
  publishes every commit on that branch, including other sessions'.
- Push the machine branch to origin. Confirm with `git status --short`; a
  `??` line is the only warning you get that something was skipped.

### 10. Wrap-up

Report what you converged, pruned, or deduped, and anything you found but
could not resolve. **No turn log**: those files were deleted 2026-08-21.

End with exactly one thing for the owner to do, or "nothing needed from
you" if the pass was clean.
