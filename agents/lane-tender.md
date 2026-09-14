---
name: lane-tender
description: Use to run the operational land/gate/close loop over every driver lane. Absorbed into the quarterback 2026-08-30.
---

Pointer, 2026-08-30: lane-tender was absorbed into the quarterback. The
survey, land-or-report, close-orphans loop now lives in
`commands/quarterback.md` (Pass 4, lane verification), on top of the
mechanics in `tools/drive-lane.py` and `tools/land-lane.py`. There is no
separate lane-tender agent any more.
