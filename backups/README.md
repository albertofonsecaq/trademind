# Database backups

This holds dumps of the TradeMind database. **The dumps themselves are
gitignored** — they are large, and a dump of real data does not belong in
version control. Only this file is tracked.

What is in here is not reproducible. The database is months of paid ingestion:
evidence that cost relevance checks, images that cost vision calls, transcripts
that cost Whisper. Re-running the pipeline would re-pay for all of it, and some
of the Telegram history behind it may no longer be fetchable at all.

## The schedule

`backend/scripts/backup.py` dumps the database, restores the dump into a
throwaway database to prove it is real, copies it to `BACKUP_MIRROR_DIR` if one
is set, and prunes under the rule below.

**Nothing runs it until you install the agent.** Until then this is a script you
can run by hand, which is not a backup arrangement:

```bash
backend/scripts/install-backup-schedule.sh --mirror ~/path/to/a/synced/folder
backend/scripts/install-backup-schedule.sh --uninstall
```

Installed, it runs daily at 03:00. launchd runs a calendar job it missed once
the machine wakes, so a laptop asleep at 03:00 still gets its dump. The script
starts the database container if it is not running — and leaves it running,
because stopping something somebody else started is not its business.

**Retention: the newest dump of each of the last 7 days, plus the newest of
each of the last 4 ISO weeks.** About 1.3 GB at today's sizes. Days and weeks,
not files: this script is runnable by hand, and counting files would let an
afternoon of manual runs pass as a week of history and delete the real one. Two
dumps on one day is one day of history, and the later one is it.

The weekly half is the half that matters — seven days only covers damage
somebody noticed inside a week, and nobody is watching. It buckets by ISO week
rather than looking for Sundays, so a month of missed Sundays still leaves
weekly history.

**It will never delete a dump whose name does not end in `-auto.dump`.** That is
the three below, and anything you take with `pg_dump` yourself. Note what this
does *not* mean: a dump from running `backup.py` by hand is an `-auto` dump and
is pruned like any other. If you want one kept for ever, take it by hand.

**Set `BACKUP_MIRROR_DIR`.** Without it every copy is on this machine, and the
machine is the thing most likely to be lost. The script says so, loudly, on
every run. If it is set and the directory is missing — an unplugged disk — the
run fails rather than quietly keeping the dump local.

### Is it working?

```bash
tail -5 backups/backup.log              # what the last runs did
ls backups/LAST-RUN-FAILED              # exists ⇒ the last real attempt did not finish
launchctl print gui/$UID/com.trademind.backup | head -20
launchctl kickstart -p gui/$UID/com.trademind.backup    # run it now
```

A `--dry-run` or a `--prune-only` deliberately leaves that marker alone: neither
proves backups work, and clearing it would erase the evidence that last night's
real run failed — which is exactly when somebody is in here poking by hand.

A failed run keeps its dump as `*.FAILED-VERIFICATION`, writes the marker file,
logs why, and raises a desktop notification. It does not delete anything.

## If the roles are the problem

The application, the migrations and the backup use three different roles, and
the application's owns nothing (#59, ADR 0004). If that arrangement is what is
in your way — a restored copy nothing can read, a migration refused, a role
whose password nobody has — there are two commands:

```bash
# Put the roles on a database that has none. Any restored copy needs this.
SUPERUSER_DATABASE_URL=postgresql://trademind:trademind@localhost:5432/<database> \
  python backend/scripts/setup_db_roles.py

# See who owns what, before changing anything.
python backend/scripts/setup_db_roles.py --show
```

And one way back, which hands everything to the bootstrap superuser again:

```bash
python backend/scripts/setup_db_roles.py --revert          # says what it would do
python backend/scripts/setup_db_roles.py --revert --yes     # does it
```

Reverting means the application's credentials can destroy the database again, so
it is for getting out of trouble rather than somewhere to stay. It moves no
rows, and applying afterwards is the same command as the first time.

## Restoring, under pressure

Read this first: **restore into a new database, look at it, and only then swap.**
Restoring over the live database is the one move that cannot be undone.

