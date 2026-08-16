# Permission loops

Standing rules for the thing that actually costs time in an agentic setup: not
what Claude is allowed to do, but how many times a day I have to say so.

Keep the CASE HISTORY (every prompt ever screenshotted, with what caused it)
in a separate file that only the `/fast-lane` persona opens. It grows without
bound, and auto-loading it into every session spends thousands of tokens per
session teaching four other personas a job that is not theirs.

## Who owns permission work

| persona | on noticing a permission hole |
|---|---|
| driver, intake, cleanup-crew | write one line in the drop-box, keep working. Never edit the permission list. Never spawn a Fast Lane. |
| quarterback | write the line. MAY self-invoke a Fast Lane, and must warn it that a parallel one is probably live. |
| fast-lane | the ONLY writer of the allow/deny list. Clears the drop-box every pass. |

**The drop-box is `reports/personas/fast-lane/INBOX.md`.** One line, then carry
on with your card. It exists because a hole noticed by a driver otherwise dies
in that driver's own log, where the lane that fixes holes never looks.

**Report a hole even if you are not sure it is one.** A false entry costs Fast
Lane ten seconds; a missed one costs a click forever, on every future session.
That asymmetry is the whole reason the parallel lane exists.

This NARROWS `rules/memory-integrity.md` rule 8 for the permission list
specifically: that rule still says fix-do-not-report, but the allow/deny list
has one writer. Guard hooks, scheduled tasks and gitignore are unchanged and
still get fixed on sight by whoever finds them.

## What the matcher actually does

Read this before theorising about why an entry did not match. Sources are the
permission docs; anything not doc-verified is marked.

- **`Bash(git push *)` and `Bash(git push:*)` are THE SAME RULE.** The `:*`
  suffix is an equivalent way to write a trailing wildcard. The dialog itself
  writes the space-separated form when you choose "Yes, do not ask again".
- **A `*` may sit anywhere**, not only at the end. `:*` is the exception; it
  is only recognized at the end of a pattern.
- **MEASURED LIMIT: a `*` INSIDE a token does not match.** Every doc example
  is a whole space-delimited WORD. A family wildcard over a directory of
  scripts (`.../tools/*-matrix.sh:*`) does NOT cover them, and the literal
  path does. **Write one entry per script.** A card that ships a new script
  needs its own entry.
- **A trailing `:*` really does cover ANY arguments**, including a subcommand
  nobody anticipated. The thing to check is only whether the FIRST WORD
  matches, character for character.
- **The trailing space is a WORD BOUNDARY.** `Bash(ls *)` matches `ls -la` and
  not `lsof`; `Bash(ls*)` matches both. Prefer the spaced form.
- **The matcher knows shell operators**, which is the whole premise of the
  guard hook: a rule like `Bash(safe-cmd *)` does not permit
  `safe-cmd && other-cmd`, because each subcommand must match independently.
  Separators: `&&`, `||`, `;`, `|`, `|&`, `&`, newlines.
- **Some forms can never be prefix-approved at all:** `watch`, `setsid`,
  `ionice`, `flock`, and `find` with `-exec` or `-delete`. Only an exact-match
  rule for the whole string works. Plan around them.

## A permission edit reaches a session that is already running

So Fast Lane can test its own fix in the pass that makes it, and must.
Verified by running the same command three times with the list edited between
runs, and asking the owner in a form whether a dialog appeared: it stopped
appearing without a restart.

- **Verify in-session, every time.** Add the entry, run the exact command
  again, and ask whether a box appeared. A session cannot see its own dialogs,
  so the owner's answer is the only instrument.
- **A failed run after an edit is real evidence about the ENTRY**, not about
  reload timing. That is what settled the wildcard limit above.

## Diagnosing a prompt, in order

Six failure modes. Only 2, 3 and 4 are fixable in `settings.json`.

0. **Wrong TOOL.** An entry exists for one shell tool and not the other. Check
   both spellings first.
1. **The command did not parse.** A chain, a newline, a pipeline, `&`, a shell
   wrapper, an escaped `\$`, escaped whitespace, or an enormous string. No
   entry can EVER fix one. Rewrite the command.
