"""
URL content extraction.
Fetches a URL and extracts the main readable text (article body) using readability-lxml.
Falls back to BeautifulSoup paragraph extraction if readability fails.
Returns None on fetch errors, binary content, or non-HTML responses.
"""
from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

_TIMEOUT_S = 15
_MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB
_MIN_TEXT_CHARS = 100   # discard pages with less meaningful text

_SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".tar", ".gz", ".mp4", ".mp3", ".jpg", ".jpeg",
    ".png", ".gif", ".svg", ".webp",
}
_SKIP_SCHEMES = {"mailto", "tg", "ftp"}


def _is_skippable(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme in _SKIP_SCHEMES:
            return True
        path = parsed.path.lower()
        return any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)
    except Exception:
        return True


def _extract_sync(url: str) -> dict | None:
    import requests

    if _is_skippable(url):
        return None

    try:
        resp = requests.get(
            url,
            timeout=_TIMEOUT_S,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TradeMind/1.0; +https://trademind.app)",
                "Accept": "text/html,application/xhtml+xml",
            },
            stream=True,
            allow_redirects=True,
        )
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return None

        raw_bytes = resp.raw.read(_MAX_CONTENT_BYTES)
        html = raw_bytes.decode("utf-8", errors="replace")

    except Exception as e:
        log.debug("URL fetch failed for %s: %s", url, e)
        return None

    # Try readability-lxml first (best article extraction)
    title, body = _readability_extract(html)
    if not body or len(body) < _MIN_TEXT_CHARS:
        title, body = _bs4_extract(html)
    if not body or len(body) < _MIN_TEXT_CHARS:
        return None

    return {"url": url, "title": title or url, "text": body[:8000]}


def _readability_extract(html: str) -> tuple[str, str]:
    try:
        from readability import Document
        doc = Document(html)
        title = doc.title() or ""
        # readability returns HTML — strip tags
        import re
        body = re.sub(r"<[^>]+>", " ", doc.summary())
        body = re.sub(r"\s+", " ", body).strip()
        return title, body
    except Exception:
        return "", ""


def _bs4_extract(html: str) -> tuple[str, str]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title else ""
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text()) > 40]
        body = " ".join(paragraphs)
        return title, body
    except Exception:
        return "", ""


async def extract(url: str) -> dict | None:
    """
    Async wrapper. Returns {url, title, text} or None.
    Run synchronous HTTP/parsing in a thread pool.
    """
    return await asyncio.get_event_loop().run_in_executor(None, _extract_sync, url)


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_youtube(url: str) -> bool:
    """
    Whether a link points anywhere on YouTube. No YouTube page has readable text
    worth keeping, so none is scraped — whether or not it is one video.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == "youtu.be" or host in _YOUTUBE_HOSTS


def youtube_video_id(url: str) -> str | None:
    """
    The video id a link points at, or None if it is not a link to one video.

    A caller that finds an id here gets the video's transcript instead of
    scraping the page.
    """
    if not is_youtube(url):
        return None
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    if parsed.hostname.lower() == "youtu.be":
        candidate = parts[0] if parts else None
    elif parsed.path == "/watch":
        candidate = parse_qs(parsed.query).get("v", [None])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
        candidate = parts[1]
    else:
        candidate = None
    return candidate if candidate and _YOUTUBE_ID_RE.match(candidate) else None
