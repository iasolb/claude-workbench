Scaffold the project below: STRUCTURE ONLY. Lightweight command, not a
persona: no turn report, no wrap-up ritual.
Target, and what needs shaping: $ARGUMENTS

The rule this command exists to enforce: build only stub files and the
necessary directory pattern. The focus is project structure. You may build
only necessary components; anything else stays as naked functions or as
classes with attributes and no implementations.

## The point

A structure you can read is worth more than a component that works, because
the structure is what everything later gets built against. Implementation
written before the shape is settled is the thing that has to be thrown away,
and in the meantime it hides the shape.

## What "necessary" means. It is DECLARED here, never judged in the moment.

That distinction is the whole rule (`docs/scoped-by-declaration.md`): a
session deciding at runtime what counts as necessary will build everything and
call it necessary.

**BUILD FOR REAL, these and nothing else:**

1. **The directory pattern and file placement.** This IS the deliverable.
2. **Declarations**: schema DDL, config and manifest files, type definitions,
   dataclasses, enums, function and method SIGNATURES with type hints, route
   paths, interface contracts. A declaration is not an implementation, it is
   the structure written down.
3. **A validator that proves a declaration is self-consistent**, where one is
   cheap. A list nobody checks rots.
4. **The README that explains the layout**, because a structure nobody can
   read is not a structure.
5. **Anything ALREADY RUN against real inputs.** See the line below; this is
   the one case where working code survives a scaffold pass.

**EVERYTHING ELSE IS A STUB:** business logic, I/O, queries, network calls,
transformations, rendering, auth, retries, error recovery.

### The test that decides it, and it is not a vibe

**Has this component been RUN, against real inputs, and produced a result
somebody acted on?**

- **Yes:** it is not speculative. Keep it. Deleting it destroys evidence.
- **No, because the things it depends on do not exist yet:** stub it. It is a
  guess wearing the costume of working code, and it cannot be tested, so
  nobody will notice when it is wrong.

## Stub shape

- **Python**: signature, full type hints, one-line docstring saying what it
  will do, body `raise NotImplementedError`. `pass` is the other option and it
  is worse by default, because it returns `None` silently and a caller cannot
  tell the thing was never built. Say which you used.
- **Classes**: attributes and their types, yes. Methods stubbed as above. No
  `__init__` logic beyond assigning declared attributes.
- **SQL**: full DDL. Tables, columns, types, keys and constraints are the
  structure, so they are built for real, not stubbed. No seed data, ever.
- **Config and manifests**: real and complete. They are declarations.
- **Every stub file opens with a comment saying what will fill it**, so the
  next session does not have to guess whether it is unfinished or abandoned.

## Do not

- **Do not write tests against stubs.** They assert nothing and they will go
  green, which is worse than no tests.
- **Do not create a directory that holds nothing**, "for later". An empty
  directory is a guess about a shape you have not settled. Necessary pattern
  means the pattern the project actually has.
- **Do not leave a stub that reads as finished.** A half-implementation is the
  failure this command exists to prevent.
- **Do not delete working code to satisfy the rule.** Apply the run-it test
  above; if a component passes it and you still think it should go, say so and
  let the owner decide.

## Scope, enumerated (`docs/scoped-by-declaration.md`)

Writes ONLY inside the project directory named in $ARGUMENTS. Creates no file
outside it, touches no other repo, and commits nothing outside the repos this
setup owns (`rules/git-github.md`). If the target is unclear, ask once in a
form rather than guessing at a path.
