# Global instructions

This file is loaded into every Claude Code session on every machine you
install the workbench on. Replace each section below with your own content
and delete these instructions.

## Who I am

Who you are, what you work on, and the recurring projects Claude should
recognize without re-explanation. A few paragraphs pays for itself quickly.

## Tooling defaults

Languages, formatters, libraries, and patterns you always want. The stack
you reach for by default, and anything you never want suggested.

## How to work with me

Tone, verbosity, how much explanation you want with code, when Claude should
ask versus act.

## Rules

Standing rules live one-per-topic in `rules/`. Import each one here with a
line starting with an at-sign, for example: `@rules/shared/style.md` on its own
line (without the backticks). Nine rules ship in `rules/` already; read
`rules/README.md`, keep the ones you want, and add an import line for each.
An unimported rule is never loaded, so deleting the import is enough to
switch one off.
