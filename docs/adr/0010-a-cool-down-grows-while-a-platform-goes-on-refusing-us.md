# 10. A cool-down grows while a platform goes on refusing us

Date: 2026-11-11

Status: accepted

## Context

ADR 0007 has a platform refusing us waited out, and names one wait: "about an
hour, configurable". ADR 0009 moved that wait into a row every fetch reads, and
has the later of two ends hold. Neither says anything about the refusal after
the wait ends, because both were written against a flood: 58,000 caption
requests in three days, which is very likely what kept YouTube refusing. An
hour of silence was the fix for the flood, and the flood is fixed.

What the hour does not fix is a block that outlasts it. Between 2026-11-04 and
2026-11-11 the log records 17 refusals on the channel source, each one setting
the same 60 minutes again. The first request after each wait was refused, which
re-earned the wait, and nothing in the recorded state could tell the first
refusal of a block from the seventeenth. A block of a week was served as an
hour, re-armed 167 times over, and the only thing that said how long it had
really been going on was a person reading the log.

It is worth being plain about what that costs and what it does not. At roughly
one caption request an hour we are not being refused for volume, so waiting
longer does not shorten the block we are in — and nothing here claims it will.
What the flat hour costs is legibility and a little traffic: every hour, one
request to a platform that has been saying no for days, and a recorded wait
that says nothing about the block's age. Two tickets after this one want to
show a person what is stuck and for how long (#141, #142), and "an hour, as
always" is not an answer either of them can give.

A refusal also has no opposite today. Nothing is recorded when the platform
does answer us, so there is no moment at which a block is over — only a wait
that expires.

## Decision

A cool-down is not a constant. It is the base wait doubled once for each
refusal in a row before it, up to a ceiling, and a request the platform answers
returns it to the base.

- **A refusal met while we were only just let back in is the same block going
  on.** It waits longer than the last one did. "Only just" is the last wait's
  own length: the longer a platform has been refusing us, the longer a spell
  with no refusal has to be before the block is read as over. Nothing is known
  about a spell in which nothing was asked — a paused source, a stopped
  process — so it is not counted as the platform still refusing.
- **A request the platform answers ends the block.** The next refusal starts
  again at the base. An answer is the platform letting us through, whatever it
  answered: a video with no captions is as much an answer as a video with them.
- **The wait has a ceiling**, configurable with the base, so no block can push
  the next attempt past a day and leave a source looking abandoned.
- **How many refusals in a row there have been is recorded beside until when**,
  one row per platform as ADR 0009 has it, because nothing else remembers it
  across the restart ADR 0009 was written for.
- **Both the wait and its age are said in words.** The log line for a refusal
  says which refusal in a row it is and until when, to the day and not only the
  hour; so does the reason recorded on every message it holds up, which is what
  the source page shows.
- Of two refusals recorded close together, the later end still holds, and so
  does the higher count: a wait recorded by this never shortens.

## Consequences

- A block lasting days now costs a wait of hours, then of a day, instead of the
  same hour 17 times. That is the point, and it is not a speed-up: nothing is
  ingested sooner than it would have been. What was gained is that the recorded
  wait says how long the platform has been refusing us.
- The rotation of the retry queue turns more slowly. Each cool-down still
  spends its one request on a different message, but under a long block that is
  one message a day rather than one an hour, so a backlog of 25 videos is tried
  over 25 days. Trying them faster means trying them against a platform that is
  refusing all of them, which is what the flat hour did; the backlog is cleared
  by the block ending, not by probing it.
- Fewer requests reach a platform that is refusing us — one a day rather than
  one an hour, under a long block. If volume ever is the reason for a block,
  this helps; while it is not, it is one less thing we are doing wrong.
- A block that ends quietly, with nothing asked while it does, leaves the next
  video waiting up to the ceiling for no reason. A person who will not wait has
  the manual fetch, which is refused by the pre-check until the wait ends — so
  a wait at the ceiling cannot be cut short from the interface. Ticket #142
  covers the button for it; until then it is a row to edit.
- The count is only as good as the record. A refusal recorded by a process that
  then stops still counts, which is what we want, but the count cannot
  distinguish a platform that refused us ten times from one that refused us
  once and was never asked again for a week — hence the forgetting window
  rather than a count that only ever grows.
- The successful-request reset writes a row, where before a success wrote
  nothing. It is one small update, skipped unless there is a block to end, and
  beside the network request it follows it is nothing.
- A ceiling of a day means a single bad afternoon can leave a video waiting
  until tomorrow. The alternative is a wait that can grow without limit, which
  is how a source stops being fetched without anyone deciding that.
- This amends ADR 0007's "about an hour, configurable" and the part of ADR 0009
  that has one recorded end as the whole of a refusal's state. Everything else
  in both stands: a refusal is still waited out, still counts no attempt, still
  stops no run, and is still recorded where every fetch and a restart see it.

## Alternatives considered

- **Leave it flat and raise the hour.** One setting, no new state. But any
  constant is wrong at one end: an hour is too short for a block of a week, and
  a day is absurd for a refusal that lasts ten minutes. The flat hour's real
  fault is that it cannot tell the two apart.
- **Derive the escalation from the failed messages waiting on the refusal.**
  Their count and their `first_failed_at` hint at how long a block has lasted,
  and ADR 0009 rejected the same idea for the refusal itself, for the same
  reason: a refusal met where no failed message is recorded — a manual fetch of
  a video already stored — would leave no trace, and the backlog's age reflects
  the bugs behind it as much as the block.
- **Count the refusals in the running process.** No migration. But ADR 0009
  exists because a restart forgetting the cool-down asked the platform again at
  once, and a count that a restart resets is worth less than the hour it would
  replace, exactly when a block is long enough to matter.
- **Escalate from the time of the first refusal in the block rather than by
  doubling.** "Wait as long as this has been going on" needs no count, only the
  block's start. It is the same curve read differently, but it needs the start
  kept as well, it grows linearly where a block's cost is better bounded by a
  ceiling reached quickly, and a restart mid-block cannot tell a block's start
  from a refusal's.
- **Give up a message after enough refusals.** It would end the waiting, and
  ADR 0007 rejected it on its merits: a refusal is not the message's fault, and
  giving up every video a block touched means a person sending them all back.
  Nothing here changes that.
- **Ask the platform during the cool-down to see whether the block has
  lifted.** The wait would end as soon as the block did. But the probe is
  exactly the request the cool-down exists to prevent, and under a block it is
  what re-earns the wait — the 17 refusals in the log are that experiment
  already run.
