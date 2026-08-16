# Memory integrity

Anti-drift rules for a memory repo, every session, every machine. These exist
because a memory that quietly disagrees with itself is worse than no memory:
you will act on the wrong half with full confidence.

1. **I outrank memory.** On conflict between something I just said and
   something a file says: say so, fix the file in the same turn (quote me,
   date it), then continue. If I repeat a correction, that means copies
   survived somewhere; grep the whole repo before replying.

2. **Verify capability claims.** Any "X can do Y" carries how and when it was
   verified, or it is UNVERIFIED and nothing builds on it. Same for chat
   answers about tooling: test it, check the product docs, or cite a line in
   an acceptance report; otherwise say UNVERIFIED out loud. The verified
   answer takes seconds; the plausible one costs hours.

   **This is the most-broken rule in any setup like this**, and the failure
   always has the same shape: a plausible mechanism gets written down in the
   voice of an observation, and the next session builds on it. Consequences
   that are not optional:

   - No claim about this framework's behaviour goes in a file or in chat
     without either a doc URL, a command whose output is quoted, or "observed
     on <date>". Nothing else counts as evidence, and "it is registered and
     deployed" is not evidence that it works.
   - **Never infer that something did not happen from the absence of a
     record. That is evidence about the recorder.** A log with no rows may
     mean a quiet night or a dead logger, and those look identical.
   - When I say a thing is broken, my observation is the finding. Do not open
     by defending the config; go find the mechanism.

3. **One fact, one place.** Load-bearing facts live in one file; everything
   else links to it. A correction greps for every copy and fixes them in one
   commit.

   **Stated as an obligation on the writer: if it can be a pointer it should
   be.**

   - **Name the file, do not restate its contents.** If a sentence you are
     about to write is recoverable from a file that already exists, write the
     path instead of the sentence. One pointer beats a paraphrase that can
     drift, and every drifted copy started as a helpful restatement.
   - **A pointer with nothing to point at becomes a copy.** So the test is not
     "did I write a pointer", it is **"does the thing I am pointing at exist
     yet?"** If it does not, create the permanent home first, then point at
     it. If the fact deserves no permanent home, it deserves no words either.
   - **Two spellings of one fact is a bug even when both are correct today.**
     Superseded text stays beneath its replacement for provenance, which is
     not a second copy: it is dated and explicitly dead. An undated parallel
     statement is.
   - **Some facts live OUTSIDE this repo, and pull-request state is the one
     that keeps biting.** The forge owns whether a pull request exists, is
     open, or is merged. No file here may assert it; point at the URL, or ask
     the API, which is one read-only call. A state you did not read is a state
     you are guessing.

4. **Teardowns sweep claims.** Retiring machinery includes fixing every claim
   that machinery backed, in the same session.

   **FINISHED WORK IS DELETED, NOT ANNOTATED.** This is the disposal half of
   rule 3 and it applies to every queue and every memory file, every pass.

   - **A completed arc leaves the queue entirely.** Its home is `memory/` and
     its cards. A priority list carrying an item whose headline reads COMPLETE
     is not a priority list.
   - **A card that is `done` and reported leaves `queue/jobs/` for `done/`.**
     The hooks glob the top level only, so an unarchived finished card is
     reported as live work forever.
   - **Annotating is the failure mode, not the fix.** "DONE", "COMPLETE",
     "retired, see below" all keep the bytes and add more. Delete the item
     and, if anything about it would change a future decision, move that one
     sentence to its permanent home first.
   - **The exception is narrow:** superseded text stays beneath its
     replacement when it is dated and explicitly dead, because that is
     provenance. Text that merely finished is not provenance.

5. **Corrections are commits.** If I state a fact, constraint, or preference,
   it is committed to memory that session, before the task continues.
   Chat-only corrections do not exist.

6. **Machinery ships with its test.** A named acceptance report is the system
   test gate, and a line goes green only when I observed it. Any card touching
   hooks, permissions, or the operating model adds or re-runs its acceptance
   line. A broken line flips to FAIL and the framework is degraded until it is
   re-passed.

7. **Write terse.** Chat and memory: fewest words that stay unambiguous.
   Memory files are bullets over narrative, a one-line description up top, and
   history only when it changes future behaviour. A new file needs a reason it
   cannot be a line in an existing one.

8. **Fix memory-repo issues immediately, do not just report them.** Scoped to
   this repo's own machinery: hooks, scheduled automation, gitignore coverage,
   and the permission list (but see `rules/claude/permission-loops.md`, which gives
   the permission list a single owner). Anything with an unambiguous fix gets
   applied in the same session, committed and pushed. Judgment calls get
   flagged as a note instead of forced through. This does not extend to code
   outside this repo, to remote writes, or to anything a safety rule covers.
