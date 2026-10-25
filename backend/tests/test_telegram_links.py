"""
Tests for #68 — a link posted in a Telegram channel never became evidence.

The connector hands a link over with no text of its own: the text is what the
linked page says, and only the pipeline fetches it. But the pipeline dropped
every text-bearing item that arrived without text before routing it, and `url`
was one of them — so since the first commit every link was discarded unread,
while the fetch position moved past it.

A YouTube watch page has no readable text worth keeping, so YouTube links are
not scraped as pages. #70 turns them into the video's transcript instead, the
way a YouTube source does — and skips a video the workspace already holds,
before anything is paid to transcribe it again.
"""
import uuid

import pytest

from app.connectors.base import RawMessage
from app.models.evidence_item import EvidenceItem
from app.services import ingestion_pipeline, transcription_service, usage_service, youtube_transcript
from tests.conftest import FakeSession

_ARTICLE = "EURUSD broke above the weekly range on the ECB decision. " * 5


@pytest.fixture
def usage_log():
    return FakeSession()


@pytest.fixture(autouse=True)
def offline(monkeypatch, usage_log):
    """Every paid or networked call this path can make, answered locally."""
    monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: usage_log)

    async def _on_topic(text, topic_scope):
        return True, "about a currency pair", {"input_tokens": 10, "output_tokens": 2}

    async def _distill(db, item, msg, text, lang):
        return None

    monkeypatch.setattr(ingestion_pipeline.relevance_service, "is_on_topic", _on_topic)
    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
    monkeypatch.setattr(ingestion_pipeline, "_detect_lang", lambda text: "en")


@pytest.fixture(autouse=True)
def videos(monkeypatch):
    """YouTube, as far as these tests can see it: captions and Whisper by video."""
    state = {"captions": {}, "whisper": {}, "asked": []}

    async def _captions(video_id):
        state["asked"].append(("captions", video_id))
        return state["captions"].get(video_id)

    async def _whisper(video_url):
        state["asked"].append(("whisper", video_url))
        return state["whisper"].get(video_url)

    monkeypatch.setattr(youtube_transcript, "fetch_captions", _captions)
    monkeypatch.setattr(transcription_service, "transcribe_youtube_video", _whisper)
    return state


class FakeWorkspace(FakeSession):
    """
    A session that remembers the evidence added to it, so a later lookup in the
    same fetch finds it — which is what a flushed row in one transaction does.
    """

    def __init__(self, already_held: tuple[str, ...] = ()):
        super().__init__(rows={EvidenceItem: self._held})
        self.held = list(already_held)

    def add(self, obj):
        super().add(obj)
        if isinstance(obj, EvidenceItem):
            self.held.append(obj.stable_id)

    def _held(self, statement):
        for value in statement.compile().params.values():
            if not isinstance(value, str):
                continue
            if any(sid == value or (value.endswith(":") and sid.startswith(value)) for sid in self.held):
                return object()
        return None


@pytest.fixture
def pages(monkeypatch):
    """The web, as far as these tests can see it: url → page, and who asked."""
    known: dict[str, dict] = {}
    fetched: list[str] = []

    async def _extract(url):
        fetched.append(url)
        return known.get(url)

    monkeypatch.setattr(ingestion_pipeline.url_service, "extract", _extract)
    return known, fetched


def _link(url: str, **over) -> RawMessage:
    """A link exactly as the Telegram connector hands it over: no text."""
    fields = {
        "stable_id": "telegram:@chan:42:url:0",
        "source_config_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "text": None,
        "author": "someone",
        "channel": "@chan",
        "timestamp": None,
        "source_type": "telegram",
        "content_type": "url",
        "metadata": {"message_id": 42, "url": url},
    }
    return RawMessage(**{**fields, **over})


class TestALinkBecomesEvidence:
    async def test_a_link_with_no_text_of_its_own_is_fetched_and_kept(self, pages):
        known, _ = pages
        url = "https://example.com/ecb-decision"
        known[url] = {"url": url, "title": "ECB decision", "text": _ARTICLE}

        item = await ingestion_pipeline.process_message(FakeSession(), _link(url), "forex")

        assert item is not None
        assert item.type == "url"
        assert item.content == _ARTICLE
        assert item.source_metadata["url"] == url
        assert item.source_metadata["message_id"] == 42

    async def test_a_page_with_nothing_readable_is_not_kept(self, pages):
        _, fetched = pages
        url = "https://example.com/login-wall"

        item = await ingestion_pipeline.process_message(FakeSession(), _link(url), "forex")

        assert item is None
        assert fetched == [url]