2. **No entry exists.** A genuine gap; add the narrowest possible entry.
3. **The entry is spelled differently than the command.** On Windows three
   path spellings exist and all three need covering as a family: POSIX
   `/c/...`, backslash `C:\...`, and drive-with-forward-slashes `C:/...`.
   Fixing only the one in the screenshot is how such a bug survives three
   sessions.

   **THAT IS TRUE OF COMMAND ENTRIES ONLY. `Read(...)` and `Edit(...)` path
   rules have exactly ONE correct spelling.** A command entry prefix-matches
   the literal string; a path rule does not, because the product NORMALIZES
   the path first and then matches. On Windows, `C:\Users\alice` becomes
   `/c/Users/alice`, so a backslash or drive-letter spelling in a path rule
   matches nothing, ever. And a SINGLE leading slash anchors at the settings
   source rather than the filesystem root, so the correct form has two:
   `//c/Users/...`. Applying the command-entry habit to path rules is how a
   settings file fills up with rules that match nothing while looking
   thorough.
4. **The entry names a path that does not exist.** Check before blaming the
   matcher.
5. **It is a READ scope problem, not a command at all.** The dialog says so
   itself: "Path is outside allowed working directories". A path rule is
   missing, nothing else. **No command entry can ever fix this**, and a free
   built-in command like `ls` least of all, since it cannot be configured.

**Read the dialog's own subtitle first.** It names the cause in plain words
more often than not. Button count does NOT identify the failure mode.

## Command forms: what a session must never do

`docs/command-forms.md` is the allowed-command table and it governs. The
standing bans, each of which cost a real click:

- **No backslash-escaped whitespace**, ever. The binary path is what drags it
  in, so invoke `git`/`tail` plainly and never by an absolute path to the
  `.exe`.
- **No `\$`** in any argument, including inside a quoted commit message.
- **No call operator** (`&` in PowerShell). Call the absolute path directly.
- **No chains or pipelines**: one command per tool call.
- **No shell wrappers**: `powershell -Command`, `pwsh`, `cmd /c`. The matcher
  sees the first word and no entry can apply, however safe the inside is.
- **No bare interpreters**: `python`, `pytest`, `pip`, `npm`. Absolute
  interpreter, `-m` form, `npm --prefix <abs>`.
- **One-line commit subjects.** A multi-line `-m` puts newlines in the command
  string and prompts every time.
- **No output redirect into a file.** It prompts even when the bare command is
  allowlisted, and an Always-allow does not cover the next filename. Both
  shell tools already return stdout and stderr to the session.
- `cd` and `ls` on their own are FREE (built-in read-only set, not
  configurable). The COMPOUND was always the defect. **But a FREE COMMAND does
  not mean a FREE PATH:** a bare `ls` on a directory outside the allowed
  working directories still prompts, and that is mode 5.

**A question about a file is never a shell call.** Glob for where, Grep for
what is inside, Read for the whole file. None of them can ever prompt.

**But do not dodge a prompt into a worse diagnosis.** When the thing under
test is a script, RUN THE SCRIPT and add the narrow entry if it needs one. Do
not reconstruct a program out of read-only probes to avoid one click. The test
is "which tool actually answers the question", not "which tool avoids a
prompt".

## "Always allow" does not fix a loop. Never ask me to click it.

Those clicks land in a gitignored local settings file, keyed to the EXACT
command string, so a one-off (a commit message, a scratch path with a session
UUID in it) can never rematch and never reaches the other machine. That file
is also the only honest record of what actually prompted; read it when
diagnosing a loop.

The fix is always a narrow `:*` entry in the shared, git-tracked settings.

## Hard limits, and they are absolute

Nothing here ever answers, suppresses, pre-approves, or routes around a
permission prompt. That is the pattern that gets a whole fleet of remote
sessions flagged. This machinery logs, reports, and adds narrow entries after
the fact, at my call.

Some tools prompt "regardless of permission mode" in the platform's own words.
No entry retires those. It is a card-SIZING fact: such a card can never run
unattended.
