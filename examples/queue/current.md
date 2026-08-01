# Current arc

In-flight state for the queue item being worked right now. Keep it updated
as the arc progresses so a fresh session resumes mid-arc without re-deriving
state. On arc completion: clear this back to the "(none)" stub, remove the
finished item from queue/global.md, and record durable outcomes in memory/.

## Arc

(none)
