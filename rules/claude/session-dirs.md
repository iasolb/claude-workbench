# Session workspace dirs

A fixed workspace (for example `~/claude/session/`) holds three folders, each
with a distinct job:

- `session/context/`: my drop zone, files I put there for you to look at.
  Check it at the start of a session and whenever I say something is "in
  context".
- `session/working/`: your scratch/working space, and the landing zone for
  anything synced in from another machine.
- `session/outputs/`: deliverables I asked for (reports, exports, generated
  artifacts) land here, not scattered around the filesystem.

Contents are mine to clean up, do not prune these folders unilaterally.

If I redirect a specific deliverable somewhere else ("straight to downloads"),
that is one instance, not a new default. Record it as a dated instance rather
than rewriting this rule; if I redirect a second time, promote it to the
default and delete the note. Generalizing from a single aside is the same
failure as writing a plausible mechanism down as an observation
(`rules/shared/memory-integrity.md` rule 2).
