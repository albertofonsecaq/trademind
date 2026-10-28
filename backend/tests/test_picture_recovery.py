"""
The pictures of image evidence ingested before #108 are fetched again — #111.

Until #108 an image was read by vision and its picture dropped, so months of
image evidence have a description and nothing to show. Each message is fetched
again from the platform by its own id, as the replay does (#104), and only its
picture is kept: the evidence's text, verdict and trade ideas stay exactly as
they are, and no model is asked anything.

A dry run only counts, with no platform call at all. The real run is the
owner's to make against the live database; these run on a test database.
"""
import base64
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from telethon.errors import AuthKeyUnregisteredError, ChannelPrivateError, FloodWaitError

from app.connectors.base import RawMessage
from app.models.evidence_image import EvidenceImage
from app.models.evidence_item import EvidenceItem
from app.services import ingestion_pipeline, picture_recovery, usage_service, vision_service
from tests.conftest import make_source, reread, rpc_error

pytestmark = pytest.mark.db

_AT = datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc)
CHART = b"\xff\xd8\xff\xe0 a chart, as Telegram sent it"


async def _image_source(session):
    """A Telegram source that fetches images, as one that has image evidence did."""
    source = await make_source(session)
    source.content_filters = {"text": True, "image": True, "video": False, "url": False}
    await session.flush()
    return source


async def _image_evidence(session, source, msg_id: int, *, on_topic=True, picture: bool = False):
    item = EvidenceItem(
        id=uuid.uuid4(), workspace_id=source.workspace_id, source_config_id=source.id,
        stable_id=f"telegram:chan:{msg_id}:image", source_type="telegram", type="image",
        content="EURUSD 4H, a breakout above 1.0850", is_on_topic=on_topic,
        message_timestamp=_AT,
    )
    session.add(item)
    await session.flush()
    if picture:
        session.add(EvidenceImage(
            evidence_item_id=item.id, workspace_id=source.workspace_id, mime_type="image/jpeg", data=b"kept",
        ))
        await session.flush()
    return item


def _posted(source, msg_id: int, picture: bytes | None = CHART) -> RawMessage:
    """The message as the connector hands it back, its photo downloaded."""
    metadata = {"message_id": msg_id}
    if picture is not None:
        metadata.update(image_base64=base64.b64encode(picture).decode(), image_mime="image/jpeg")
    return RawMessage(
        stable_id=f"telegram:chan:{msg_id}:image", source_config_id=source.id,
        workspace_id=source.workspace_id, text=None, author="someone", channel="chan",
        timestamp=_AT, source_type="telegram", content_type="image", metadata=metadata,
    )


@pytest.fixture(autouse=True)
def no_spending(monkeypatch):
    """Recovering a picture asks no model anything and records no usage; one that did fails here."""
    def _refuse(what):
        async def _refused(*args, **kwargs):
            raise AssertionError(f"{what} while recovering a picture")
        return _refused
    monkeypatch.setattr(vision_service, "extract_image", _refuse("vision was called"))
    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _refuse("distillation was called"))
    monkeypatch.setattr(usage_service, "log_usage_event", _refuse("a usage event was logged"))
    monkeypatch.setattr(ingestion_pipeline, "log_usage_event", _refuse("a usage event was logged"))


class TestADryRun:
    async def test_counts_the_image_evidence_with_no_picture_and_fetches_nothing(self, db_session, channel):
        source = await _image_source(db_session)
        await _image_evidence(db_session, source, 1)
        await _image_evidence(db_session, source, 2, on_topic=None)        # unreadable: kept for replay
        await _image_evidence(db_session, source, 3, on_topic=False)       # off topic: never shown
        await _image_evidence(db_session, source, 4, picture=True)         # already has one

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id, dry_run=True)

        assert report.found == 2
        assert channel.fetched == []
        assert await db_session.scalar(select(func.count()).select_from(EvidenceImage)
                                       .where(EvidenceImage.workspace_id == source.workspace_id)) == 1

    async def test_lists_per_source_how_many_are_missing(self, db_session):
        source = await _image_source(db_session)
        await _image_evidence(db_session, source, 1)
        await _image_evidence(db_session, source, 2)
        await _image_evidence(db_session, source, 3, on_topic=False)

        rows = {row.source_config_id: row for row in await picture_recovery.missing_by_source(db_session)}

        assert rows[source.id].count == 2


async def _picture_of(session, item) -> EvidenceImage | None:
    return (await session.execute(
        select(EvidenceImage).where(EvidenceImage.evidence_item_id == item.id)
    )).scalar_one_or_none()


