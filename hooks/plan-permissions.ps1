# PostToolUse hook on ExitPlanMode: once a plan is approved, start building
# IMMEDIATELY. Rewritten 2026-08-03 (owner directive, superseding the
# 2026-07-27 batch-early version): pre-triggering permission prompts as a
# ceremony before the work was a 2-hour snag, never do it. Prompts that
# genuinely fire get answered when they fire; allowlist maintenance is an
# after-the-work follow-up, never a gate in front of it.
# Windows counterpart of plan-permissions.sh (invoked via powershell,
# which macOS/Linux lack, so each side self-selects by platform). Prints
# [plan-permissions] lines and always exits 0: a broken reminder must
# never block the build.

Write-Output "[plan-permissions] Plan approved. Start the build NOW. Do NOT pre-trigger permission prompts, run permission-audit skills, or edit settings.json before the work: that ceremony is banned (owner, 2026-08-03)."
Write-Output "[plan-permissions] If a prompt fires mid-build, answer it and keep going. Only AFTER the deliverable is done, if the same read-only pattern prompted repeatedly, fold it into permissions.allow (standing policy: no interpreters/shells, no git/gh writes outside the designated repos, narrowest pattern, Bash+PowerShell spellings, :* form)."

exit 0
