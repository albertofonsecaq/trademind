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

There are 29 `datetime.utcnow()` calls across 12 files. Each returns a naive
value, and `utcnow()` is deprecated in 3.12.

## Decision

**Timestamps are timezone-aware UTC everywhere in the application and the
database.** Models declare `DateTime(timezone=True)`, matching the schema they
have always had. Code asks `app.core.clock.now_utc()` for the current instant
rather than `datetime.utcnow()`.

**Connectors are the single exception.** Some platform APIs genuinely speak
naive UTC — Telethon's message dates, YouTube's `publishedAt` once parsed — so
the connector layer converts at its own boundary with `to_naive_utc`, and that
helper exists for that reason alone. A conversion anywhere else is a bug or a
leftover, not a local decision.

Nothing above the connector boundary stores, compares or returns a naive
timestamp.

## Consequences

The `.replace(tzinfo=None)` workarounds go, and aware values flow from an HTTP
request through the ORM to the column and back without conversion. Comparisons
against market-data timestamps and Stripe period boundaries — both aware —
stop being accidents.

The session time zone stops being load-bearing. It is set explicitly on the
engine so a differently configured server cannot change the meaning of stored
data, and a database-backed test asserts it (#32), so the assumption fails
loudly rather than silently.

This is a wide change: 36 columns across 18 models, and the writers that touch
them. It is sequenced as expand–contract in #8 — the convention and the clock
first, then one batch of models per domain area, then the shims are deleted and
a test asserts no model can declare a bare timestamp again. Each batch keeps
the suite green, because a model's reads and its writers move together.

**No migration is required and none should be written.** The columns are
already `timestamptz`; this makes the mapping tell the truth about them. Any
change that proposes altering a timestamp column's type under this ADR is
almost certainly wrong — the data in this database is months of paid ingestion
and is not reproducible.

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
