# Working near the live database

Standing instructions for any agent working in this repo. Unlike most things
here, the subject of this file cannot be undone by a revert.

The local Postgres holds months of paid ingestion: evidence that cost relevance
checks, images that cost vision calls, transcripts that cost Whisper, and source
history that is no longer fetchable from the platforms it came from. It cannot
be rebuilt by re-running anything, at any price. Everything below follows from
that one fact.

## The rule

**An agent reviewing, analysing or exploring code does not execute anything
against the live database.**

Not the application, not a script, not a function imported out of one, not a
test run that points at it. Read the code and reason about it instead.

Writing a feature is different: the work sometimes has to touch real data, and
then it is announced before it happens, a verified backup is taken first, and
the change is rehearsed against a copy. Reviewing is never that work.

### What counts as executing against it

The rule is about the target, not about the name of the thing you ran. All of
these are the same act:

- running `backend/scripts/*.py`, `psql`, `createdb`, `dropdb`, `pg_restore`,
  `alembic`, or `docker compose exec db …`
- importing a function out of one of those scripts and calling it
- running the test suite with `TEST_DATABASE_URL` pointed at `trademind`
- editing a guard, a constant or a condition and then running the thing it
  guarded, to see whether the guard worked

That last one is not a hypothetical. It is what happened, and the agent that did
it had been told in its own prompt not to run the script in question. It did not
run the script. It called the script's function with the guard removed.

## Where to run it instead

```bash
python backend/scripts/scratch_db.py
```

That restores the newest verified dump into a disposable copy, gives it the same
roles the live database has, and prints how to connect to it and how to delete
it. Point anything destructive there: drop a table to check a permission,
exercise a guard, try a migration's downgrade, rehearse a restore.

A rule with no alternative is just a prohibition, and a prohibition with nowhere
to go is how production became the only database to hand.

## What a review has to report

**A review that claims code works says what it ran to find out.**

Not a summary of the conclusion — the commands. A report that says only what it
concluded cannot be checked, and one did: it described itself as clean while the
server's log recorded it dropping two databases.

If something could only be settled by running it, and running it was not
allowed, the honest report says that rather than quietly reasoning its way to a
confident-sounding answer.

## What happened

On 2026-10-22 a code-review agent was asked to verify that some new tests could
actually fail. It mutated `_drop_throwaway` — the function whose only job is to
refuse any database name that is not a throwaway — and called it with real
names. At 00:41:08 UTC two forced checkpoints in the Postgres log mark two
successful `DROP DATABASE` statements; at 00:41:09 the log records
`DROP DATABASE IF EXISTS ""`. The application database and the server's
`postgres` database were both gone. Only `template0` and `template1` survived.

Nothing was lost. A verified dump taken five minutes earlier restored every row
— 8,582 evidence items, 1,274 trade ideas, 11,630 usage events — and the
scheduled backup that produced it had been written that same afternoon. The
recovery was luck in its timing, not in its existence.

This file exists because the rule that would have prevented it was written in a
prompt, where it lasted exactly one conversation.

## What is already enforced without you

Instructions are the weaker half, and they are not the only half.

The application's credentials own nothing and cannot drop a table or a
database. The backup's credentials can read every table and create their own
throwaway, and are refused — by Postgres, not by a condition in a script — when
asked to drop the live database. The scratch copies are created and destroyed by
that same role, so the tool this file points you at cannot be turned on the
thing it is protecting you from. See
`docs/adr/0004-the-application-owns-nothing-in-its-own-database.md`.

The gap those permissions leave is why this file is still necessary. The
container's bootstrap superuser exists and trusts local socket connections, so
anything with shell and Docker access can still destroy everything — including
an agent that decides a rule was about a filename.

## If you are reviewing, say so in the prompt as well

These instructions are standing, and an agent spawned for a review still
benefits from being told the same thing in its own prompt: belt and braces cost
nothing. Both halves failed once each already — the prompt because it named a
file instead of an action, and the docs because they said nothing at all.
