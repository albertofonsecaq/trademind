"""
Most tests here are offline by design: no database, no network, no API keys.
Everything they touch is either pure or stubbed, so they stay fast enough to
run on every change.

    pip install -r requirements-dev.txt && pytest

A few things cannot be checked that way. How Postgres stores a timestamp, for
instance, is a property of the driver and the column — fakes cannot see it, and
that is exactly where #8 hides. Those tests are marked `db` and are **skipped
unless a database is configured for tests**, so the default run stays offline:

    docker compose up -d db
    cd backend && python scripts/setup_db_roles.py        # once, as the superuser
    DATABASE_URL_SYNC=postgresql+psycopg2://trademind_owner:trademind@localhost:5432/trademind \
        alembic upgrade head
    TEST_DATABASE_URL=postgresql+asyncpg://trademind_app:trademind@localhost:5432/trademind pytest

The `alembic upgrade head` matters: these tests read the real schema, so a
database on an older revision fails in ways that look like code bugs. The
fixture checks the revision and says so rather than letting that happen.

The variable is deliberately its own, not `DATABASE_URL`: that one has a
default pointing at localhost, so reading it would mean a contributor with
Postgres running locally had tests writing to their development data without
ever asking for it.

Connect it as `trademind_app`, the role the application itself uses. The
privilege tests assert what that role cannot do (#59), so running them as the
owner or the superuser would pass them for the wrong reason — or rather, would
fail them, which is the point.

Whatever a `db` test writes is rolled back when it finishes, so a run leaves no
rows behind — including when the test fails, when the failure is a statement
that aborted the transaction, and when the test commits. That last one is why
the session runs inside an outer transaction it cannot see: a `commit()` in a
test releases a savepoint, and the outer transaction is still thrown away.
"""
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND = Path(__file__).resolve().parents[1]

# Let `import app...` work whether pytest runs from backend/ or from the repo root.
sys.path.insert(0, str(BACKEND))


def _expected_revision() -> str | None:
    """The head revision of the migrations in this working tree."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


async def _assert_schema_is_current(session) -> None:
    """
    Fail loudly on a stale test database.

    These tests assert against the real schema, so one on an older revision
    produces errors about missing columns that read as application bugs. This
    turns that into one sentence naming the fix.
    """
    expected = _expected_revision()
    if not expected:
        return
    actual = (
        await session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalars().first()
    if actual != expected:
        pytest.fail(
            f"test database is at migration {actual or 'nothing'}, "
            f"but this tree's head is {expected} — run `alembic upgrade head` against it",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _restore_the_machines_time_zone():
    """
    Put the process's time zone back after a test moves it.

    Several tests prove that an answer does not depend on where the server runs
    by setting TZ and calling `time.tzset()`. `monkeypatch.setenv` restores the
    variable but cannot undo `tzset()`, so without this the whole session
    carried on in whichever zone the last such test chose — and a later test
    reading local time would pass or fail depending on collection order.
    """
    before = os.environ.get("TZ")
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


@pytest.fixture
async def db_session():
    """
    A session against a real database, in a transaction that is always rolled
    back. Skips when no test database is configured.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run database-backed tests")

    # An engine per test, with no pooling: the asyncio loop is function-scoped
    # (see pytest.ini), so a pooled connection kept between tests would be bound
    # to a loop that has already closed. Do not "optimize" this to session scope
    # without changing that too.
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            session_factory = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                # The session's own commits become savepoint releases inside the
                # transaction above, so even a test that commits deliberately
                # leaves nothing behind once that transaction is discarded.
                join_transaction_mode="create_savepoint",
            )
            async with session_factory() as session:
                await _assert_schema_is_current(session)
                try:
                    yield session
                finally:
                    await session.rollback()
            # Unconditional, and safe on a transaction a failed statement has
            # already aborted — which is a case these tests deliberately provoke.
            await outer.rollback()
    finally:
        await engine.dispose()


# ── offline session doubles ───────────────────────────────────────────────────
#
# Six test files used to carry their own copy of these, and most answered a
# query by *call order*: a queue of rows popped per execute(). That is wrong the
# moment production code adds a query, and it went wrong three times — #13 added
# one workspace lookup and three tests in another file had to have a row
# inserted at the right index, #9 added a MAX() and the #2 regression tests
# failed on a missing scalar(), and a poll test in #13 passed only because every
# source happened to clear the earlier gates.
#
# So these resolve a query by what it asks for. Register a value per model and
# the order of queries stops mattering; a lookup by id can register a mapping
# and gets matched against the statement's own parameters.


