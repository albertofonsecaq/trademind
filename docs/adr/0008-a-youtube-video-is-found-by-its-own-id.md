# 8. A YouTube video is found by its own id

Date: 2026-10-31

Status: accepted

## Context

ADR 0005 has every connector fetch a failed message again by its own
identity. For YouTube it accepted a cost: the connector found a video only by
walking the channel's newest uploads, one page of 50, so a video that had
left that window could no longer be retried and was given up, although it was
still on YouTube.

That cost grew. A YouTube block lasting hours (ADR 0007) holds a channel's
videos back while new ones push them down the list. A video with no
transcript is now retried over a day. And recovering the videos a source
skipped during the block of 28–31 October needs a backfill that can reach
past the newest 50, which the same walk prevented.

## Decision

A YouTube video is fetched again by its own id, wherever it is on its
channel. It is given up as gone only when YouTube no longer shows it: deleted,
made private, or not the source's channel when the source names its channel
by id.

A backfill reads a channel's uploads page by page until a whole page was
published before the range starts, and a playlist source, whose order is its
owner's, to its end. A video's date is when it was published, not when it was
added to the list of uploads. A poll still reads the newest page only.

## Consequences

- A failed message on YouTube is no longer given up for being old. A retry
  costs one unit of the Data API's daily quota, fewer than the walk did.
- A backfill costs one unit per 50 videos it reads, plus finding the
  channel's list of uploads, so a long range costs more than before. Before,
  it reached nothing older, so it cost less only by doing less.
- A whole page has to be older than the range before a backfill stops, so it
  usually reads one page more than it needs. A premiere is added to the
  uploads before it is published, and stopping at the first older video would
  miss what comes after it.
- A source named by its @handle does not have each video's channel checked:
  that would cost a lookup on every retry. Its ids come from its own failed
  messages, so a video of another channel does not reach it.
- This amends ADR 0005's consequence about YouTube's window. Its decision is
  unchanged.

## Alternatives considered

- **Keep walking the newest uploads, but more pages of them.** It needs no new
  call, but a retry would cost a page per 50 videos the video has sunk, and a
  retry of an old video would read most of the channel.
- **Store each video's publish date and search by date.** The Data API has a
  search by date, but it costs a hundred units a call and does not promise
  every video.
- **Stop a backfill at the first video older than its range.** It reads the
  fewest pages, but the uploads are in the order they were added, so a
  premiere published inside the range can sit past that point and be missed
  without anyone knowing.
