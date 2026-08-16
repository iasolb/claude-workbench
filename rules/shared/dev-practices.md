# Dev practices

This file is for the tooling decisions that are DECISIONS rather than
defaults: the thing you would otherwise re-suggest every month, and the thing
that looks like noise until you know it was deliberate. Two of mine, as the
shape to copy:

- **A banned tool, with its replacement named.** "No <GUI API client>, ever,
  on any project. To explore or test an API, use `curl`, `httpie`, or a quick
  script or REPL snippet. If a request collection needs to persist, put it in
  a checked-in `.http` file, a shell script, or a markdown catalog of `curl`
  commands." A ban with no alternative just gets worked around.

- **Strict linting and type-checking are the default assumption**, so treat
  deliberate-looking strict-typing patterns as deliberate, not noise:
  `_ = call()` to mark a discarded return value, explicit `-> None` returns,
  `cast()`, `Any` only where a dynamic boundary genuinely requires it. Do not
  suggest loosening or dropping these as cleanup. This one is worth keeping
  verbatim in any repo that types its Python: without it, every review
  proposes removing the annotations that took effort to get right.
