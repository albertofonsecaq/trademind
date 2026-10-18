# Database backups

This holds dumps of the TradeMind database. **The dumps themselves are
gitignored** — they are large, and a dump of real data does not belong in
version control. Only this file is tracked.

What is in here is not reproducible. The database is months of paid ingestion:
evidence that cost relevance checks, images that cost vision calls, transcripts
that cost Whisper. Re-running the pipeline would re-pay for all of it, and some
of the Telegram history behind it may no longer be fetchable at all.

## Taking one

```bash
docker compose up -d db
docker compose exec -T db pg_dump -U trademind -d trademind -Fc \
  > backups/trademind-$(date +%Y%m%d)-alembic$(
      docker compose exec -T db psql -U trademind -d trademind -tAc \
        'SELECT version_num FROM alembic_version' | tr -d '[:space:]'
    ).dump
```

The migration revision is in the filename on purpose: a dump only restores
cleanly against code that expects its schema, so the revision is the thing you
need to know before trusting one.

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

| File | Taken | Revision | Verified | Why |
| --- | --- | --- | --- | --- |
| `trademind-20261018-alembic016.dump` | 2026-10-18 | 016 | restored to a scratch database, 127 objects, 8,582 evidence / 1,274 ideas / 11,630 usage events | before #34, the first batch of #8 to change how stored timestamps are read |

## Before changing how timestamps are stored or read

#8's batches (#34–#37) change the meaning of values already in these tables,
not just the code around them. Take a dump and verify it first — #34's own
acceptance criteria say so, and the same applies to #35 and #36.
