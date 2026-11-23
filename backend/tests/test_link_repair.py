"""
Tests for #97 — link evidence stored as junk by #94 is found and repaired.

Before #94 a compressed page was decoded as text byte for byte. Where that junk
held a NUL the insert failed; where it did not, the junk was stored as the
evidence's content, relevance-checked, and kept. Each such row still carries the
link it came from, so repairing it needs no connector: fetch the page again,
replace the content in place, and judge it again.
"""
import gzip

import pytest
from sqlalchemy import select

from app.models.evidence_item import EvidenceItem
from app.services import ingestion_pipeline, link_repair_service, url_service
from tests.conftest import make_source, reread

_ARTICLE = "EURUSD broke above the weekly range on the ECB decision. " * 5


def _as_the_bug_stored_it(text: str) -> str:
    """
    What #94 did: gzip bytes decoded as UTF-8, failures replaced. Only junk with
    no NUL in it was ever stored — the rest failed the insert — so none here.
    """
    return gzip.compress(text.encode("utf-8")).decode("utf-8", errors="replace").replace("\x00", "")


class TestTextThatLooksGarbled:
    def test_is_a_compressed_page_read_as_text(self):
        assert url_service.looks_garbled(_as_the_bug_stored_it(f"<p>{_ARTICLE}</p>"))

    def test_is_not_an_article(self):
        assert not url_service.looks_garbled(_ARTICLE)

    def test_is_not_an_article_with_accents_and_emoji(self):
        text = "El EUR/USD rompió el rango semanal tras la decisión del BCE 📈 — ¿continuación? " * 4
        assert not url_service.looks_garbled(text)

    def test_is_not_an_article_with_the_odd_replacement_character(self):
        assert not url_service.looks_garbled(_ARTICLE + "�")

    def test_is_not_empty_text(self):
        assert not url_service.looks_garbled("")


# ── repairing the stored rows ────────────────────────────────────────────────

_URL = "https://example.com/ecb-decision"


@pytest.fixture
def web(monkeypatch):
    """The web as the repair sees it today: url → page, and who asked."""
    pages: dict[str, dict] = {}
    fetched: list[str] = []

    async def _extract(url):
        fetched.append(url)
        return pages.get(url)

    monkeypatch.setattr(url_service, "extract", _extract)
    return pages, fetched


@pytest.fixture
def distilled(monkeypatch, offline_pipeline):
    """Which evidence reached distillation, on top of the offline stubs."""
    seen: list[str] = []

    async def _distill(db, item, msg, text, lang):
        seen.append(item.stable_id)

    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
    return seen


async def _stored_link(db, source, *, content, msg_id=42, url=_URL, on_topic=False) -> EvidenceItem:
    item = EvidenceItem(
        workspace_id=source.workspace_id,
        source_config_id=source.id,
        stable_id=f"telegram:chan:{msg_id}:url:0",
        source_type="telegram",
        type="url",
        content=content,
        confidence=0.9,
        is_on_topic=on_topic,
        relevance_reason="not about trading",
        source_metadata={"author": "someone", "channel": "chan", "url": url, "title": "junk", "message_id": msg_id},
    )
    db.add(item)
    await db.flush()
    return item


