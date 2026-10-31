# 5. A failed message is retried, not skipped

Date: 2026-10-27

Status: accepted, amended by 0007

## Context

The fetch position moves forward past everything a fetch has seen, including a
message whose processing raised. It has to: a position held back by one bad
message would make every later tick re-fetch, and re-pay for, everything after
it (#19). But it meant a message that failed was gone for good. Nothing
recorded it, nothing came back to it, and the only trace was a log line in a
file that rotates. A backfill counted its failures and lost them the same way.

On 25–26 October that cost twelve links to one bug and sixty-three videos to a
missing tool. Both bugs were fixed within a day, and neither fix could bring
anything back, because nothing said which messages had been lost. They were
recovered by hand (#93, #97).

## Decision

A message that fails during a fetch or a backfill is recorded against its
source: which message, why, how many times, and when. The fetch position still
moves past it.

Each fetch of a source first retries that source's recorded failures, fetching
each message again from the platform by its own identity. A success removes the
record. A failure counts another attempt, and so does failing to fetch it
again. After the third failed attempt the message is given up: it is no longer
retried, and it stays recorded and visible on the source, with the last reason,
until a person deals with it. A message the platform no longer offers — deleted
there, or a kind of content the source no longer asks for — is given up at
once. A message a backfill stores is no longer a failure, whatever its state.

A failure that would repeat for everything left in the run — an exhausted
account, a rejected session — is not the message's fault. It is not recorded
and does not count as an attempt; the run stops as it did before.

## Consequences

- A passing bug costs a delay rather than the content: once it is fixed, the
  next fetch picks its failures back up.
- Every connector has to be able to fetch one message again by its identity,
  which is new work for each platform. On YouTube that means finding the video
  in the channel's current window: a video that has left the window can no
  longer be retried and is given up.
- A retry pays again. On YouTube a transcript without captions is transcribed
  again for each attempt, and a Telegram video is downloaded again. Three
  attempts bound that.
- A message that fails for a reason no retry can fix costs three attempts
  before it is given up, and then waits for a person.
- An outage of the platform or of a service a message needs, lasting three
  fetches of a source, gives up that source's recorded failures. For a source
  fetched every minute that is three minutes. They stay visible, and a backfill
  over their dates stores them and clears them; there is no button for it yet.
- Given-up messages accumulate until someone looks. That is the point — they
  are what was lost — but nothing clears them automatically.

## Alternatives considered

- **Hold the fetch position at the first failure.** It needs no new record and
  loses nothing, but one message that always fails stops the source for ever,
  and every tick re-fetches and re-pays for everything after it. That trade was
  already rejected once (#19).
- **Store the message itself with the failure and retry from the copy.** It
  needs no connector change, but an image makes the row large and a video
  cannot be kept at all: its download is a temporary file deleted after the
  attempt. Fetching again also means a retry sees the message as it is now.
- **Retry for ever, with growing gaps.** Nothing would ever be given up, but a
  message that can never succeed would cost money on every attempt, for ever,
  and would never reach a person.
- **A separate retry job.** It would retry on its own schedule, but would need
  its own copy of every gate the fetch already checks — a paused source, a
  paused workspace, a lapsed bill, a rejected connection — and could disagree
  with them.
