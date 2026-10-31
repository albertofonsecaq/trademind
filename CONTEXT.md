# TradeMind

A knowledge base that ingests trading content from public channels, extracts the trade ideas inside it, mines recurring patterns across sources, checks those patterns against real market outcomes, and teaches the validated ones back to the user. It is not a signal follower: the system reports what has historically worked, with evidence and confidence, and the user decides what to act on.

## Language

### Time

**Timestamp**:
An instant, stored timezone-aware in UTC and compared as an instant rather than as a wall clock. A value without a time zone is not one: it cannot be compared with anything read out of the database, and it is only ever correct by accident of a session setting. Connectors are the single boundary where a naive value is legitimate, because some platform APIs speak that way. See `docs/adr/0001-timestamps-are-aware-utc.md`.
_Avoid_: Naive datetime, local time, wall-clock time

### Tenancy and people

**Workspace**:
The unit of ownership and isolation for a knowledge base — its sources, evidence, and strategy cards. Every user gets a private one; a workspace may later gain members who share all of it.
_Avoid_: Account, tenant, org, team

**User**:
A person with login credentials. A user belongs to one or more workspaces and owns the personal layers inside them.
_Avoid_: Customer, account

**Member**:
A user's membership in a specific workspace, as either owner or member. Membership grants access to the workspace's knowledge base, never to another user's personal layer.
_Avoid_: Collaborator, seat (a seat is the billing unit, not the person)

**Super admin**:
A platform-level role that sits outside every workspace's membership and can act on any workspace — create one for a user, add members, or override payment enforcement.
_Avoid_: Admin (ambiguous with a workspace owner), staff

**Topic scope**:
A workspace's own statement of what subject matter belongs in it. Everything ingested is judged against it before any expensive processing runs.
_Avoid_: Filter, topic, niche

### Sources and ingestion

**Platform connection**:
One authenticated account on an external platform, belonging to a single workspace. Never shared across workspaces, and a workspace may hold several per platform.
_Avoid_: Integration, account, credential

**Source**:
A single tracked origin of content — one Telegram channel, one YouTube channel. Sources belong to a workspace and carry their own fetch cadence and content-type filters.
_Avoid_: Channel (a channel is one kind of source), feed, subscription

**Evidence**:
One captured piece of content from a source — a message, an image, a transcript, or a linked article — stored in its original language alongside anything extracted from it.
_Avoid_: Document, item, post, message

**Relevance check**:
The judgment of whether a piece of evidence falls inside the workspace's topic scope. It is a mandatory early stage: off-topic content never reaches vision, transcription, or distillation.
_Avoid_: Filtering, moderation, screening

**Distillation**:
Turning on-topic evidence into a structured trade idea plus bilingual summaries. Runs only on evidence that passed the relevance check.
_Avoid_: Parsing, extraction, enrichment

**Backfill**:
A one-off catch-up fetch of a source's history over a date range, as opposed to the recurring poll of new content.
_Avoid_: Import, sync, historical load

**Backfill outcome**:
How a backfill ended, recorded on the job and readable without the logs: _completed_ lost nothing, _partial_ ingested some and lost some, _failed_ hit a failure that would repeat for every remaining message and stopped, _cancelled_ was called off before it ran, by a pause or by the source losing the platform connection it fetches through. A failure that will repeat — an exhausted account, rejected credentials — ends the run; a rate limit or one unusable message does not.
_Avoid_: Success/error, done, aborted, batch

**Paused source**:
A source the user has deliberately stopped fetching from. It keeps its evidence and its fetch position, so resuming continues where it stopped; only future fetching is halted. Distinct from the automatic pausing of deferrable work when a budget is exhausted.
_Avoid_: Disabled, inactive, archived, muted

**Workspace ingestion pause**:
One switch that stops fetching for every source in a workspace at once — for a holiday, or to freeze spend today. Orthogonal to each source's own paused state, which survives underneath it, so switching it off resumes exactly the sources that were running before. It gates fetching and backfills only: pattern mining and validation answer to the budget, not to this switch, and reading the knowledge base is never affected.
_Avoid_: Workspace disabled, frozen workspace, suspended

**Resume mode**:
How a paused source starts fetching again, chosen by the user every time: _catch up_ ingests the backlog from where the pause stopped, _from now_ moves the fetch position to the source's newest content and declines the gap. Declining a backlog never deletes anything already ingested.
_Avoid_: Resume strategy, catch-up policy, skip

**Fetch position**:
How far through a source's content the system has already got, stored per source and advanced only forward. A platform gives it one of two shapes: a _bound_, which says "everything after this point" and survives the content it names being deleted (Telegram's message id), or a _marker_, which has to be found again in the platform's current window and is lost if that item disappears (YouTube's video id). A marker that cannot be found falls back to the timestamp of the newest evidence already stored, so a source never stalls on a deleted item.
_Avoid_: Cursor, offset, watermark, last seen

**Failed message**:
A message whose processing raised during a fetch or a backfill, recorded against its source so it is not lost when the fetch position moves past it. Each fetch of the source retries it first; after three failed attempts it is _given up_ — no longer retried, but kept and shown on the source with the last reason, until a person deals with it: by _sending it back_, which gives it a fresh three attempts from the next fetch, or by a backfill that stores it. A failure that would repeat for everything left in the run is not the message's fault and is not recorded. A platform _refusing_ us for a while (YouTube's 429) is different: the message is recorded without counting an attempt, and waits until the refusal ends before it is retried. See ADR 0005 and ADR 0007.
_Avoid_: Error, dead letter, poison message, skipped

