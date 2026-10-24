# TradeMind

## Tests

`cd backend && pytest` runs offline — no database, network or API keys — and is
meant to be fast enough for every change. Tests that need a real database are
marked `db` and skipped unless `TEST_DATABASE_URL` is set; `backend/tests/conftest.py`
has the commands, including the `alembic upgrade head` those tests assume.

`.github/workflows/ci.yml` runs all of it on every push and pull request: the
offline suite in two time zones, the full suite against a Postgres service
migrated from empty, and `npm run build` for the frontend's only typecheck. If a
check only passes locally, it does not pass.

## Database roles

Three roles, by job: `trademind_owner` owns everything and is used by `alembic`
alone, `trademind_app` reads and writes rows and owns nothing, `trademind_backup`
reads everything and can create its own databases. `backend/scripts/setup_db_roles.py`
produces them and is safe to run repeatedly — run it against any database you
restore, or the application cannot read it. See `docs/adr/0004-the-application-owns-nothing-in-its-own-database.md`.

The application's credentials cannot drop a table or the database. That is
deliberate and load-bearing; `backend/tests/test_database_privileges.py` fails if
it stops being true.

`--show` reports who owns what, and `--revert --yes` hands everything back to
the bootstrap superuser — the way out if the roles are ever what is in the way.
`backups/README.md` has both, next to the restore procedure.

**Anything destructive runs against a disposable copy, never the live database.**
`backend/scripts/scratch_db.py` makes one from the newest verified dump and
prints how to connect to it and how to delete it. Dropping a table to check a
permission, exercising a guard, trying a downgrade: there, not here.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `albertofonsecaq/trademind`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using their default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

`docs/adr/README.md` says how an ADR here is written; read the ones touching the area you are changing before changing it.
