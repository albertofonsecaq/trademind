"""
Tests for #94 — a link to a compressed page became binary junk, not its article.

The fetcher read the response body before it was decompressed, so a page sent
with `Content-Encoding: gzip` was decoded as text byte for byte. The result was
stored as the evidence's content; when it held a NUL byte Postgres refused the
insert, and the fetch position moved past the message anyway. Twelve links from
one Telegram chat were lost that way on 25–26 Oct.

The page is served from this process on the loopback interface, so the real
HTTP client and its decompression run without leaving the machine.
"""
import gzip
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.connectors.base import RawMessage
from app.services import ingestion_pipeline, url_service, usage_service
from tests.conftest import FakeSession

_ARTICLE = "EURUSD broke above the weekly range on the ECB decision. " * 5
_PAGE = f"<html><head><title>ECB decision</title></head><body><article><p>{_ARTICLE}</p></article></body></html>"


class _GzipPage(BaseHTTPRequestHandler):
    def do_GET(self):
        body = gzip.compress(_PAGE.encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def gzip_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GzipPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/ecb-decision"
    server.shutdown()
    server.server_close()


class TestACompressedPage:
    async def test_is_read_as_its_article_text(self, gzip_site):
        fetched = await url_service.extract(gzip_site)

        assert fetched is not None
        assert "EURUSD broke above the weekly range" in fetched["text"]
        assert fetched["title"] == "ECB decision"


@pytest.fixture
def offline(monkeypatch):
    """Every paid call the text path can make, answered locally."""
    monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: FakeSession())

    async def _on_topic(text, topic_scope):
        return True, "about a currency pair", {"input_tokens": 10, "output_tokens": 2}

    async def _distill(db, item, msg, text, lang):
        return None

    monkeypatch.setattr(ingestion_pipeline.relevance_service, "is_on_topic", _on_topic)
    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
    monkeypatch.setattr(ingestion_pipeline, "_detect_lang", lambda text: "en")


class TestContentWithANulByte:
    """Postgres text cannot hold NUL, and one in the content failed the whole insert."""

    async def test_is_stored_without_it(self, offline):
        msg = RawMessage(
            stable_id="telegram:@chan:7",
            source_config_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            text="EURUSD long\x00 above 1.0850",
            author="someone",
            channel="@chan",
            timestamp=None,
            source_type="telegram",
            content_type="text",
        )

        item = await ingestion_pipeline.process_message(FakeSession(), msg, "forex")

        assert item is not None
        assert item.content == "EURUSD long above 1.0850"
