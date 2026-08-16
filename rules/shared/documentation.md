# Documentation

Default: one-line docstrings, code comments only for a non-obvious WHY.
Long-form rationale, design notes, or architecture explanation belong in a
markdown file in the repo, not in code comments or docstrings.

Exception: code meant for other people to use, or anything where a usage demo
genuinely belongs next to the code (for example interactive discovery
helpers), warrants fuller docstrings with example usage.

Ask which mode applies during a project's planning phase rather than assuming
partway through, and do not retroactively downgrade fuller docstrings that
were an intentional choice.
