# 13. The next turn goes to the least recently attempted message

Date: 2026-11-11

Status: accepted

## Context

ADR 0007 has a refusal waited out, and says what happens to the messages
waiting on it: "If a retry is refused, the source's other failed messages that
were waiting out a refusal wait for the same end without being asked for."
Every one of them is given the same end, so when the wait expires they all
become due at the same moment and the fetch retries them in whatever order it
holds them in. That order was oldest-first by first failure.

It only takes one of those retries to reach the platform. If it is refused, the
refusal is recorded for the whole platform and everything behind it is refused
before a request is made (ADR 0009) — and the oldest-first order makes it the
same message every time. The one that reaches the platform earns the next
cool-down, the rest wait for it, and the front of the queue never moves.

The log measures it. Between 2026-11-04 12:25 and 2026-11-05 08:37, nine poll
ticks produced 15 refusals across two videos and ingested nothing. On
2026-11-11 the single video attempted was still the one that first failed on
2026-11-04. Twenty-five messages were waiting; one of them had every turn for a
week.

There was a second reason the front could not move, which is the part worth
recording. A message that was never asked for was nonetheless stamped as having
just been attempted, because the same bookkeeping recorded both the message the
retry asked about and the ones the pre-check held back. So any ordering by "when
was this last tried" would have moved every waiting message back together on
every tick, and changed nothing.

## Decision

While a platform is refusing us, the one request a cool-down allows goes to the
message that has gone longest without being attempted.

- **The queue is ordered by when a message was last attempted**, least recently
  first, so the turn rotates instead of belonging to the oldest failure.
- **A message never attempted goes before any that has been.** A refusal counts
  no attempt (ADR 0007), so a message nothing is yet held against is tried
  before one whose own attempts have failed.
- **A message that was not asked for was not attempted.** When the pre-check
  holds a message back because the platform is refusing us, it waits for the
  refusal's end as ADR 0007 says, and its last attempt is left as it was.
  Nothing of it reached the platform, so nothing about it was learned, and the
  turn it is waiting for must still be coming to it.
- **The order is total and stable.** Two messages cannot swap places from one
  tick to the next unless one of them was attempted; the same recorded state
  always produces the same queue. A rotation that depended on anything else
  would be a different message each tick for reasons nobody could read.
- **Nothing new is recorded for this.** When a message was last attempted is
  already kept, for a person to read; this is a second use of it.

## Consequences

- A run of refusals costs every waiting message a delay, instead of costing the
  backlog everything. Twenty-five waiting messages are tried one per cool-down
  in turn, so each is tried about once every twenty-five cool-downs rather than
  one of them being tried always and the rest never.
- Nothing is ingested sooner for this on its own. Under a sustained refusal
  every attempt is refused whichever message takes the turn; what changes is
  that when the refusals end, the backlog is not behind one message that has
  been failing since the start. Combined with ADR 0010's growing cool-down the
  rotation is slow — one message a day at the ceiling — and that is deliberate:
  the backlog is drained by the refusals ending, not by probing them.
- The oldest failure is no longer tried first, which is a loss. If a backlog
  has one message that matters more than the others it is usually the oldest,
  and now it waits its turn with the rest. In ordinary conditions, with no
  refusal in force, every due message is retried on the same tick and the order
  decides nothing — the trade only applies while a platform is refusing us,
  which is when oldest-first was starving everything.
- A message whose retry is refused goes to the back of the queue, having
  learned nothing about itself. That is correct — it was tried — but it means a
  message can be sent to the back by a failure that was not its own.
- Ordering in the fetch rather than in the query that finds due messages is one
  more thing that is true only because the retry does it; a second caller
  reading due messages for itself would not get the rotation and would not be
  told so.
- This adds to ADR 0007's rule for messages waiting out a refusal: they still
  wait for the same end without being asked for, and now that waiting leaves
  no mark on when they were last attempted.

## Alternatives considered

- **Keep oldest-first and let more than one message through per cool-down.**
  The backlog would drain in one tick when the refusals end. But the second
  request of the tick is made to a platform that has just refused the first,
  which is the flood ADR 0007 exists to prevent, and under a block it re-earns
  the cool-down for everything.
- **Shuffle the queue.** A random turn also rotates, needs nothing recorded,
  and cannot starve a message for ever. But the same state would produce a
  different order every tick, so nobody could predict or reproduce which
  message gets tried — and a test could only assert that it varies.
- **Record a separate "last offered a turn" mark for the rotation.** It would
  separate "we tried this" from "this got the turn" cleanly. It needs a
  migration, and the existing mark already answers the question once the
  messages that were never asked for stop being stamped — which was a bug, not
  a shortage of state.
- **Order by how many attempts a message has, fewest first.** It prefers the
  least-tried message, which is close to the intent. But a refusal counts no
  attempt, so under a long block every waiting message sits at the same count
  and the order falls back to whatever breaks the tie — the oldest failure, and
  the starvation again.
