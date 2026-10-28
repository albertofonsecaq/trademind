"""
An answer on /ask shows the chart behind each cited source — #110.

Every source an answer cites has always been kept with its evidence's stable
id, which names one piece of evidence in the workspace. So whether a source has
a picture (#108) is looked up when the answer is served, fresh or read back,
and an answer asked before pictures were kept gets them too. Nothing stored
changes, and reading an answer back still runs no retrieval or synthesis.
"""
import uuid

import pytest

from app.api import ask as ask_api
from app.models.ask_record import AskRecord
from app.models.evidence_image import EvidenceImage
from app.models.evidence_item import EvidenceItem
from app.models.workspace_member import WorkspaceMember
from app.services import embedding_service, query_synthesis_service, retrieval_service
from tests.conftest import make_source, make_user, make_workspace

pytestmark = pytest.mark.db


async def _member(session, workspace):
    user = await make_user(session)
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="member"))
    await session.flush()
    user.preferred_language = "en"
    return user


async def _evidence(session, workspace, stable_id, *, picture: bool):
    source = await make_source(session, workspace)
    item = EvidenceItem(
        id=uuid.uuid4(), workspace_id=workspace.id, source_config_id=source.id,
        stable_id=stable_id, source_type="telegram", type="image" if picture else "text",
        content="EURUSD 4H, a breakout above 1.0850",
    )
    session.add(item)
    await session.flush()
    if picture:
        session.add(EvidenceImage(
            evidence_item_id=item.id, workspace_id=workspace.id, mime_type="image/png", data=b"a chart",
        ))
        await session.flush()
    return item


def _cited(*stable_ids) -> list[dict]:
    """Sources as synthesis returns them and an ask record keeps them."""
    return [
        {"index": i, "channel": "fx-desk", "author": None, "timestamp": None, "stable_id": sid, "symbol": "EURUSD"}
        for i, sid in enumerate(stable_ids, 1)
    ]


@pytest.fixture
def answers_citing(monkeypatch):
    """The ask endpoint answering with chosen sources, with every paid call stubbed."""
    cited: list[dict] = []

    async def _no_retrieval(db, **kwargs):
        return []

    async def _synthesize(query, results, language="en"):
        return "EURUSD broke out twice at this level [1]", cited, {"input_tokens": 1, "output_tokens": 1}

    async def _no_usage(**kwargs):
        pass

    monkeypatch.setattr(embedding_service, "embed_query", lambda text: [0.0, 1.0])
    monkeypatch.setattr(retrieval_service, "hybrid_search", _no_retrieval)
    monkeypatch.setattr(query_synthesis_service, "synthesize", _synthesize)
    monkeypatch.setattr(ask_api, "log_usage_event", _no_usage)

    def _cite(sources):
        cited[:] = sources
    return _cite


class TestAFreshAnswer:
    async def test_says_which_of_its_sources_have_a_picture(self, db_session, answers_citing):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        chart = await _evidence(db_session, workspace, "telegram:fx-desk:1:image", picture=True)
        await _evidence(db_session, workspace, "telegram:fx-desk:2", picture=False)
        answers_citing(_cited("telegram:fx-desk:1:image", "telegram:fx-desk:2"))

        response = await ask_api.ask(workspace.id, ask_api.AskRequest(query="EURUSD?"), user, db_session)

        first, second = response.sources
        assert (first["evidence_id"], first["has_image"]) == (str(chart.id), True)
        assert second["has_image"] is False

    async def test_is_kept_as_synthesized_without_what_was_looked_up(self, db_session, answers_citing):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        await _evidence(db_session, workspace, "telegram:fx-desk:1:image", picture=True)
        answers_citing(_cited("telegram:fx-desk:1:image"))

        response = await ask_api.ask(workspace.id, ask_api.AskRequest(query="EURUSD?"), user, db_session)

        record = await db_session.get(AskRecord, response.id)
        assert record.sources == _cited("telegram:fx-desk:1:image")


    async def test_citing_another_workspaces_evidence_shows_none_of_it(self, db_session, answers_citing):
        mine, theirs = await make_workspace(db_session), await make_workspace(db_session)
        user = await _member(db_session, mine)
        await _evidence(db_session, theirs, "telegram:fx-desk:1:image", picture=True)
        answers_citing(_cited("telegram:fx-desk:1:image"))

        response = await ask_api.ask(mine.id, ask_api.AskRequest(query="EURUSD?"), user, db_session)

        [source] = response.sources
        assert (source["evidence_id"], source["has_image"]) == (None, False)

    async def test_citing_one_piece_of_evidence_twice_shows_it_on_both(self, db_session, answers_citing):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        chart = await _evidence(db_session, workspace, "telegram:fx-desk:1:image", picture=True)
        answers_citing(_cited("telegram:fx-desk:1:image", "telegram:fx-desk:1:image"))

        response = await ask_api.ask(workspace.id, ask_api.AskRequest(query="EURUSD?"), user, db_session)

        assert [(s["index"], s["evidence_id"]) for s in response.sources] == [(1, str(chart.id)), (2, str(chart.id))]


class TestAnAnswerReadBack:
    async def test_asked_before_pictures_were_kept_shows_them_now(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        chart = await _evidence(db_session, workspace, "telegram:fx-desk:1:image", picture=True)
        record = AskRecord(
            workspace_id=workspace.id, user_id=user.id, question="EURUSD?", answer="It broke out [1]",
            sources=_cited("telegram:fx-desk:1:image"), language="en",
        )
        db_session.add(record)
        await db_session.flush()

        read = await ask_api.read_ask_record(workspace.id, record.id, user, db_session)

        [source] = read.sources
        assert (source["evidence_id"], source["has_image"]) == (str(chart.id), True)
        assert source["channel"] == "fx-desk"             # the rest, as it was kept

    async def test_a_source_from_another_workspace_or_gone_has_no_picture(self, db_session):
        mine, theirs = await make_workspace(db_session), await make_workspace(db_session)
        user = await _member(db_session, mine)
        await _evidence(db_session, theirs, "telegram:fx-desk:1:image", picture=True)
        record = AskRecord(
            workspace_id=mine.id, user_id=user.id, question="EURUSD?", answer="It broke out [1] [2]",
            sources=_cited("telegram:fx-desk:1:image", "telegram:fx-desk:9"), language="en",
        )
        db_session.add(record)
        await db_session.flush()

        read = await ask_api.read_ask_record(mine.id, record.id, user, db_session)

        assert [(s["evidence_id"], s["has_image"]) for s in read.sources] == [(None, False), (None, False)]
