# 7. A platform refusing us is waited out

Date: 2026-10-31

Status: accepted

## Context

ADR 0005 sorts a failure two ways. A failure that is the message's is recorded,
and counts one of three attempts. A failure that would repeat for everything
left in the run (an exhausted account, a rejected session) stops the run, and
nothing is recorded.

A platform refusing us for a while fits neither. Between 28 and 31 October,
YouTube answered every caption request from this machine with 429 Too Many
Requests. The refusal was read as "this video has no captions", so nothing was
recorded and the fetch position of the new YouTube source never moved. Every
poll asked again for the same 79 videos, about 58,000 requests in three days,
which is very likely what kept the refusal going. The source ingested nothing,
and the log said "No transcript available" for videos that had captions.

Counting a refusal as an attempt would give up every video after three fetches.
For a source fetched every minute, that is three minutes into a block that
lasts hours. Stopping the run would be worse. A YouTube link posted in a
Telegram channel goes through the same captions, so one refused link would
stop that channel's ingestion of everything else. The user's rule is that
nothing stops a fetch except the Anthropic account having no credit left.

## Decision

When a platform refuses our requests for a while, the refusal is waited out.
It is not the message's fault, and it is not the run's end.

- **The refused message is still recorded.** It becomes a failed message with
  the refusal as its reason, and the fetch position moves past it, as ADR 0005
  says. The refusal does not count as an attempt, so no refusal, however long,
  gives a message up.
- **Retries wait.** The refusal says when it ends, and a failed message is not
  retried before then. If a retry is refused, the source's other failed
  messages wait for the same end without being asked for.
- **The platform is left alone.** Once it refuses, nothing more is asked of it
  until a cool-down passes: about an hour, configurable. Content met in the
  meantime is recorded the same way, without a request. The refusal is said
  once in the log, with when requests resume, not once per message.
- **The run carries on.** A YouTube source moves on to its next video, and a
  Telegram channel goes on with its other messages. Only failures ADR 0005
  already stops a run for still stop it.

Today the only platform refusal is YouTube refusing captions: a 429, a
request blocked or our address blocked. Any other error from YouTube, a 403
included, is still read as "no captions".

## Consequences

- A block costs a delay, not content: once YouTube stops refusing, the next
  fetch after the cool-down picks the refused videos back up.
- A refused video from a YouTube source is recorded as the video itself, not
  as its transcript's chunks, since there is no transcript yet. Its transcript
  is asked for when it is retried, by the same path as a YouTube link from
  Telegram.
- The cool-down lives in the running process. A restart forgets it and asks
  YouTube once more; a refusal then starts it again. The wait recorded on each
  failed message is kept in the database and survives a restart.
- A refused video is not sent to Whisper. Whisper would be paid for something
  YouTube will hand over later, and its download would likely be refused too.
- A platform that refuses forever leaves its messages waiting forever, visible
  on the source but never given up. A person sees them; nothing clears them on
  its own.
- This partly amends ADR 0005. A failure that would repeat for everything left
  in the run still stops the run when it is ours (credit, credentials). When it
  is a platform refusing us for a while, it is waited out instead.

## Alternatives considered

- **Count a refusal as an attempt.** It needs nothing new, but a block of a few
  minutes gives up everything it touched, and a person has to send it all back.
- **Stop the run, as ADR 0005 does for an exhausted account.** It asks nothing
  more of YouTube, but one refused link stops a whole Telegram channel, and a
  stopped run is retried on the next tick anyway, which is the flood again.
- **Keep the wait only in memory.** It needs no new column, but retrying a
  waiting video still looks it up in the channel window every minute through
  the YouTube Data API, which would use up the daily quota within hours. A
  restart would also forget when to try again.
- **Fall back to Whisper.** The content would arrive sooner, but it is paid for
  each video and its download goes through the same refusing platform.
