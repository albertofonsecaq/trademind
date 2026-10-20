# 1. Timestamps are timezone-aware UTC, and connectors are the only naive boundary

Date: 2026-10-17

Status: accepted

## Context

Every timestamp column in this schema is `timestamptz` — 36 of them, created
that way by every migration since the first. No model declares
`DateTime(timezone=True)`, so SQLAlchemy binds writes as `TIMESTAMP WITHOUT TIME
ZONE`. The two directions disagree: reads come back aware, writes are bound
naive, and an aware value cannot be written at all.

Today's writes are nonetheless correct, for a reason nobody wrote down: the
database session runs with its time zone set to UTC, so Postgres interprets a
naive value as UTC. That is the whole of the safety margin. Point the
application at a database whose session time zone is anything else and every
stored timestamp shifts silently, with no error and no way to tell afterwards
which values were written under which assumption.

The symptoms have been paid for twice already. Creating a backfill with an
aware start date raised a driver error, which is why a `.replace(tzinfo=None)`
sits in the sources API — a workaround for a mismatched binding, not a decision
about time. Comparing a connector's naive timestamp against a bound read from
the database raised `TypeError` (#4), fixed at that boundary with a conversion
helper. Both are the same root cause one layer apart.

The write side is larger than it looks: 29 `datetime.utcnow()` calls across 12
files, plus 21 models whose columns default to `datetime.utcnow` — 50 places
producing a naive value, and `utcnow()` is deprecated in 3.12.

## Decision

**Timestamps are timezone-aware UTC everywhere in the application and the
database.** Models declare `DateTime(timezone=True)`, matching the schema they
have always had. Code asks `app.core.clock.now_utc()` for the current instant
rather than `datetime.utcnow()`.

**Connectors are the single exception**, if a platform ever needs one. The
connector layer is where such a conversion belongs, and nowhere else: a
conversion above that layer is a bug or a leftover, not a local decision.

> **Correction, 2026-10-18 (#34).** As written, this section claimed Telethon's
> message dates and YouTube's parsed `publishedAt` "genuinely speak naive UTC".
> Implementing the first batch disproved it: Telethon hands back aware UTC, and
> the YouTube timestamps are ours to parse, so neither platform we talk to
> insists on naive. `to_naive_utc` therefore has no caller. The decision stands
> — the exception is still the right shape for a platform that does need it, and
> the connector layer is still where it would live — but the examples were
> wrong, and whether to keep a caller-less helper is #37's to settle.

Nothing above the connector boundary stores, compares or returns a naive
timestamp.

## Consequences

The `.replace(tzinfo=None)` workarounds go, and aware values flow from an HTTP
request through the ORM to the column and back without conversion. Comparisons
against market-data timestamps and Stripe period boundaries — both aware —
stop being accidents.

The session time zone stops being load-bearing. Both engines — the
application's and the one Alembic runs migrations through — state it rather than
inheriting it, so a differently configured server cannot change what a naive
value means on the way in. #32 adds a database-backed test asserting it, so that
the assumption will fail loudly rather than silently.

This was a wide change: 36 columns across 18 models, and the writers that touch
them. It could not land as one green commit, so #8 sequenced it as
expand–contract. What the decision asked of each step was only this: a model's
reads and its writers move together, because a model that returns aware values
breaks any arithmetic still using a naive now.

> **Completed, 2026-10-19.** All 36 columns across all 18 models now declare
> `DateTime(timezone=True)`, every default and `onupdate` produces an aware
> value, and neither `datetime.utcnow()` nor `date.today()` appears in the
> application. Three things are worth recording, because they were discovered by
> doing the work rather than by planning it:
>
> - **A naive value is no longer read as UTC.** Once a column is declared aware,
>   the driver reads a naive value in the *machine's* local zone, with that
>   zone's rules for the value's own date — so the error is not even a constant
>   offset. This is why the API boundary reads offset-less payloads as UTC and
>   why nothing above the connectors may produce a naive value: the old
>   forgiving behaviour is gone.
> - **The Stripe subscription-period write could not previously have
>   succeeded.** Those timestamps arrive aware, and the column was bound naive,
>   which is this ADR's own failure mode. There are no subscription rows, which
>   is consistent with that.
> - **No platform we talk to needs naive.** `to_naive_utc` spent all three
>   batches with no caller, and #37 deleted it. The exception in the Decision
>   above still stands as the right *shape* — a platform that demands a naive
>   value gets converted at the connector boundary and nowhere else — there is
>   simply nothing to convert for today. A comment where the helper was says so,
>   and git history has the implementation if it is ever needed again.

**No migration is required and none should be written.** The columns are
already `timestamptz`; this makes the mapping tell the truth about them. Any
change that proposes altering a timestamp column's type under this ADR is
almost certainly wrong — the data in this database is months of paid ingestion
and is not reproducible.

## Enforcement

As of #37 this is checked rather than described. Tests walk every mapped model
and fail if a timestamp column does not declare its time zone, or if its default
or `onupdate` would produce a naive value; others fail if `utcnow()` or
`date.today()` is called anywhere in the application, or if a `.replace(tzinfo=None)`
appears outside the connector boundary — which is currently nowhere at all.

Each of those guards was verified by breaking the convention on purpose and
watching the right test fail. That matters more than it sounds: three of the
tests written across #34–#36 could not have failed as first written, and were
caught by review rather than by running. A guard that cannot fail is worse than
no guard, because it reads as coverage.

## Alternatives considered

**Store naive UTC and normalise at every edge.** This is what the code does by
accident today, and the accident is the argument against it: the invariant
lives in 29 call sites and a session setting rather than in the schema, nothing
enforces it, and every new column is a chance to get it wrong — as happened
twice while this very decision was being written. It also keeps the
`.replace(tzinfo=None)` calls, which read as deliberate and are not.

**Change the columns to `timestamp without time zone`** so the mapping matches
the models instead. Rejected: it makes the database the thing that forgets, and
it would mean rewriting 36 columns of irreplaceable data to fix a typing
mistake in Python.
