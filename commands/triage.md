# /triage

Review the approved work backlog and make its status, priority, and next action
clear. Read the current issue state rather than relying on an earlier summary.

## Pass

1. Refresh issue-backed job state with `python tools/issue-job.py sync` when a
   job store is configured. Inspect current GitHub issues and pull requests for
   work not represented there.
2. Preserve priorities explicitly set by the owner. For unranked work,
   prioritize measured deadlines or limits, work that unblocks other work,
   correctness and maintenance, then individual features.
3. Classify each item as actionable, waiting on a decision or dependency,
   intentionally parked, or complete. Record the exact blocker for anything
   that cannot proceed.
4. Remove completed narration from the active backlog while retaining a stable
   pointer to the issue, pull request, or report that is its record.
5. Rewrite vague titles as short statements of current status and the next
   useful action. Do not turn speculation into a status claim.
6. Check for duplicate work and conflicting edits. Group work that shares the
   same files; run independent groups in parallel only when neither consumes
   the other's output and neither gate reads the other's changes.
7. If an approved item is actionable and not yet represented by a job, prepare
   its exact scope and gate, then use the configured repository and store with:

       python tools/issue-job.py open --repo <owner>/<repository> --slug <slug> --name "<title>" --tier nano --body-file <spec-file> --gate "<gate-command>"

## Partial pass

After a change to the backlog, fold in only facts learned during that work:
cleared blockers, falsified assumptions, new constraints, or changed status.
Then reread the affected issues and regenerate any derived summary. Do not
re-rank untouched goals during a partial pass.

Never invent work, silently change an explicit priority, or call work complete
without checking the owning issue or pull request.
