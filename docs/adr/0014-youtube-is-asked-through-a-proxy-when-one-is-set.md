# 14. YouTube is asked through a proxy when one is set

Date: 2026-11-23

Status: accepted

## Context

ADRs 0007, 0009 and 0010 treat YouTube refusing us as something that ends:
the refusal is recorded, the platform is left alone for a cool-down, and the
cool-down grows while the refusals go on. All three assumed we were refused for
something we did — the 58,000 requests of October — and that leaving YouTube
alone would get it to stop.

That is no longer what is happening. YouTube answers this machine's caption
requests with `IpBlocked` at about one request an hour, which is not volume:
ADR 0010 already says waiting longer does not get it to stop sooner. What it
blocks is the address. Every video of every YouTube source, and every YouTube
link posted in a Telegram channel, waits on a refusal that nothing we can wait
out will end.

The captions library names the cause and the remedy itself: requests from a
cloud or residential address YouTube has flagged are refused, and the way past
it is to send them through a rotating residential proxy, which the library
supports directly.

Discovery is unaffected. Finding a channel's videos goes through the YouTube
Data API with our key, which is not refused.

## Decision

When a proxy is configured, every caption request to YouTube goes through it;
when none is, nothing changes.

- **One optional setting, empty by default.** It holds the proxy's URL. Unset,
  caption requests leave from this machine as before, so a deployment that is
  not blocked needs nothing new.
- **Every caption request uses it**, whichever source reached the video — a
  YouTube source, or a YouTube link in a Telegram message — because both go
  through the same transcript code (#69, #70). Discovery through the Data API
  does not.
- **A refusal is still a refusal.** A proxy changes where we ask from, not what
  YouTube's answer means. A refusal met through it is read in the same place,
  recorded in the same row, and waited out with the same growing cool-down
  (ADRs 0007, 0009, 0010). The proxy is not a reason to ask during a
  cool-down: a rotating pool that is refused is refused.
- **The URL is a secret.** It carries the proxy's credentials. It is held as a
  secret setting, so printing the settings does not show it, and it is never
  logged and never written into a failed message's reason. Errors from the
  network stack name the proxy they could not reach, so any text from such an
  error is scrubbed of the URL — and of its user, password and host — before it
  is logged or kept.

## Consequences

- A blocked address stops costing us YouTube's captions, for as long as the
  proxy's addresses are not blocked too. Nothing is ingested from YouTube
  otherwise.
- It is paid for, by the traffic. Caption requests are small and few — one per
  new video, and retries are held to the cool-down — so the cost is bounded
  by the number of videos, not by polling.
- A new way to fail arrives with it: the proxy itself down, unreachable, or
  rejecting our credentials. Until that is told apart (#153) it reads as a
  video with no captions, as any other non-refusal error from the library does,
  and costs attempts. That is the reading ADR 0011 was written against for our
  own tooling, and #153 gives a broken proxy the same treatment.
- Videos with no captions still have their audio downloaded for Whisper from
  this machine's address, which YouTube refuses for the same reason (ADR 0009).
  Until that download goes through the proxy too (#152), a video that needs
  Whisper still meets the block.
- Scrubbing is by text. An error that says a part of the proxy URL in a form we
  do not look for — encoded some other way — would carry it through. The URL
  itself, its credentials and host, and their percent-decoded forms are what is
  looked for.
- Whoever operates the proxy sees which videos we ask about. They are public
  videos, and nothing about the user travels with the request.

## Alternatives considered

- **Send cookies from a signed-in YouTube account.** The library once
  supported it, and a signed-in session is refused less. But it ties ingestion
  to a real Google account that YouTube can — and, for automated traffic,
  does — suspend, and losing that account is a far larger cost than a blocked
  address. The library has also disabled cookie authentication, because
  YouTube's changes broke it.
- **Use the official captions endpoint of the Data API.** It is not refused,
  and it is the sanctioned road. But it only downloads captions for videos on
  channels we own; for anyone else's video it answers with a permission error,
  and every source here is someone else's channel.
- **Keep waiting.** It needs nothing, and ADRs 0007 to 0010 make the waiting
  safe. But a block of the address is not ended by our silence, so the waiting
  has no end, and ADR 0010 has already said so.
- **Move the backend to another address.** It works until the new address is
  blocked too, which for a datacentre address is soon; a rotating residential
  pool is what the library recommends because each request can leave from a
  different, unflagged address.
- **Route all YouTube traffic, the Data API included, through the proxy.** One
  rule instead of two. But the Data API is not refused, it is authenticated by
  our key rather than by our address, and sending it through a paid proxy costs
  money and adds a way for discovery to fail for nothing.
