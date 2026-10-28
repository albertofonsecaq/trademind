"""
The chart a trader posted is kept, and shown wherever its evidence is cited — #108.

Vision read every image into a description and the picture itself was dropped,
so the knowledge base could only ever say what a chart showed, never show it.
"""
import base64
import uuid

import pytest
from fastapi import HTTPException

from app.api import evidence as evidence_api
from app.api import strategy_cards as cards_api
from app.connectors.base import RawMessage
from app.models.evidence_image import EvidenceImage
from app.models.evidence_item import EvidenceItem
from app.models.trade_idea import TradeIdea
from app.models.workspace_member import WorkspaceMember
from sqlalchemy import select

from app.services import ingestion_pipeline, reprocess_service, workspace_clone_service
from tests.conftest import FakeSession, make_card, make_source, make_user, make_workspace

# Any bytes do: nothing here decodes the picture, it is only kept and served.
CHART = b"\xff\xd8\xff\xe0 a chart, as Telegram sent it"


def _image_message(picture: bytes = CHART, mime: str = "image/png") -> RawMessage:
    return RawMessage(
        stable_id="telegram:@chan:7:image",
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        text=None,
        author="someone",
        channel="@chan",
        timestamp=None,
        source_type="telegram",
        content_type="image",
        metadata={
            "message_id": 7,
            "image_base64": base64.b64encode(picture).decode(),
            "image_mime": mime,
        },
    )


@pytest.fixture
def vision(offline_pipeline, monkeypatch):
    """Vision answers with the verdict a test chooses, at no cost."""
    verdict = {"is_on_topic": True}

    async def _extract(image_bytes, mime="image/jpeg"):
        return (
            {
                "is_on_topic": verdict["is_on_topic"],
                "needs_reprocessing": verdict["is_on_topic"] is None,
                "description": "EURUSD 4H, a breakout above 1.0850",
                "reason": "a forex chart",
            },
            {"input_tokens": 1200, "output_tokens": 300},
        )

    monkeypatch.setattr(ingestion_pipeline.vision_service, "extract_image", _extract)
    monkeypatch.setattr(ingestion_pipeline.vision_service, "vision_cost", lambda i, o: 0)
    return verdict


def _kept_images(session: FakeSession) -> list[EvidenceImage]:
    return [obj for obj in session.added if isinstance(obj, EvidenceImage)]


class TestAnImageMessage:
    async def test_keeps_the_picture_it_was_read_from(self, vision):
        session = FakeSession()

        item = await ingestion_pipeline.process_message(session, _image_message(), "forex")

        [image] = _kept_images(session)
        assert image.evidence_item_id == item.id
        assert image.workspace_id == item.workspace_id
        assert (image.data, image.mime_type) == (CHART, "image/png")

    async def test_judged_off_topic_keeps_no_picture(self, vision):
        # Nothing shows off-topic evidence, so its picture would only take space.
        vision["is_on_topic"] = False
        session = FakeSession()

        await ingestion_pipeline.process_message(session, _image_message(), "forex")

        assert _kept_images(session) == []

    async def test_whose_verdict_was_unreadable_keeps_its_picture(self, vision):
        # It is flagged for replay, which may well find it on topic.
        vision["is_on_topic"] = None
        session = FakeSession()

        await ingestion_pipeline.process_message(session, _image_message(), "forex")

        assert len(_kept_images(session)) == 1


# ── serving it ───────────────────────────────────────────────────────────────


async def _member(session, workspace):
    user = await make_user(session)
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="member"))
    await session.flush()
    return user


async def _evidence(session, workspace, *, picture: bytes | None = CHART):
    source = await make_source(session, workspace)
    item = EvidenceItem(
        id=uuid.uuid4(), workspace_id=workspace.id, source_config_id=source.id,
        stable_id=f"telegram:chan:{uuid.uuid4().hex[:8]}:image", source_type="telegram",
        type="image", content="EURUSD 4H, a breakout above 1.0850",
    )
    session.add(item)
    await session.flush()
    if picture is not None:
        session.add(EvidenceImage(
            evidence_item_id=item.id, workspace_id=workspace.id, mime_type="image/png", data=picture,
        ))
        await session.flush()
    return item