**Backlog**:
The content a paused source has published since it was last fetched — what catching up would ingest. Measured from the fetch position, not from the pause.
_Avoid_: Queue, pending items, lag

### Knowledge

**Trade idea**:
The structured claim extracted from one piece of evidence — what instrument, what direction, and at what levels somebody proposed a trade. Ideas are what a source said, never what the system recommends.
_Avoid_: Signal, call, recommendation, tip

**Setup type**:
The named pattern a trade idea belongs to — the recurring shape that makes ideas from different sources comparable.
_Avoid_: Strategy (a strategy card is the mined artifact), pattern, category

**Strategy card**:
A mined pattern: a setup type on a symbol scope, described in plain language, backed by the trade ideas that evidence it and by whatever validation exists. This is the thing the system teaches.
_Avoid_: Strategy, pattern, playbook, rule

**Symbol scope**:
What a strategy card applies to — a specific instrument, or the whole market when the pattern is instrument-independent.
_Avoid_: Ticker, market, asset

**Confidence tier**:
How far a strategy card has earned trust, from still learning through developing to established. Derived from how much validated evidence stands behind it, and it is the tier the user sees.
_Avoid_: Score, rating, quality, maturity

**Analysis run**:
One pass of pattern mining or of validation over a workspace, started by a member or by the weekly schedule, and how it ended — in a backfill outcome's words: _completed_ lost nothing, _partial_ carried on past clusters or cards that failed and names each with the reason, _failed_ stopped on a failure that would repeat for everything left (a rejected API key) or was cut off by a restart. A workspace runs one at a time, of either kind, since both write its strategy cards; every member sees the same runs.
_Avoid_: Job, task, batch, refresh

### Asking

**Ask record**:
One question a user asked of their workspace's knowledge base and the answer the system synthesized for it, with the sources that answer cited. Private to the user who asked it, like a plan item: another member of the same workspace never sees it. Kept so that a question already answered can be read again instead of asked again, which is why reading one costs nothing — the stored answer and its cited sources are served as they were kept, never re-synthesized. Beside each source, what its evidence has to show now — a picture kept since — is looked up when the record is read, so an old answer shows it too. All of one user's records in one workspace are their **ask history**, which is the collection and never the name of a single record.
_Avoid_: Chat, conversation, message, thread

### Validation

**Outcome check**:
The result of replaying one trade idea against real market prices after the fact, to see whether it reached its target or its stop first.
_Avoid_: Backtest, result, verification

**Outcome**:
What a checked trade resolved to: won, lost, still open, expired unresolved, or inconclusive for want of data.
_Avoid_: Result, status, P&L

**Adverse excursion**:
How far price moved against a trade before it resolved — the risk the trade actually carried, as distinct from the loss it ended with. Favorable excursion is its mirror.
_Avoid_: Drawdown, max loss

**Walk-forward check**:
Testing whether a strategy card's win rate holds up on the ideas that arrived after the card was first mined, rather than only on the ones that produced it.
_Avoid_: Out-of-sample test, revalidation

### The user's own trading

Everything in this group is scoped to one user and is never visible to other members of their workspace, however many there are.

**Plan item**:
A user's private stance on a strategy card — whether they are watching it, their own notes, how it sits against their risk tolerance.
_Avoid_: Watchlist entry, bookmark, favorite

**Broker order**:
A proposed or placed order belonging to one user. An order cannot be submitted until the user has explicitly confirmed it; there is no automated execution anywhere in the system.
_Avoid_: Trade, position, execution

**Confirmation**:
The explicit human approval that moves a broker order from proposed to submittable. Its absence is a hard gate, not a default that can be configured away.
_Avoid_: Approval, authorization, ack

**Mode**:
Whether a broker order or journal entry is paper or live. Paper is the default; live is a deliberate opt-in.
_Avoid_: Environment, sandbox, real

**Journal entry**:
The record of a trade the user actually executed, paper or live, whether it came from a broker order or was logged by hand.
_Avoid_: Trade log, history, fill

**Execution score**:
How a user's own journalled trades performed, checked the same way trade ideas are. It measures the user's execution, where an outcome check measures a source's prediction.
_Avoid_: Performance, personal win rate

### Cost and billing

**Usage event**:
One billable call to an external model or service, logged with what it cost and what task it served. Every such call produces one, and the budget cap, the overage metering and the margin figures are all derived from them. Recorded in its own transaction, because a call that happened is owed whether or not the work it was for survived.
_Avoid_: Metric, log entry, transaction

**Included budget**:
The compute spend a workspace's plan covers in a period, scaled by seat count. It is derived from the subscription, never set by hand.
_Avoid_: Quota, allowance, credit

**Overage**:
Metered spend past the included budget, billed rather than absorbed. It accrues only up to a hard ceiling.
_Avoid_: Excess, extra usage

**Deferrable work**:
Work that can be paused when a budget is exhausted — backfills and recurring recomputation. What the user is actively waiting on is never deferrable, which is why it is metered as overage instead.
_Avoid_: Background jobs, batch, low priority
