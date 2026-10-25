"""
Tests for #72 — an answered question is kept.

The Ask view held its transcript in the browser alone, so a reload threw away
every answer the workspace had already paid to synthesize, and the only way
back to one was to ask again and pay again. An ask now leaves an **ask record**
behind: the question, the answer, and the sources that answer cited.

Three things about that record are worth a guard rather than a comment.

It is written in the request's own transaction, unlike the usage event beside
it. A usage event records that money was spent, which stays true however the
request ends, and is written on its own for exactly that reason (#27). An ask
record *is* the answer — kept for an answer the caller never received, it is a
lie the history would then show, and one nobody could tell from a real answer.

It says which language the answer is in. The endpoint already falls back to the
user's own language when asked for one it does not speak, so the record cannot
assume the language it was asked for.

And it is personal, like a plan item: being a member of the workspace a
question was asked in is not being allowed to read the question.

Offline like the rest of the suite: no database, no provider, no model.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import ask as ask_api
from app.models.ask_record import AskRecord
from app.models.workspace import Workspace
from app.services import embedding_service, query_synthesis_service, retrieval_service
from tests.conftest import FakeSession

_SOURCES = [
    {
        "index": 1, "channel": "fx-desk", "author": None, "timestamp": None,
        "stable_id": "telegram:fx-desk:4120", "symbol": "EURUSD",
    }
]


def _workspace() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), monthly_budget_cap=None)


def _user(preferred_language: str = "en") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), preferred_language=preferred_language)


def _records(db: FakeSession) -> list[AskRecord]:
    return [obj for obj in db.added if isinstance(obj, AskRecord)]


@pytest.fixture
def forbid_spending(monkeypatch):
    """
    Everything that costs money, wired to fail the test if it is reached.

    Reopening an ask record is the one read in this API that must not touch a
    provider. The answer was paid for once, when it was asked; charging for the
    same sentence twice is the thing keeping it was meant to avoid, and a
    silently re-synthesized answer is not even the same answer.
    """
    def refuse_sync(*_args, **_kwargs):
        raise AssertionError("a paid call was made while reading a stored answer")

    async def refuse_async(*_args, **_kwargs):
        raise AssertionError("a paid call was made while reading a stored answer")

    monkeypatch.setattr(embedding_service, "embed_query", refuse_sync)
    monkeypatch.setattr(retrieval_service, "hybrid_search", refuse_async)
    monkeypatch.setattr(query_synthesis_service, "synthesize", refuse_async)
    monkeypatch.setattr(ask_api, "log_usage_event", refuse_async)


@pytest.fixture
def ask(monkeypatch):
    """
    The ask endpoint with every paid call stubbed out.

    `await ask.run("question")` answers one and returns the response alongside
    the session, the user and the workspace it used, so a test can assert on
    both the answer and the writes behind it. `ask.fails = <error>` makes
    synthesis raise instead; `ask.usage_events` is what was recorded.
    """
    usage_events: list[dict] = []
    state = SimpleNamespace(
        answer="EURUSD broke out twice at this level [1]",
        sources=_SOURCES,
        usage={"input_tokens": 900, "output_tokens": 120},
        fails=None,
        usage_events=usage_events,
        db=None,
        answered=None,
    )

    monkeypatch.setattr(embedding_service, "embed_query", lambda text: [0.0, 1.0])

    async def _no_retrieval(db, **kwargs):
        return []

    monkeypatch.setattr(retrieval_service, "hybrid_search", _no_retrieval)

    async def _synthesize(query, results, language="en"):
        state.answered = query                  # the string the model was given
        if state.fails:
            raise state.fails
        return state.answer, state.sources, state.usage

    monkeypatch.setattr(query_synthesis_service, "synthesize", _synthesize)

    async def _log_usage_event(**kwargs):
        # How many times the request had committed when the call was recorded.
        # #27's ordering is the subject of a test below, and it is only visible
        # from in here.
        usage_events.append({**kwargs, "commits_before": state.db.commits})

    monkeypatch.setattr(ask_api, "log_usage_event", _log_usage_event)

    async def run(question="what happened to EURUSD?", *, language="en", user=None, db=None, workspace=None):
        workspace = workspace or _workspace()
        user = user or _user()
        state.db = db if db is not None else FakeSession(rows={Workspace: workspace})
        response = await ask_api.ask(
            workspace_id=workspace.id,
            payload=ask_api.AskRequest(query=question, language=language),
            current_user=user,
            db=state.db,
        )
        return SimpleNamespace(response=response, db=state.db, user=user, workspace=workspace)

    state.run = run
    return state


# ── what is kept ──────────────────────────────────────────────────────────────


class TestAnAnsweredQuestionIsKept:
    async def test_one_record_carries_the_question_the_answer_and_its_sources(self, ask):
        result = await ask.run("what happened to EURUSD?")

        record, = _records(result.db)
        assert record.question == "what happened to EURUSD?"
        assert record.answer == "EURUSD broke out twice at this level [1]"
        assert record.sources == _SOURCES
        assert record.user_id == result.user.id
        assert record.workspace_id == result.workspace.id

    async def test_the_question_is_kept_as_asked_not_as_padded(self, ask):
        """
        The whitespace is the client's, not the question. Read once at the top
        of the handler, so what was embedded, retrieved against, answered and
        kept are all the same string.
        """
        result = await ask.run("   EURUSD breakouts?  ")

        record, = _records(result.db)
        assert record.question == "EURUSD breakouts?"
        assert ask.answered == "EURUSD breakouts?"

    async def test_an_answer_with_no_sources_is_still_an_answer(self, ask):
        """
        "Nothing relevant found" is what the synthesis returns for an empty
        knowledge base, and it is worth keeping: it answers the question of
        whether asking again would help.
        """
        ask.sources = []

        result = await ask.run()

        record, = _records(result.db)
        assert record.sources == []

    async def test_the_record_names_the_language_it_was_answered_in(self, ask):
        result = await ask.run(language="es")

        record, = _records(result.db)
        assert record.language == "es"

    async def test_a_language_the_endpoint_does_not_speak_is_not_the_one_recorded(self, ask):
        """
        Asked for French, the endpoint answers in the user's own language. A
        record that stored "fr" would render Spanish prose under a French label.
        """
        result = await ask.run(language="fr", user=_user("es"))

        record, = _records(result.db)
        assert record.language == "es"

    async def test_the_response_names_the_record_it_was_kept_as(self, ask):
        """
        A fresh answer and one read back out of the history are the same thing
        to a client, which it cannot treat them as while only one has an
        identity.
        """
        result = await ask.run()

        record, = _records(result.db)
        assert result.response.id == record.id
        assert result.response.created_at == record.created_at
        assert result.response.created_at.tzinfo is not None        # ADR 1


# ── whose transaction it belongs to ───────────────────────────────────────────


class TestTheRecordBelongsToTheRequestsTransaction:
    async def test_the_record_is_added_before_the_request_commits(self, ask):
        """
        Verified to bite: move the `db.add(record)` below `await db.commit()`
        and this fails with ["commit", "add"] — a row written after the only
        commit the request makes, which is to say never written at all.
        """
        result = await ask.run()

        assert result.db.writes_and_commits(AskRecord) == ["add", "commit"]

    async def test_nothing_is_kept_when_synthesis_never_answers(self, ask):
        """
        Verified to bite: write the record before the synthesis call and this
        fails. The history would otherwise show an answer the caller never got.
        """
        workspace = _workspace()
        db = FakeSession(rows={Workspace: workspace})
        ask.fails = RuntimeError("the provider hung up")

        with pytest.raises(RuntimeError):
            await ask.run(db=db, workspace=workspace)

        assert _records(db) == []
        assert db.commits == 0

    async def test_the_call_is_still_recorded_after_the_commit(self, ask):
        """
        #27's ordering, unchanged by keeping the answer: the subscription's
        running overage is committed before the call is recorded, so an overage
        row cannot outlive a request that failed.
        """
        await ask.run()

        event, = ask.usage_events
        assert event["task_type"] == "query_synthesis"
        assert (event["input_units"], event["output_units"]) == (900, 120)
        assert event["commits_before"] == 1


    async def test_the_call_is_recorded_even_when_the_answer_cannot_be_stored(self, ask):
        """
        Keeping the answer in this transaction gives the transaction one more
        way to fail, and the tokens were spent before the record was written.
        So the spend is recorded whatever the commit does — under-metered in
        that case, because the subscription's running overage went back with
        everything else, but not lost, which is #27's whole point.

        Verified to bite: drop the `finally` around the commit and this fails
        with nothing recorded at all.
        """
        workspace = _workspace()
        db = FakeSession(rows={Workspace: workspace})

        async def commit_that_fails():
            raise RuntimeError("ask_records violates a constraint")

        db.commit = commit_that_fails
        with pytest.raises(RuntimeError):
            await ask.run(db=db, workspace=workspace)

        event, = ask.usage_events
        assert event["task_type"] == "query_synthesis"
        assert (event["input_units"], event["output_units"]) == (900, 120)


# ── whose records they are ────────────────────────────────────────────────────


class TestRecordsAreOnlyEverTheCallersOwn:
    @staticmethod
    async def _list(*, user, workspace, membership: bool = True):
        """The list endpoint, with the statement it ran for the records."""
        seen: dict = {}

        def capture(statement):
            seen["statement"] = statement
            return []

        db = FakeSession(
            rows={Workspace: workspace, AskRecord: capture}, membership=membership
        )
        await ask_api.list_ask_records(
            workspace_id=workspace.id, limit=50, current_user=user, db=db
        )
        return seen.get("statement"), db

    async def test_the_query_is_scoped_to_the_caller_and_the_workspace(self):
        """
        An ask record is a personal layer. A member of the same workspace
        asking for the history gets their own, never the one next to it.

        Verified to bite: drop the `user_id` filter and this fails naming it.
        """
        user, workspace = _user(), _workspace()

        statement, _ = await self._list(user=user, workspace=workspace)

        bound = set(statement.compile().params.values())
        assert user.id in bound
        assert workspace.id in bound

    async def test_the_newest_record_comes_first_and_not_the_whole_table(self):
        user, workspace = _user(), _workspace()

        statement, _ = await self._list(user=user, workspace=workspace)

        sql = str(statement).lower()
        assert "order by ask_records.created_at desc" in sql
        assert "limit" in sql

    async def test_a_non_member_is_refused_before_anything_is_read(self):
        user, workspace = _user(), _workspace()

        with pytest.raises(HTTPException) as refused:
            await self._list(user=user, workspace=workspace, membership=False)

        assert refused.value.status_code == 403


# ── reading one back ──────────────────────────────────────────────────────────


def _stored(user, workspace, **over) -> AskRecord:
    fields = {
        "id": uuid.uuid4(),
        "workspace_id": workspace.id,
        "user_id": user.id,
        "question": "what happened to EURUSD?",
        "answer": "EURUSD broke out twice at this level [1]",
        "sources": _SOURCES,
        "language": "en",
    }
    return AskRecord(**{**fields, **over})


class TestReopeningOneSpendsNothing:
    async def test_the_stored_answer_and_its_sources_come_back_as_they_were(self, forbid_spending):
        """
        Reopening is a read. Every paid call is wired to fail the test, so
        anything that reached a provider — or recorded spend — fails here
        rather than on someone's bill.
        """
        user, workspace = _user(), _workspace()
        record = _stored(user, workspace)
        db = FakeSession(rows={Workspace: workspace, AskRecord: record})

        found = await ask_api.read_ask_record(
            workspace_id=workspace.id, record_id=record.id, current_user=user, db=db
        )

        assert found.answer == "EURUSD broke out twice at this level [1]"
        assert found.sources == _SOURCES
        assert db.added == [] and db.commits == 0
        assert not db.touched("usage_events")

    async def test_another_users_record_is_not_found_rather_than_refused(self, forbid_spending):
        """
        An ask record is a personal layer, so whether this id exists at all is
        not something a caller who does not own it gets to learn. 403 would
        answer that question; 404 does not.
        """
        user, workspace = _user(), _workspace()
        db = FakeSession(rows={Workspace: workspace, AskRecord: None})

        with pytest.raises(HTTPException) as refused:
            await ask_api.read_ask_record(
                workspace_id=workspace.id, record_id=uuid.uuid4(), current_user=user, db=db
            )

        assert refused.value.status_code == 404

    async def test_the_lookup_is_scoped_to_the_caller_and_the_workspace(self, forbid_spending):
        """
        Verified to bite: drop the `user_id` filter and this fails. The 404
        above would still pass on a fake, because what makes it a 404 in
        production is the query finding nothing.
        """
        user, workspace = _user(), _workspace()
        record_id = uuid.uuid4()
        seen: dict = {}

        def capture(statement):
            seen["statement"] = statement
            return None

        db = FakeSession(rows={Workspace: workspace, AskRecord: capture})

        with pytest.raises(HTTPException):
            await ask_api.read_ask_record(
                workspace_id=workspace.id, record_id=record_id, current_user=user, db=db
            )

        bound = set(seen["statement"].compile().params.values())
        assert {user.id, workspace.id, record_id} <= bound


class TestTheListIsAListNotEveryAnswer:
    async def test_it_does_not_read_the_answers_out_of_the_database(self):
        """
        The list says which questions were asked and when; the answers are what
        makes the table big, and only the one that gets opened is needed.

        Verified to bite: select the whole AskRecord and this fails naming the
        answer column.
        """
        user, workspace = _user(), _workspace()
        seen: dict = {}

        def capture(statement):
            seen["statement"] = statement
            return []

        db = FakeSession(rows={Workspace: workspace, AskRecord: capture})
        await ask_api.list_ask_records(
            workspace_id=workspace.id, limit=50, current_user=user, db=db
        )

        sql = str(seen["statement"]).lower()
        assert "ask_records.question" in sql and "ask_records.created_at" in sql
        assert "ask_records.answer" not in sql
        assert "ask_records.sources" not in sql

    async def test_a_user_with_no_records_gets_an_empty_list(self):
        user, workspace = _user(), _workspace()
        db = FakeSession(rows={Workspace: workspace, AskRecord: []})

        assert await ask_api.list_ask_records(
            workspace_id=workspace.id, limit=50, current_user=user, db=db
        ) == []