class FakeResult:
    """Serves every access shape the code under test uses on a result."""

    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else [self._value]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def scalar_one_or_none(self):
        return self._value[0] if isinstance(self._value, list) and self._value else (
            None if isinstance(self._value, list) else self._value
        )

    def scalar_one(self):
        return self.scalar_one_or_none()

    def scalar(self):
        return self.scalar_one_or_none()

    @property
    def rowcount(self):
        """
        How many rows a DELETE or UPDATE claimed to touch.

        Registered as a plain int, because that is all a caller of one can read
        off the result: `delete(...)` returns no rows, so the only question a
        bulk delete can answer is how many there were (#74).
        """
        return self._value if isinstance(self._value, int) else 0

    def one(self):
        return self._value


def _apply_column_defaults(obj) -> None:
    """
    Set the Python-side column defaults a flush would assign.

    A handler may read a value back off a row it has just added rather than
    waiting for the row to come back out of the database — the ask endpoint
    names the record it stored an answer as (#72). Those values come from
    `default=` on the mapping, which a real flush evaluates; a fake that only
    counted the flush left them None and the handler failed for a reason that
    exists nowhere but the test.
    """
    try:
        mapper = sa_inspect(type(obj))
    except Exception:
        return                                  # not a mapped object
    for column in mapper.columns:
        default = column.default
        if default is None or not (default.is_scalar or default.is_callable):
            continue                            # server-side, or none at all
        attribute = mapper.get_property_by_column(column).key
        if getattr(obj, attribute, None) is not None:
            continue                            # the caller said what it wanted
        try:
            value = default.arg(None) if default.is_callable else default.arg
        except TypeError:
            continue    # a default that wants the real execution context, which
                        # there is none of here — leave it to the caller to set
        setattr(obj, attribute, value)


