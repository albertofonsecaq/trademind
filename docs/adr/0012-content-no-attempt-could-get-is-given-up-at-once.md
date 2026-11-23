# 12. Content no attempt could get is given up at once

Date: 2026-11-11

Status: accepted

## Context

ADR 0005 gives a failed message three attempts, and gives up at once only one
kind of failure: a message the platform no longer offers. Everything else is
worth asking again for, and the reasoning holds — the cost of retrying
something hopeless is three attempts, and the cost of giving up on something
temporary is the content itself.

Some failures are hopeless and say so. A live event that has ended has no
recording behind it; a video the platform offers no audio format for will not
grow one. Both arrived as a transcription failure with the tool's words inside
it, became an ordinary failed message, and were retried an hour later and a
day after that. One of them was first recorded on 2026-11-04 and was still
taking its turn on 2026-11-11, because a refusal does not count an attempt
(ADR 0007) and the attempts were never spent.

Three things make that worse than a wasted retry. Each attempt is a request to
a platform that is refusing us, and that request re-earns the cool-down for
every other message (ADR 0010). While a refusal is in force only one message a
cool-down is asked for at all, so a hopeless one takes a turn that would
otherwise drain the backlog (ADR 0013). And the reason a person reads on the
source page was the tool's own sentence, which does not say that waiting is
pointless.

ADR 0009 drew a seam between a platform refusing us and a problem with one
video, because reading a refusal too widely held every request up. The same
seam, read once more, tells a problem with one video that is temporary — no
captions yet, which the platform often adds hours after an upload — from one
that is permanent.

## Decision

A failure that says no attempt could ever get this content gives the message up
at once, without spending the attempts it had, and records why in words a
person reads.

- **A failure is retried by default.** Only a failure that is the content's own
  and final is read this way. A platform refusing us is asked about first and
  is never one of these, whatever else it resembles: it is the platform's
  doing, not the content's, and every message met during a block would read the
  same way (ADR 0009). Nor is a failure of our own tooling (ADR 0011), an age
  check, a server error, or content whose transcript has simply not appeared
  yet.
- **The reason replaces the failure's own.** It says which case it was, in a
  sentence written for a person rather than the tool's words, because the
  message now waits for a person and that sentence is all they get.
- **A given-up message is not deleted.** It stays recorded and visible on its
  source with that reason, as ADR 0005 has it, and a person can send it back —
  which is the way out if a verdict here is ever wrong.
- **What can never succeed is a judgement about one platform's answers**, kept
  in the one place that already tells a refusal from a video's own problem, so
  no verdict can widen what a refusal is.

## Consequences

- A hopeless message costs one attempt instead of three, and costs the backlog
  no turns at all. Under a long run of refusals, where a cool-down's single
  request is the whole budget, that is the difference between a turn spent and
  a turn wasted for a day.
- A person reads why, and reads it in a sentence that tells them no retry will
  help. Before, they read the tool's error and could not tell that from a video
  whose captions had yet to appear.
- A wrong verdict costs the content. A failure read as final that was in fact
  temporary is given up without the two further attempts that would have got
  it, and only a person noticing and sending it back recovers it. That is why
  the list of final failures is a short list of exact answers rather than a
  pattern, and why a refusal is asked about first.
- The list has to be kept. A platform that changes the words of an answer stops
  being understood, and the failure degrades to the old behaviour — three
  attempts over a day — rather than to anything worse. That is the right way
  round, and it is silent: nothing announces that a verdict has stopped
  matching.
- This widens ADR 0005's "given up at once" from one case to a class. The rest
  of ADR 0005 stands: three attempts for an ordinary failure, the fetch
  position moving past it, the record visible until a person deals with it.

## Alternatives considered

- **Leave it at three attempts.** No new judgement, no list of answers to keep
  up to date, and the hopeless message gives itself up within a day — in
  ordinary conditions. The case that made this necessary is not ordinary
  conditions: under a refusal those three attempts are spread over days, and
  each one is a request that re-earns a cool-down for everything else.
- **Give up anything whose reason has not changed across attempts.** It needs
  no knowledge of any platform's words and would catch cases nobody has listed
  yet. But "the same reason twice" is also what a block looks like, and a
  platform repeating "no captions yet" for an hour is the case ADR 0005's
  growing gaps exist for.
- **Keep retrying but stop paying.** Only ask the cheap way, never the paid
  one, for a message like this. It keeps the content reachable if the verdict
  was wrong, but a message that is never going to succeed stays in the queue
  for ever, taking turns under a refusal, which is the cost that mattered here.
- **Delete the message rather than give it up.** The row says nothing anybody
  will act on, so it could go. But ADR 0005's record exists so that what was
  lost is legible, and a verdict read from a platform's wording is exactly the
  kind of thing a person should be able to find and overrule.
