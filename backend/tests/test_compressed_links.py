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
from app.services import ingestion_pipeline, url_service
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


class TestContentWithANulByte:
    """Postgres text cannot hold NUL, and one in the content failed the whole insert."""

    async def test_is_stored_without_it(self, offline_pipeline):
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

    async def test_in_a_page_title_is_stored_without_it(self, offline_pipeline, monkeypatch):
        # The title goes into source_metadata, and JSONB refuses NUL as text does.
        url = "https://example.com/ecb-decision"

        async def _extract(u):
            return {"url": u, "title": "ECB\x00 decision", "text": _ARTICLE}

        monkeypatch.setattr(ingestion_pipeline.url_service, "extract", _extract)
        msg = RawMessage(
            stable_id="telegram:@chan:42:url:0",
            source_config_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            text=None,
            author="someone",
            channel="@chan",
            timestamp=None,
            source_type="telegram",
            content_type="url",
            metadata={"message_id": 42, "url": url},
        )

        item = await ingestion_pipeline.process_message(FakeSession(), msg, "forex")

        assert item.source_metadata["title"] == "ECB decision"