class FakeSavepoint:
    """
    Records that a savepoint was entered and how it ended, which is what #2's
    fix turns on: a failed message must undo its own savepoint and leave the
    shared session usable. "enter" is recorded as well as the outcome, so a test
    can tell a savepoint that was opened and never closed from one that never
    opened at all.
    """

    def __init__(self, recorder: list):
        self._recorder = recorder

    async def __aenter__(self):
        self._recorder.append("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._recorder.append("rollback" if exc_type else "release")
        return False        # never swallow: the caller's except clauses decide


class FakeSession:
    """
    An AsyncSession stand-in that answers by statement, and records what it was
    asked to do.

    Register what a query should find, by model:

        db = FakeSession(rows={SourceConfig: [source], Workspace: workspace})
        db = FakeSession(rows={Workspace: {ws.id: ws, other.id: other}})

    A dict value is matched against the statement's bound parameters, so a
    lookup by id finds the right row however many other queries ran first. A
    callable value is handed the statement. Anything unregistered answers None,
    which is what a real session does for a row that is not there.
    """

    def __init__(self, rows: dict | None = None, *, membership: bool = True):
        self._rows = dict(rows or {})
        # Most API handlers check workspace membership first; `membership=False`
        # makes that check fail, for the tests that want a 403.
        self._membership = membership
        self.statements: list[str] = []
        self.entities: list = []
        self.savepoints: list[str] = []
        self.added: list = []
        self.deleted: list = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        # Which keys a by-id lookup matched, in order. A test asserting that a
        # source was turned back at a particular gate needs to know the gate was
        # reached at all — not just that nothing was fetched (#13).
        self.lookup_keys: list = []
        # What was done, in order, as (operation, object). The counters above
        # cannot answer *when* a write happened relative to a commit, and that
        # is the question for anything written in the caller's transaction on
        # purpose: an ask record is added before the request commits, unlike the
        # usage event beside it (#72, #27).
        self.timeline: list[tuple[str, object]] = []

    # the session is also used as a context manager by background runners
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def register(self, model, value):
        self._rows[model] = value
        return self

    async def execute(self, statement=None, *_args, **_kwargs):
        self.statements.append(str(statement).lower())
        entity = self._entity_of(statement)
        self.entities.append(entity)
        value = self._rows.get(entity, _MISSING)

        if value is _MISSING:
            from app.models.workspace_member import WorkspaceMember

            # An unregistered membership check passes by default, so a test only
            # says so when it cares.
            if entity is WorkspaceMember:
                return FakeResult(object() if self._membership else None)
            return FakeResult(None)
        if callable(value):
            return FakeResult(value(statement))
        if isinstance(value, dict):
            key, found = self._by_parameter(statement, value)
            if key is not None:
                self.lookup_keys.append(key)
            return FakeResult(found)
        return FakeResult(value)

    @staticmethod
    def _entity_of(statement):
        try:
            description = statement.column_descriptions[0]
        except (AttributeError, IndexError):
            # A DELETE or an UPDATE has no columns to describe; it names one
            # entity instead, which is what a test registering rows for one
            # means by it (#74).
            description = getattr(statement, "entity_description", None) or {}
        return description.get("entity") or description.get("type")

    @staticmethod
    def _by_parameter(statement, mapping: dict):
        """The registered row whose key appears in the statement's parameters."""
        try:
            parameters = statement.compile().params.values()
        except Exception:
            return None, None
        for value in parameters:
            if value in mapping:
                return value, mapping[value]
        return None, None

    def begin_nested(self):
        return FakeSavepoint(self.savepoints)

    def add(self, obj):
        self.added.append(obj)
        self.timeline.append(("add", obj))

    def add_all(self, objects):
        self.added.extend(objects)
        self.timeline.extend(("add", obj) for obj in objects)

    async def delete(self, obj):
        self.deleted.append(obj)
        self.timeline.append(("delete", obj))

    async def flush(self):
        self.flushes += 1
        for obj in self.added:
            _apply_column_defaults(obj)

    async def commit(self):
        self.commits += 1
        self.timeline.append(("commit", None))

    async def rollback(self):
        self.rollbacks += 1
        self.timeline.append(("rollback", None))

    async def refresh(self, _obj):
        pass

    # ── what a test can ask about what happened ──────────────────────────────

    def touched(self, table: str) -> bool:
        """True if any statement mentioned that table, read or write."""
        return any(table in statement for statement in self.statements)

    def wrote_anything_destructive(self) -> bool:
        """A bulk delete or update, which .deleted would never see."""
        return any(
            verb in statement
            for statement in self.statements
            for verb in ("delete", "update")
        )

    def lookups_of(self, model) -> list:
        """Which rows of this model were looked for, in order."""
        return [entity for entity in self.entities if entity is model]

    def writes_and_commits(self, model) -> list[str]:
        """
        Every write of this model and every commit, in the order they happened.

        `["add", "commit"]` is a row written inside the transaction that then
        commits. `["commit", "add"]` is one added after the only commit the
        caller makes, which is to say never committed at all.
        """
        return [
            operation
            for operation, obj in self.timeline
            if operation == "commit" or isinstance(obj, model)
        ]


class _Missing:
    pass


_MISSING = _Missing()


# ── platform failures the tests simulate ─────────────────────────────────────
#
# Telethon's RPC errors take a `request=` naming what caused them, which these
# tests have no use for but cannot omit. Two files build them, so the shape
# lives here rather than being copied with its explanation.


class FakeRpcRequest:
    """Stands in for the Telethon request an RPCError names as its cause."""


def rpc_error(error_class, **kwargs):
    """
    One of Telethon's RPC errors, ready to raise.

    The class is the test's subject — which way Telegram ended the session, or
    which failure says nothing about it — so it is passed in rather than wrapped
    one function per error.
    """
    return error_class(request=FakeRpcRequest(), **kwargs)


# ── the recurring poll ───────────────────────────────────────────────────────


@pytest.fixture
def poll(monkeypatch):
    """
    `_poll_all_sources` against a fake session, with the billing gates open.

    `await poll.run(a, b)` polls those sources in one workspace that is not
    itself paused, whose id is `poll.workspace_id`. Tests that need several
    workspaces, or a paused one, pass them: `poll.run(*sources, workspaces=[…])`.

    Returns what the tick fetched, which workspaces were costed, and the session
    it used, so a test can assert both the fetching and the reads behind it.

    Three test files had their own copy of this, and all three stubbed the fetch
    as returning a bare int — see below for why that mattered.
    """
    from app.models.source_config import SourceConfig
    from app.models.workspace import Workspace
    from app.services import billing_service, ingestion_pipeline
    from app.workers import poller

    default_workspace = SimpleNamespace(
        id=uuid.uuid4(), name="a workspace", topic_scope="trading",
        ingestion_paused=False, payment_enabled=False,
    )
    fetched: list[str] = []
    gate_checks: list = []

    async def _fetch(db, *, source, workspace):
        fetched.append(source.identifier)
        # The real one returns a FetchOutcome and the poller reads .ingested off
        # it. Every copy of this fixture returned 1, so each fetch the poll
        # *allowed* raised AttributeError straight into the poller's except
        # clause: the assertions still held, because the identifier is recorded
        # first, and nothing after the call was ever exercised (found reviewing
        # #49).
        return ingestion_pipeline.FetchOutcome(ingested=1, failed=0)

    async def _not_lapsed(db, workspace_id):
        # Recorded, so a test can tell a source turned back at an earlier gate
        # from one that reached the billing checks and passed them (#13).
        gate_checks.append(workspace_id)
        return False

    async def _not_exhausted(db, workspace_id):
        return False

    # Both are imported inside the function under test, so patching the module
    # here is what the call will see.
    monkeypatch.setattr(ingestion_pipeline, "run_fetch_pipeline", _fetch)
    monkeypatch.setattr(billing_service, "is_payment_lapsed", _not_lapsed)
    monkeypatch.setattr(billing_service, "is_budget_exhausted", _not_exhausted)

    async def run(*sources, workspaces=None):
        workspaces = list(workspaces) if workspaces else [default_workspace]
        session = FakeSession(rows={
            SourceConfig: list(sources),
            Workspace: {ws.id: ws for ws in workspaces},
        })
        monkeypatch.setattr(poller, "AsyncSessionLocal", lambda: session)
        await poller._poll_all_sources()
        return SimpleNamespace(fetched=fetched, gate_checks=gate_checks, session=session)

    return SimpleNamespace(run=run, workspace_id=default_workspace.id)


# ── rows for the database-backed tests ───────────────────────────────────────
#
# Built in dependency order and flushed in turn: several of these models carry
# foreign keys to each other with no matching relationship, so the unit of work
# has no dependency to order the inserts by and would otherwise send a child
# first. Two test files had their own copies of this, including that reasoning.


async def make_user(session):
    from app.models.user import User

    user = User(
        id=uuid.uuid4(), email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    session.add(user)
    await session.flush()
    return user


async def make_workspace(session, user=None):
    from app.models.workspace import Workspace

    user = user or await make_user(session)
    workspace = Workspace(
        id=uuid.uuid4(), name="a workspace", owner_user_id=user.id, topic_scope="forex",
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def make_source(session, workspace=None):
    from app.models.source_config import SourceConfig

    workspace = workspace or await make_workspace(session)
    source = SourceConfig(
        id=uuid.uuid4(), workspace_id=workspace.id, source_type="telegram",
        identifier="chan", created_by_user_id=workspace.owner_user_id,
    )
    session.add(source)
    await session.flush()
    return source


def make_job(source, starting):
    """Unflushed, so a test can assert on what the write does."""
    from app.models.backfill_job import BackfillJob

    return BackfillJob(
        id=uuid.uuid4(), source_config_id=source.id, workspace_id=source.workspace_id,
        date_range_start=starting, triggered_by_user_id=source.created_by_user_id,
    )


async def make_card(session, workspace=None):
    from app.models.strategy_card import StrategyCard

    workspace = workspace or await make_workspace(session)
    card = StrategyCard(
        id=uuid.uuid4(), workspace_id=workspace.id, setup_type="breakout",
        symbol_scope="EURUSD", description_en="a breakout", description_es="una ruptura",
        flowchart_spec={}, supporting_evidence=[], sample_size=0, source_count=0,
        version=1, version_history=[],
    )
    session.add(card)
    await session.flush()
    return card


# ── the ingestion pipeline, offline ──────────────────────────────────────────


@pytest.fixture
def offline_pipeline(monkeypatch):
    """
    `process_message` with every paid call it can make answered locally: the
    relevance check says on topic, distillation does nothing, the language is
    English, and usage events land in the returned session's `added`.

    Two test files had their own copy of this (found reviewing #94).
    """
    from app.services import ingestion_pipeline, usage_service

    usage_log = FakeSession()
    monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: usage_log)

    async def _on_topic(text, topic_scope):
        return True, "about a currency pair", {"input_tokens": 10, "output_tokens": 2}

    async def _distill(db, item, msg, text, lang):
        return None

    monkeypatch.setattr(ingestion_pipeline.relevance_service, "is_on_topic", _on_topic)
    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
    monkeypatch.setattr(ingestion_pipeline, "_detect_lang", lambda text: "en")
    return usage_log