```bash
# 1. Pick the newest dump whose revision matches the code you are running.
ls -lt backups/*.dump                   # the revision is in the filename

# 2. Restore it beside the live one, never over it.
docker compose up -d db
docker compose exec -T db createdb -U trademind trademind_restored
cat backups/<file>.dump | docker compose exec -T db \
  pg_restore -U trademind -d trademind_restored --no-owner --exit-on-error

# 3. Look at what you got before trusting it.
docker compose exec -T db psql -U trademind -d trademind_restored -tAc \
  "SELECT 'evidence='||(SELECT count(*) FROM evidence_items)
        ||' ideas='||(SELECT count(*) FROM trade_ideas)
        ||' usage='||(SELECT count(*) FROM usage_events)
        ||' rev='||(SELECT version_num FROM alembic_version)"

# 4. Give the restored copy the same roles the live one has. It is owned by
#    whoever restored it and has no grants, so the application cannot read it
#    until this runs (#59, ADR 0004).
SUPERUSER_DATABASE_URL=postgresql://trademind:trademind@localhost:5432/trademind_restored \
  python backend/scripts/setup_db_roles.py

# 5. Point the application at it — no rename, nothing destroyed.
#    In backend/.env:
#      DATABASE_URL=postgresql+asyncpg://trademind_app:trademind@localhost:5432/trademind_restored
```

Step 5 is deliberately not "rename the databases". Leaving the damaged one in
place costs 120 MB of disk and keeps every option open; renaming over it closes
them for good. Rename later, once the restored database has been running for a
while.

If the dump's revision is *older* than the code, run `alembic upgrade head`
against the restored database before step 5. If it is *newer*, check out the
commit that matches it instead — migrations do not run backwards for free.

## Taking one by hand

```bash
docker compose up -d db
docker compose exec -T db pg_dump -U trademind -d trademind -Fc \
  > backups/trademind-$(date -u +%Y%m%d)-alembic$(
      docker compose exec -T db psql -U trademind -d trademind -tAc \
        'SELECT version_num FROM alembic_version' | tr -d '[:space:]'
    ).dump
```

The migration revision is in the filename on purpose: a dump only restores
cleanly against code that expects its schema, so the revision is the thing you
need to know before trusting one. The scheduled dumps carry it too, plus a
timestamp and an `-auto` suffix that keeps the retention rule away from
anything taken by hand.

## Checking one is real

A dump you have not restored is not a backup. Restore it into a throwaway
database — never over the live one — and compare:

```bash
docker compose exec -T db createdb -U trademind trademind_hand_check
cat backups/<file>.dump | docker compose exec -T db \
  pg_restore -U trademind -d trademind_hand_check --no-owner --exit-on-error
docker compose exec -T db psql -U trademind -d trademind_hand_check -tAc \
  "SELECT 'evidence='||(SELECT count(*) FROM evidence_items)||' usage='||(SELECT count(*) FROM usage_events)"
docker compose exec -T db dropdb -U trademind trademind_hand_check
```

As the superuser above, that replays everything in the dump. The scheduled run
is not a superuser (#59, ADR 0004), so it adds `--no-acl --no-comments` and
copies its throwaway from a template that already has pgvector — ownership,
grants, comments and `CREATE EXTENSION` all need privileges it deliberately
lacks, and none of them say anything about whether the rows are there.

`--exit-on-error` is not optional: without it `pg_restore` reports problems and
carries on, exits 0, and the counts you then read come from a partial restore.
The name avoids `trademind_verify_*`, which the script uses for its own
throwaways and is the only name it will agree to drop.

## What is here

Hand-taken dumps only. The scheduled ones come and go under the retention rule,
so a table of them would be wrong within a week — `ls -lt backups/*-auto.dump`
is the answer to "what do we have", and `backups/backup.log` to "did it work".

| File | Taken | Revision | Verified | Why |
| --- | --- | --- | --- | --- |
| `trademind-20261018-alembic016.dump` | 2026-10-18 | 016 | restored to a scratch database, 127 objects, 8,582 evidence / 1,274 ideas / 11,630 usage events | before #34, the first batch of #8 to change how stored timestamps are read |
| `trademind-20261018-alembic016-pre35.dump` | 2026-10-18 | 016 | restored to a throwaway database, 8,582 evidence / 1,274 ideas / 11,630 usage events | before #35. Its own tables (cards, checks, plans, journal, orders) were all empty, so this dump protects the ingestion data rather than anything #35 touches |
| `trademind-20261018-alembic016-pre36.dump` | 2026-10-18 | 016 | restored to a throwaway database, 2 users / 2 workspaces / 2 members / 11,630 usage events | before #36. Unlike #35, these tables hold data — the usage events the budget cap is computed from |

## Counts after #8's batches

#34, #35 and #36 each recorded counts before starting; this is the state after
all three, for comparison against the rows above. Nothing was added, removed or
rewritten by that work — it changed how values are *declared and read*, not the
values:

    users=2, workspaces=2, members=2, evidence=8582, ideas=1274, usage=11630

Taken 2026-10-19, at alembic revision 016.

## Before changing how timestamps are stored or read

#8's batches (#34–#37) change the meaning of values already in these tables,
not just the code around them. Take a dump and verify it first — #34's own
acceptance criteria say so, and the same applies to #35 and #36.
