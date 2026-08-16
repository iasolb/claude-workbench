# Style

Never use em dashes. In files, code, comments, commit messages, or any other
written artifact, an em dash is unacceptable: use a comma, period,
parentheses, or colon instead. In live chat replies they are a lesser sin but
still avoid them by default.

## GOLDEN RULE: everything I read is DECODED

If you are presenting information to me it should always be decoded. Do not
refer to your internal bookkeeping unless it is genuinely necessary.

**What "internal bookkeeping" means:** card order numbers, card slugs and
filenames, phase letters, issue numbers, frontmatter values, persona names in
place of what a persona did, repo-relative paths, commit SHAs, finding
numbers, acceptance-line ids. All of it is yours. I read on a phone, days
after the sequence was written, with none of it in my head.

**Every surface, not just forms:** chat replies, queue lines, the workload
issue, completion emails, card Reports you point me at, and the text hooks
print at session start.

- **Describe the WORK in plain words, first.** "The back button that knows
  where you came from", never "order 46". "The popup pickers on the sell
  screen", never "the units rework".
- **An internal id may FOLLOW the plain description in parentheses, never
  replace it**, and only when I might need to name the thing back. Never as
  the subject of a sentence.
- **"Unless it is necessary" is a real exception and it is narrow.** A PR URL,
  a link I must tap, a command I must run, a filename I asked for: those ARE
  the information. Give them exactly. The ban is on ids standing in for
  meaning.
- **A stale encoded label is worse than an encoded one**, because I cannot
  even tell it is wrong.

Pointing at a file instead of restating it is the same discipline from the
other side: `rules/memory-integrity.md` rule 3.

## Ask me in a form, always

Any question for me goes through `AskUserQuestion`, not prose in a reply and
not a checkbox I have to interpret. Every environment, every kind of question:
clarifications, decisions, gates, "did this pass", priority calls. One
question still gets a form.

- **Never encode the question.** A form I have to decode is a form I dismiss,
  and a dismissed form costs the whole round. The plain-words requirement
  above applies to every OPTION too, not just the question.
- Write real options with a description that says what happens if I pick it. I
  am answering on a phone; a tap must be enough.
- Ask the whole batch in ONE form, never one question per turn. The tool caps
  at four questions with two to four options each, so a bigger round gets
  CHUNKED into successive forms. Group a long mechanical tail into one
  question that openly stands for a family of lines, never silently.
- Do NOT bury a question in chat prose and wait. If it is worth asking, it is
  worth a form; if it is not worth a form, decide it yourself and say what you
  assumed.
- A checkbox answers "done", never "which one" or "why". A question posed as a
  checkbox is a bug.

## Always link directly

Any file, report, card, or issue referenced in chat carries its direct URL,
tappable in a phone app. A bare repo path is useless to me during the workday.
Anything I must act on gets the tappable surface itself.

**Pull requests especially.** Every PR you cause to exist, or ask me to act
on, carries its full URL in chat, in the card Report, and in the completion
email. `git push -u` prints one on stderr, so there is never an excuse.

## Drive cheaper

A standing performance instruction for sessions I am driving.

- **Act on what is already established.** Do not re-read a file this session
  wrote, re-derive a fact already in context, or re-verify something stated.
- **Subagents are for genuine fan-out only.** One survey that answers a
  question fully beats three that overlap. A subagent that reads 130KB to
  produce a list you then do not use is pure waste.
- **Measure once.** Quoting two contradictory numbers for one thing costs a
  second measurement AND the paragraph explaining the discrepancy.
- **Shorter replies.** I read on a phone. The finding and the ask, not the
  narration of how it was found.
- **Prefer the cheap instrument:** Grep/Read/Glob over shell probes, one
  `status --short` over a re-read, a hook's own output over recomputing it.

## End-of-turn framing

Every handoff message ends with exactly what I must do to keep the planned
flow moving, stated as an action ("fire a session and say work the top card",
"merge this PR", "push commit X"), with links. No status prose after the ask.
If nothing is needed from me, say "nothing needed from you."

**Answering a stop hook is the SAME turn. Do not restate the ask.** A hook
fires after the ask has already been given, so re-applying this rule to the
continuation duplicates it, Run button and all. Answer the hook in its one
line and stop.

**A bare `claude` in a command block is not a handoff.** The Run button only
opens a session; the instruction I actually have to give it is still text I
must type. Put the words in the sentence, and do not dress the launch up as a
runnable command.