@pytest.mark.db
class TestServingThePicture:
    async def test_a_member_gets_it_as_it_was_posted(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        item = await _evidence(db_session, workspace)

        response = await evidence_api.get_evidence_image(workspace.id, item.id, user, db_session)

        assert response.body == CHART
        assert response.media_type == "image/png"

    async def test_evidence_with_no_picture_is_not_found(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        item = await _evidence(db_session, workspace, picture=None)

        with pytest.raises(HTTPException) as refused:
            await evidence_api.get_evidence_image(workspace.id, item.id, user, db_session)

        assert refused.value.status_code == 404

    async def test_another_workspaces_picture_is_not_found_through_your_own(self, db_session):
        # A member of one workspace naming the other's evidence: there is
        # nothing by that id in their workspace, and saying so is all they learn.
        mine, theirs = await make_workspace(db_session), await make_workspace(db_session)
        user = await _member(db_session, mine)
        their_item = await _evidence(db_session, theirs)

        with pytest.raises(HTTPException) as refused:
            await evidence_api.get_evidence_image(mine.id, their_item.id, user, db_session)

        assert refused.value.status_code == 404

    async def test_someone_outside_the_workspace_is_refused(self, db_session):
        workspace = await make_workspace(db_session)
        outsider = await make_user(db_session)
        item = await _evidence(db_session, workspace)

        with pytest.raises(HTTPException) as refused:
            await evidence_api.get_evidence_image(workspace.id, item.id, outsider, db_session)

        assert refused.value.status_code == 403


# ── where it is shown ────────────────────────────────────────────────────────


async def _idea(session, item):
    idea = TradeIdea(
        id=uuid.uuid4(), evidence_item_id=item.id, workspace_id=item.workspace_id,
        symbol="EURUSD", setup_type="breakout", action="long",
    )
    session.add(idea)
    await session.flush()
    return idea


@pytest.mark.db
class TestAStrategyCardsExamples:
    async def test_say_which_have_a_picture_to_show(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        pictured = await _idea(db_session, await _evidence(db_session, workspace))
        unpictured = await _idea(db_session, await _evidence(db_session, workspace, picture=None))
        card = await make_card(db_session, workspace)
        card.supporting_evidence = [str(pictured.id), str(unpictured.id)]
        await db_session.flush()

        detail = await cards_api.get_strategy_card_detail(workspace.id, card.id, 10, user, db_session)

        shown = {example.id: example for example in detail.examples}
        assert shown[pictured.id].evidence_id == pictured.evidence_item_id
        assert shown[pictured.id].has_image is True
        assert shown[unpictured.id].has_image is False


# ── when a flagged image is read again ───────────────────────────────────────


async def _flagged_image(session, source, *, picture: bytes | None):
    """An image whose vision verdict could not be read, waiting for the replay."""
    item = EvidenceItem(
        id=uuid.uuid4(), workspace_id=source.workspace_id, source_config_id=source.id,
        stable_id="telegram:chan:7:image", source_type="telegram", type="image",
        content="No trading content detected.", is_on_topic=None, needs_reprocessing=True,
    )
    session.add(item)
    await session.flush()
    if picture is not None:
        session.add(EvidenceImage(
            evidence_item_id=item.id, workspace_id=source.workspace_id, mime_type="image/png", data=picture,
        ))
        await session.flush()
    return item


async def _picture_of(session, item) -> EvidenceImage | None:
    return (await session.execute(
        select(EvidenceImage).where(EvidenceImage.evidence_item_id == item.id)
    )).scalar_one_or_none()


def _refetched(source) -> RawMessage:
    msg = _image_message()
    msg.stable_id = "telegram:chan:7:image"
    msg.source_config_id, msg.workspace_id = source.id, source.workspace_id
    return msg


@pytest.mark.db
class TestReplayingAFlaggedImage:
    async def test_found_on_topic_keeps_the_picture_it_was_fetched_with(self, db_session, channel, vision):
        # Flagged before #108, so it was stored with no picture; the replay
        # downloads the image again anyway, and now keeps it.
        source = await make_source(db_session)
        item = await _flagged_image(db_session, source, picture=None)
        channel.messages.append(_refetched(source))

        await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        picture = await _picture_of(db_session, item)
        assert (picture.data, picture.mime_type) == (CHART, "image/png")

    async def test_found_off_topic_no_longer_keeps_its_picture(self, db_session, channel, vision):
        vision["is_on_topic"] = False
        source = await make_source(db_session)
        item = await _flagged_image(db_session, source, picture=CHART)
        channel.messages.append(_refetched(source))

        await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        assert await _picture_of(db_session, item) is None


# ── when a workspace is cloned ───────────────────────────────────────────────


@pytest.mark.db
class TestACloneOfTheWorkspace:
    async def test_shows_the_same_pictures(self, db_session):
        workspace = await make_workspace(db_session)
        original = await _evidence(db_session, workspace)
        await _evidence(db_session, workspace, picture=None)
        receiver = await make_user(db_session)

        clone = await workspace_clone_service.clone_workspace(
            db_session, source_workspace_id=workspace.id, target_user_id=receiver.id, new_name=None,
        )
        await db_session.flush()

        copied = (await db_session.execute(
            select(EvidenceImage).where(EvidenceImage.workspace_id == clone.id)
        )).scalars().all()
        [picture] = copied
        copied_item = await db_session.get(EvidenceItem, picture.evidence_item_id)
        assert copied_item.workspace_id == clone.id
        assert copied_item.stable_id == original.stable_id
        assert (picture.data, picture.mime_type) == (CHART, "image/png")