class TestTextThatIsMissingIsStillSkipped:
    @pytest.mark.parametrize("content_type", ["text", "video_transcript"])
    async def test_before_any_paid_call(self, monkeypatch, content_type):
        async def _no_call(*a, **kw):
            raise AssertionError("a paid call was made for an item with no text")

        monkeypatch.setattr(ingestion_pipeline.relevance_service, "is_on_topic", _no_call)
        msg = _link("unused", content_type=content_type, metadata={})

        assert await ingestion_pipeline.process_message(FakeSession(), msg, "forex") is None


class TestAYouTubeLinkIsNotScrapedAsAPage:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
    ])
    async def test_no_page_fetch_and_no_url_evidence(self, pages, url):
        known, fetched = pages
        known[url] = {"url": url, "title": "YouTube", "text": _ARTICLE}
        db = FakeWorkspace()

        await ingestion_pipeline.process_message(db, _link(url), "forex")

        assert fetched == []
        assert [e.type for e in db.added if isinstance(e, EvidenceItem)] == []

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/@MarketWizards",
        "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
        "https://www.youtube.com/playlist?list=PL123",
        "https://youtube.com/",
        "https://www.youtube.com/watch?v=not-an-id",
    ])
    async def test_a_youtube_page_that_is_not_one_video_is_left_alone(self, pages, videos, url):
        known, fetched = pages
        known[url] = {"url": url, "title": "YouTube", "text": _ARTICLE}
        db = FakeWorkspace()

        item = await ingestion_pipeline.process_message(db, _link(url), "forex")

        assert item is None
        assert fetched == []
        assert videos["asked"] == []

    async def test_a_page_that_only_mentions_youtube_is_still_fetched(self, pages):
        known, fetched = pages
        url = "https://example.com/why-youtube.com-traders-are-wrong"
        known[url] = {"url": url, "title": "Opinion", "text": _ARTICLE}

        item = await ingestion_pipeline.process_message(FakeSession(), _link(url), "forex")

        assert item is not None
        assert fetched == [url]


# ── #70: a YouTube link becomes the video's transcript ───────────────────────

_VIDEO = "dQw4w9WgXcQ"
_WATCH = f"https://www.youtube.com/watch?v={_VIDEO}"


def _captions(minutes: int) -> list[dict]:
    return [
        {"text": f"minute {i}: EURUSD holds the range", "start": i * 60.0, "duration": 60.0}
        for i in range(minutes)
    ]


class TestAYouTubeLinkBecomesTranscriptEvidence:
    async def test_credited_to_the_telegram_source_with_the_link_and_message(self, videos):
        videos["captions"][_VIDEO] = _captions(2)
        link = _link("https://youtu.be/dQw4w9WgXcQ")
        db = FakeWorkspace()

        item = await ingestion_pipeline.process_message(db, link, "forex")

        assert item is not None
        assert item.type == "video_transcript"
        assert item.stable_id == f"youtube:{_VIDEO}:full"
        assert item.source_config_id == link.source_config_id
        assert item.source_type == "telegram"
        assert item.source_metadata["url"] == "https://youtu.be/dQw4w9WgXcQ"
        assert item.source_metadata["message_id"] == 42
        assert item.source_metadata["video_id"] == _VIDEO

    async def test_a_long_video_keeps_every_chunk(self, videos, monkeypatch):
        monkeypatch.setattr(youtube_transcript.settings, "YOUTUBE_CHUNK_SECONDS", 300)
        videos["captions"][_VIDEO] = _captions(12)
        db = FakeWorkspace()

        await ingestion_pipeline.process_message(db, _link(_WATCH), "forex")

        assert db.held == [f"youtube:{_VIDEO}:chunk_{i}" for i in range(3)]

    async def test_whisper_is_paid_for_and_logged_once(self, videos, usage_log, monkeypatch):
        from decimal import Decimal

        monkeypatch.setattr(youtube_transcript.settings, "YOUTUBE_CHUNK_SECONDS", 300)
        videos["whisper"][_WATCH] = (_captions(12), Decimal("0.072"))

        await ingestion_pipeline.process_message(FakeWorkspace(), _link(_WATCH), "forex")

        transcription = [e for e in usage_log.added if e.task_type == "transcription"]
        assert len(transcription) == 1
        assert transcription[0].cost_usd == Decimal("0.072")

    async def test_no_captions_and_no_whisper_is_nothing_and_no_error(self, videos):
        db = FakeWorkspace()

        assert await ingestion_pipeline.process_message(db, _link(_WATCH), "forex") is None
        assert db.held == []


