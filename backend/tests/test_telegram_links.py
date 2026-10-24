"""
Tests for #68 — a link posted in a Telegram channel never became evidence.

The connector hands a link over with no text of its own: the text is what the
linked page says, and only the pipeline fetches it. But the pipeline dropped
every text-bearing item that arrived without text before routing it, and `url`
was one of them — so since the first commit every link was discarded unread,
while the fetch position moved past it.

A YouTube watch page has no readable text worth keeping, so YouTube links are
not scraped as pages.
"""
import uuid

import pytest

from app.connectors.base import RawMessage
from app.services import ingestion_pipeline, usage_service
from tests.conftest import FakeSession

_ARTICLE = "EURUSD broke above the weekly range on the ECB decision. " * 5


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Every paid or networked call this path can make, answered locally."""
    monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: FakeSession())

    async def _on_topic(text, topic_scope):
        return True, "about a currency pair", {"input_tokens": 10, "output_tokens": 2}

    async def _distill(db, item, msg, text, lang):
        return None

    monkeypatch.setattr(ingestion_pipeline.relevance_service, "is_on_topic", _on_topic)
    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
    monkeypatch.setattr(ingestion_pipeline, "_detect_lang", lambda text: "en")


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

        item = await ingestion_pipeline.process_message(FakeSession(), _link(url), "forex")

        assert item is None
        assert fetched == []

    async def test_a_page_that_only_mentions_youtube_is_still_fetched(self, pages):
        known, fetched = pages
        url = "https://example.com/why-youtube.com-traders-are-wrong"
        known[url] = {"url": url, "title": "Opinion", "text": _ARTICLE}

        item = await ingestion_pipeline.process_message(FakeSession(), _link(url), "forex")

        assert item is not None
        assert fetched == [url]
