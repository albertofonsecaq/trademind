# 10. A failure of our own tooling is waited out, not counted

Date: 2026-11-11

Status: accepted

## Context

ADR 0005 sorts a failure two ways, and ADR 0007 adds a third. A failure that is
the message's is recorded and counts one of three attempts. One that would
repeat for everything left in the run stops the run and is not recorded. One
that is the platform refusing us for a while is recorded, counts no attempt,
and is waited out.

A fourth kind arrives by neither road. The audio Whisper transcribes is
downloaded with yt-dlp, whose extractor stops matching YouTube's player
whenever YouTube changes it. yt-dlp then says "Failed to extract any player
response" for every video it is asked for, and says, correctly, that the fix is
to upgrade it. We read it as the video's own failure: it became "Whisper failed:
…", spent one of that video's three attempts, and after three fetches the video
was given up with a reason naming Whisper. On 2026-11-10 that is what happened
to `sW1axKLV-t4` on the channel source.

This is the worst of the three readings to give it. It is not the message's
fault, so counting it gives up messages for something that was never about
them, and a person has to send each one back by hand after the upgrade — the
backlog the upgrade was meant to recover. It is not the platform's doing
either: YouTube is still serving captions, and recording a refusal would hold
every request to it up for a cool-down, which is the reading ADR 0009 exists to
keep narrow. And it will succeed on an ordinary retry, so it is not a failure
no attempt can fix (#137).

## Decision

A failure of our own tooling — our tool, our version, our fix — is recorded
against the message, counts no attempt, and is waited out.

- **No attempt is spent.** However long the tool stays stale, nothing is given
  up for it, so no message has to be sent back by hand once it is fixed.
- **The message waits, and says what to run.** The reason a person reads on the
  failed message names the cause and the command that fixes it, because the
  fix is ours to apply and nothing else will apply it.
- **It waits long enough to be quiet.** A stale tool fails every message it is
  asked for; retried at once, one source's backlog is a request a minute each.
  So a message that met it is not retried for an hour.
- **It is read in one place.** Which of a platform's words mean "our tooling is
  out of date" is decided where "the platform is refusing us" and "this video
  can never be transcribed" are decided, and a refusal is asked about first, so
  no new verdict can widen what a refusal is.
- **Nothing stops.** The fetch carries on, as it does for a refusal.

The version of such a tool is pinned, and how to refresh it is documented
beside what else ingestion needs. A failure that says our tooling is stale is
worth nothing if the version in use is whatever a machine happens to have.

## Consequences

- A stale tool costs a delay, not content: the upgrade is the whole fix, and
  the next fetch of each source picks up everything that was waiting.
- Nothing gives up a message waiting on us, so a tool left stale leaves
  messages waiting for ever, visible on their sources. That is the same cost
  ADR 0007 accepted for a platform that refuses for ever, and the same answer:
  a person sees them.
- This is read from what the tool says, so a new wording reads as the video's
  own failure and costs attempts, as before. That is the cost ADR 0009 already
  named for refusals, and it fails the same safe way.
- The record of a message waiting on us is indistinguishable, on the source,
  from one waiting out a refusal: both count no attempt and show a retry time.
  The reason tells them apart, and nothing yet counts them separately.
- This amends ADR 0005 without superseding it. Its two kinds and ADR 0007's
  third are unchanged; what counted an attempt because it was neither the
  platform's nor the run's is now sorted once more.

## Alternatives considered

- **Leave it as the video's own failure.** It needs nothing, and that is what
  it cost: a stale extractor gives up every video of a backlog within three
  fetches, each one then waiting for a person who has to send it back by hand
  to recover content the upgrade had already made available.
- **Record it as a platform refusal.** The waiting and the uncounted attempt
  come for free, but it would hold every request to YouTube up for a cool-down
  — captions included, which are not affected and are most of what works — and
  it would widen "the platform is refusing us" to mean "something about the
  platform went wrong", which is what ADR 0009 was written to prevent.
- **Give the message up at once, as for content that can never be had.** It
  reaches a person sooner, which sounds right for a failure only a person can
  fix, but it is the one reading that needs every message sent back by hand
  afterwards, and a backlog of hundreds makes that the expensive half of the
  recovery.
- **Stop the run, as ADR 0005 does for an exhausted account.** It is true that
  every later video will fail the same way, but captions still work, so a stop
  would cost the videos that have them, and the run is retried on the next tick
  anyway.
- **Upgrade the tool automatically when it says so.** It would need no person
  at all, but it installs unreviewed code on the strength of an error message,
  and an unpinned version is how a working machine and a broken one become
  indistinguishable.
