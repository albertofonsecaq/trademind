# Database backups

This holds dumps of the TradeMind database. **The dumps themselves are
gitignored** — they are large, and a dump of real data does not belong in
version control. Only this file is tracked.

What is in here is not reproducible. The database is months of paid ingestion:
evidence that cost relevance checks, images that cost vision calls, transcripts
that cost Whisper. Re-running the pipeline would re-pay for all of it, and some
of the Telegram history behind it may no longer be fetchable at all.

## The schedule

A launchd agent runs `backend/scripts/backup.py` daily at 03:00. It dumps,
restores the dump into a throwaway database to prove it is real, copies it to
`BACKUP_MIRROR_DIR` if one is set, and prunes under the rule below. It starts
the database container if it is not running, because at 03:00 it usually is not.

```bash
backend/scripts/install-backup-schedule.sh --mirror ~/path/to/a/synced/folder
backend/scripts/install-backup-schedule.sh --uninstall
```

**Retention: the last 7, plus the last 4 Sundays.** About 1.3 GB at today's
sizes. The weekly half is the half that matters — seven days only covers damage
somebody noticed inside a week, and nobody is watching.

**It will never delete a dump taken by hand.** Pruning only considers the names
the script writes, which end in `-auto.dump`. The three dumps below are safe
from it permanently.

**Set `BACKUP_MIRROR_DIR`.** Without it every copy is on this machine, and the
machine is the thing most likely to be lost. The script says so, loudly, on
every run.

### Is it working?

```bash
tail -5 backups/backup.log              # what the last runs did
ls backups/LAST-RUN-FAILED              # exists ⇒ the last attempt did not finish
launchctl print gui/$UID/com.trademind.backup | head -20
launchctl kickstart -p gui/$UID/com.trademind.backup    # run it now
```

A failed run keeps its dump as `*.FAILED-VERIFICATION`, writes the marker file,
logs why, and raises a desktop notification. It does not delete anything.

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

# 4. Point the application at it — no rename, nothing destroyed.
#    In backend/.env:
#      DATABASE_URL=postgresql+asyncpg://trademind:trademind@localhost:5432/trademind_restored
```

Step 4 is deliberately not "rename the databases". Leaving the damaged one in
place costs 120 MB of disk and keeps every option open; renaming over it closes
them for good. Rename later, once the restored database has been running for a
while.

If the dump's revision is *older* than the code, run `alembic upgrade head`
against the restored database before step 4. If it is *newer*, check out the
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
docker compose exec -T db createdb -U trademind trademind_verify
cat backups/<file>.dump | docker compose exec -T db pg_restore -U trademind -d trademind_verify --no-owner
docker compose exec -T db psql -U trademind -d trademind_verify -tAc \
  "SELECT 'evidence='||(SELECT count(*) FROM evidence_items)||' usage='||(SELECT count(*) FROM usage_events)"
docker compose exec -T db dropdb -U trademind trademind_verify
```

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
