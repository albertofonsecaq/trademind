# TradeMind

## Tests

`cd backend && pytest` runs offline — no database, network or API keys — and is
meant to be fast enough for every change. Tests that need a real database are
marked `db` and skipped unless `TEST_DATABASE_URL` is set; `backend/tests/conftest.py`
has the commands, including the `alembic upgrade head` those tests assume.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `albertofonsecaq/trademind`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using their default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
