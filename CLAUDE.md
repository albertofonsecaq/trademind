# TradeMind

## Agent skills

### Issue tracker

Issues live as GitHub issues in `albertofonsecaq/trademind`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using their default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

Decisions recorded so far:

- [ADR 1: timestamps are timezone-aware UTC](docs/adr/0001-timestamps-are-aware-utc.md) — and connectors are the only place a naive value is correct.