@pytest.mark.db
class TestGarbledLinkEvidence:
    async def test_is_fetched_again_and_replaced_in_place(self, db_session, web, distilled, offline_pipeline):
        pages, _ = web
        pages[_URL] = {"url": _URL, "title": "ECB decision", "text": _ARTICLE}
        source = await make_source(db_session)
        item = await _stored_link(db_session, source, content=_as_the_bug_stored_it(_ARTICLE))

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        repaired = await reread(db_session, item)
        assert repaired.id == item.id
        assert repaired.content == _ARTICLE
        assert repaired.source_metadata["title"] == "ECB decision"
        assert repaired.source_metadata["message_id"] == 42
        assert repaired.is_on_topic is True
        assert repaired.relevance_reason == "about a currency pair"
        assert distilled == [item.stable_id]
        assert [e.task_type for e in offline_pipeline.added] == ["relevance_filter"]
        assert (report.garbled, report.repaired_on_topic) == (1, 1)

    async def test_that_was_readable_is_left_alone(self, db_session, web, distilled):
        _, fetched = web
        source = await make_source(db_session)
        item = await _stored_link(db_session, source, content=_ARTICLE)

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        assert fetched == []
        assert (await reread(db_session, item)).content == _ARTICLE
        assert report.garbled == 0

    async def test_is_only_counted_in_a_dry_run(self, db_session, web, distilled, offline_pipeline):
        pages, fetched = web
        pages[_URL] = {"url": _URL, "title": "ECB decision", "text": _ARTICLE}
        source = await make_source(db_session)
        junk = _as_the_bug_stored_it(_ARTICLE)
        item = await _stored_link(db_session, source, content=junk)

        report = await link_repair_service.repair_garbled_links(
            db_session, source_config_id=source.id, dry_run=True
        )

        assert report.garbled == 1
        assert fetched == []
        assert offline_pipeline.added == []
        assert (await reread(db_session, item)).content == junk

    async def test_whose_page_is_gone_is_kept_and_named(self, db_session, web, distilled, offline_pipeline):
        source = await make_source(db_session)
        junk = _as_the_bug_stored_it(_ARTICLE)
        item = await _stored_link(db_session, source, content=junk)

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        assert report.unfetchable == [item.stable_id]
        assert (await reread(db_session, item)).content == junk
        assert offline_pipeline.added == []

    async def test_of_another_source_is_not_touched(self, db_session, web, distilled):
        pages, fetched = web
        pages[_URL] = {"url": _URL, "title": "ECB decision", "text": _ARTICLE}
        source = await make_source(db_session)
        other = await make_source(db_session)
        await _stored_link(db_session, other, content=_as_the_bug_stored_it(_ARTICLE))

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        assert report.garbled == 0
        assert fetched == []

    async def test_that_fails_does_not_stop_the_next(self, db_session, web, distilled, monkeypatch):
        pages, _ = web
        broken = "https://example.com/broken"
        pages[_URL] = {"url": _URL, "title": "ECB decision", "text": _ARTICLE}
        source = await make_source(db_session)
        junk = _as_the_bug_stored_it(_ARTICLE)
        await _stored_link(db_session, source, content=junk, msg_id=1, url=broken)
        item = await _stored_link(db_session, source, content=junk, msg_id=2)
        healthy = url_service.extract

        async def _extract(url):
            if url == broken:
                raise RuntimeError("connection reset")
            return await healthy(url)

        monkeypatch.setattr(url_service, "extract", _extract)

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        assert report.errors == 1
        assert report.repaired_on_topic == 1
        assert (await reread(db_session, item)).content == _ARTICLE

    async def test_whose_page_is_still_junk_is_left_without_a_paid_call(
        self, db_session, web, distilled, offline_pipeline
    ):
        pages, _ = web
        junk = _as_the_bug_stored_it(_ARTICLE)
        pages[_URL] = {"url": _URL, "title": "?", "text": junk}
        source = await make_source(db_session)
        item = await _stored_link(db_session, source, content=junk)

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        assert report.unfetchable == [item.stable_id]
        assert offline_pipeline.added == []

    async def test_already_distilled_from_the_junk_is_named_and_not_distilled_again(
        self, db_session, web, distilled
    ):
        from app.models.trade_idea import TradeIdea

        pages, _ = web
        pages[_URL] = {"url": _URL, "title": "ECB decision", "text": _ARTICLE}
        source = await make_source(db_session)
        item = await _stored_link(db_session, source, content=_as_the_bug_stored_it(_ARTICLE), on_topic=True)
        db_session.add(TradeIdea(evidence_item_id=item.id, workspace_id=source.workspace_id))
        await db_session.flush()

        report = await link_repair_service.repair_garbled_links(db_session, source_config_id=source.id)

        assert report.already_distilled == [item.stable_id]
        assert distilled == []
        assert (await reread(db_session, item)).content == _ARTICLE