class TestARealRun:
    async def test_keeps_the_picture_and_changes_nothing_else(self, db_session, channel):
        source = await _image_source(db_session)
        item = await _image_evidence(db_session, source, 1)
        channel.messages.append(_posted(source, 1))

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        picture = await _picture_of(db_session, item)
        assert (picture.data, picture.mime_type, picture.workspace_id) == (CHART, "image/jpeg", source.workspace_id)
        after = await reread(db_session, item)
        assert (after.content, after.is_on_topic) == ("EURUSD 4H, a breakout above 1.0850", True)
        assert (report.found, report.stored) == (1, 1)
        assert channel.fetched == ["telegram:chan:1:image"]

    async def test_says_which_messages_are_gone_or_have_no_photo(self, db_session, channel):
        source = await _image_source(db_session)
        await _image_evidence(db_session, source, 1)                      # deleted on Telegram
        no_photo = await _image_evidence(db_session, source, 2)
        channel.messages.append(_posted(source, 2, picture=None))

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        assert report.missing == ["telegram:chan:1:image", "telegram:chan:2:image"]
        assert (report.stored, report.failed) == (0, 0)
        assert await _picture_of(db_session, no_photo) is None

    async def test_one_that_cannot_be_fetched_does_not_stop_the_rest(self, db_session, channel):
        source = await _image_source(db_session)
        await _image_evidence(db_session, source, 1)
        recovered = await _image_evidence(db_session, source, 2)
        channel.raises["telegram:chan:1:image"] = ConnectionError("Telegram did not answer")
        channel.messages.append(_posted(source, 2))

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        assert (report.failed, report.stored) == (1, 1)
        assert await _picture_of(db_session, recovered) is not None

    async def test_run_twice_stores_nothing_the_second_time(self, db_session, channel):
        source = await _image_source(db_session)
        await _image_evidence(db_session, source, 1)
        channel.messages.append(_posted(source, 1))
        await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        again = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        assert (again.found, again.stored) == (0, 0)
        assert channel.fetched == ["telegram:chan:1:image"]

    @pytest.mark.parametrize("failure", [
        rpc_error(AuthKeyUnregisteredError),
        rpc_error(ChannelPrivateError),
        rpc_error(FloodWaitError, capture=3600),
    ], ids=["session rejected", "channel gone private", "told to wait an hour"])
    async def test_stops_on_a_failure_every_message_after_would_hit_and_says_so(self, db_session, channel, failure):
        # What it stored before stopping is kept, and reported, not lost to a traceback.
        source = await _image_source(db_session)
        first = await _image_evidence(db_session, source, 3)
        await _image_evidence(db_session, source, 2)
        await _image_evidence(db_session, source, 1)
        first.message_timestamp = datetime(2026, 10, 1, tzinfo=timezone.utc)   # newest: fetched first
        await db_session.flush()
        channel.messages.append(_posted(source, 3))
        channel.raises["telegram:chan:2:image"] = failure
        channel.raises["telegram:chan:1:image"] = failure

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        assert report.stored == 1
        assert report.stopped_because is not None
        assert channel.fetched == ["telegram:chan:3:image", "telegram:chan:2:image"]

    async def test_one_found_off_topic_since_is_not_counted_as_stored(self, db_session, channel, monkeypatch):
        source = await _image_source(db_session)
        item = await _image_evidence(db_session, source, 1)
        channel.messages.append(_posted(source, 1))
        real_get = db_session.get

        async def _judged_off_topic_meanwhile(model, ident, **kw):
            found = await real_get(model, ident, **kw)
            if model is EvidenceItem:
                found.is_on_topic = False
            return found

        monkeypatch.setattr(db_session, "get", _judged_off_topic_meanwhile)

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        assert (report.stored, report.off_topic) == (0, 1)
        assert await _picture_of(db_session, item) is None


    async def test_a_limit_takes_the_newest_first(self, db_session, channel):
        source = await _image_source(db_session)
        older = await _image_evidence(db_session, source, 1)
        newer = await _image_evidence(db_session, source, 2)
        older.message_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await db_session.flush()
        channel.messages.extend([_posted(source, 1), _posted(source, 2)])

        await picture_recovery.recover_pictures(db_session, source_config_id=source.id, limit=1)

        assert channel.fetched == ["telegram:chan:2:image"]
        assert await _picture_of(db_session, newer) is not None


class TestASourceThatNoLongerFetchesImages:
    async def test_is_not_fetched_from_and_says_why(self, db_session, channel):
        # Its connector would hand back nothing for every message, which would
        # read as all of them deleted on Telegram.
        source = await make_source(db_session)
        source.content_filters = {"text": True, "image": False, "video": False, "url": False}
        await db_session.flush()
        await _image_evidence(db_session, source, 1)

        report = await picture_recovery.recover_pictures(db_session, source_config_id=source.id)

        assert report.stopped_because == "the source no longer fetches images"
        assert (report.found, report.missing, channel.fetched) == (1, [], [])