class TestAVideoTheWorkspaceAlreadyHoldsIsSkipped:
    async def test_before_captions_or_whisper_are_asked_for(self, videos):
        """Whisper is paid before the duplicate check could catch it, so this is the check."""
        videos["captions"][_VIDEO] = _captions(2)
        db = FakeWorkspace(already_held=(f"youtube:{_VIDEO}:chunk_3",))  # from a YouTube source

        item = await ingestion_pipeline.process_message(db, _link(_WATCH), "forex")

        assert item is None
        assert videos["asked"] == []
        assert db.held == [f"youtube:{_VIDEO}:chunk_3"]

    async def test_the_same_video_linked_twice_in_one_fetch_is_transcribed_once(self, videos):
        videos["captions"][_VIDEO] = _captions(2)
        db = FakeWorkspace()
        first = _link(_WATCH, stable_id="telegram:@chan:42:url:0")
        second = _link("https://youtu.be/dQw4w9WgXcQ", stable_id="telegram:@chan:57:url:0")

        await ingestion_pipeline.process_message(db, first, "forex")
        again = await ingestion_pipeline.process_message(db, second, "forex")

        assert again is None
        assert videos["asked"] == [("captions", _VIDEO)]

    async def test_another_video_is_not_mistaken_for_it(self, videos):
        videos["captions"][_VIDEO] = _captions(2)
        db = FakeWorkspace(already_held=("youtube:aaaaaaaaaaa:full",))

        item = await ingestion_pipeline.process_message(db, _link(_WATCH), "forex")

        assert item is not None


class TestOneChunkFailingDoesNotUndoTheOthers:
    """
    A link's chunks all ran inside the link's one savepoint, so a chunk that
    failed rolled back the ones already stored, while the Whisper cost survived
    on its own session — and the next attempt paid for the video again. Each
    chunk is its own savepoint now, as it is for a YouTube source.
    """

    @pytest.fixture
    def three_chunks(self, videos, monkeypatch):
        monkeypatch.setattr(youtube_transcript.settings, "YOUTUBE_CHUNK_SECONDS", 300)
        videos["captions"][_VIDEO] = _captions(12)

    def _failing_on(self, monkeypatch, stable_id, error):
        async def _distill(db, item, msg, text, lang):
            if item.stable_id == stable_id:
                raise error

        monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)

    async def test_the_chunks_either_side_are_kept(self, three_chunks, monkeypatch):
        self._failing_on(monkeypatch, f"youtube:{_VIDEO}:chunk_1", RuntimeError("distillation failed"))
        db = FakeWorkspace()

        item = await ingestion_pipeline.process_message(db, _link(_WATCH), "forex")

        assert item.stable_id == f"youtube:{_VIDEO}:chunk_0"
        assert db.savepoints == ["enter", "release", "enter", "rollback", "enter", "release"]

    async def test_a_failure_that_dooms_the_run_still_stops_it(self, three_chunks, monkeypatch):
        import anthropic
        import httpx

        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(401, request=request, json={"error": {"message": "bad key"}})
        doomed = anthropic.AuthenticationError("bad key", response=response, body=None)
        self._failing_on(monkeypatch, f"youtube:{_VIDEO}:chunk_1", doomed)

        with pytest.raises(anthropic.AuthenticationError):
            await ingestion_pipeline.process_message(FakeWorkspace(), _link(_WATCH), "forex")


@pytest.mark.db
class TestTheHeldCheckAgainstARealDatabase:
    """
    The check is a prefix LIKE, and video ids may contain `_` — a LIKE wildcard.
    Unescaped, a different video one character apart would count as held and
    the link would be skipped for good. Only Postgres can say what the query
    really matches, so these run against one (see conftest.py).
    """

    _ID = "ab_cdefghij"

    async def _hold(self, db_session, source, stable_id):
        from datetime import datetime, timezone

        db_session.add(EvidenceItem(
            id=uuid.uuid4(), workspace_id=source.workspace_id,
            source_config_id=source.id, stable_id=stable_id,
            source_type="youtube", type="video_transcript", content="EURUSD",
            confidence=1.0, message_timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        ))
        await db_session.flush()

    def _link_for(self, source):
        return _link(
            f"https://youtu.be/{self._ID}",
            source_config_id=source.id, workspace_id=source.workspace_id,
        )

    async def test_any_chunk_of_the_video_counts_as_held(self, db_session, videos):
        from tests.conftest import make_source

        source = await make_source(db_session)
        await self._hold(db_session, source, f"youtube:{self._ID}:chunk_3")

        item = await ingestion_pipeline.process_message(db_session, self._link_for(source), "forex")

        assert item is None
        assert videos["asked"] == []

    async def test_an_underscore_is_not_a_wildcard(self, db_session, videos):
        from tests.conftest import make_source

        source = await make_source(db_session)
        await self._hold(db_session, source, "youtube:abXcdefghij:full")

        await ingestion_pipeline.process_message(db_session, self._link_for(source), "forex")

        # Not skipped: the video was looked for (no captions, so Whisper too).
        assert videos["asked"][0] == ("captions", self._ID)
