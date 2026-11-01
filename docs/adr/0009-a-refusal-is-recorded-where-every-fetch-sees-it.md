# 9. A refusal is recorded where every fetch sees it

Date: 2026-11-01

Status: accepted

## Context

ADR 0007 has a platform that refuses us waited out: once it refuses, nothing
more is asked of it until a cool-down passes. It accepted a cost: the
cool-down lived in the running process. A restart forgot it and asked the
platform again. A manual fetch, a backfill and the poller running side by side
could each ask before one of them had set it.

Each of those is one request more to a platform that has just told us to stop,
and asking anyway is what kept YouTube refusing for three days in October.

The same refusal also reached us by another road, and was not read as one.
When a video has no captions, its audio is downloaded for Whisper through
YouTube too. YouTube blocking that download was read as Whisper failing, so the
video used up its attempts instead of waiting.

## Decision

A refusal is recorded in the database, one per platform, with until when and
why. Every fetch reads it before asking that platform, and records it when it
meets one. Of two refusals recorded close together, the later end holds.

YouTube refusing the audio download for Whisper, with a 429 or a demand to sign
in and prove we are not a bot, is the same refusal as for captions: recorded
alike, waited out alike.

## Consequences

- No fetch asks YouTube while a refusal it can see is in force, whichever
  process or kind of fetch it is, and a restart changes nothing.
- Asking costs a read of one row before each video's captions. It is small
  beside the request it guards.
- Two fetches that meet the refusal at the same moment, before either has
  recorded it, still ask once each. Nothing smaller than a lock around every
  request would stop that, and the poller fetches sources one at a time.
- A refusal outlives the block itself if the block ends early: nothing is asked
  until the recorded end. That is the cool-down working as meant.
- Which download errors mean "YouTube is refusing us" is a list of what yt-dlp
  says, and yt-dlp's words can change. One it does not know reads as Whisper
  failing, which costs an attempt, as before.
- This amends ADR 0007's consequence that the cool-down lives in the running
  process. Its decision is unchanged.

## Alternatives considered

- **Derive the refusal from the failed messages waiting on it.** It needs no
  new table, but a refusal met by a fetch that records no failed message, a
  manual fetch of a video already stored, say, would leave no trace, and the
  next fetch would ask again.
- **Keep it in the process and accept the restart.** It needs nothing, but a
  backfill started during a block, the moment a person most wants to recover
  what was lost, would ask again at once.
- **Leave the download's refusal as Whisper failing.** It needs no list of
  yt-dlp's words, but a block would spend every attempt of each video without
  captions within a day and give them up.
