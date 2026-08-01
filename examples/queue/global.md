# Global queue

Standing priorities, hand-ordered by you: a fresh session starts on the
topmost unblocked item without re-litigating the order. Reordering is yours;
Claude adds, annotates, and removes items as work completes. In-flight state
for the item being worked lives in queue/current.md, not here.

Format contract: the first line of each numbered item is a self-contained
headline (title + status), details follow indented. The queue-print
SessionStart hook prints only the headlines from this file (plus all of
current.md), so keep headlines meaningful on their own.

1. **Example item: replace me** (queued today, not started)
   Enough detail that a fresh session can start without asking. Include
   where the work lives, what "done" looks like, and any constraints.
