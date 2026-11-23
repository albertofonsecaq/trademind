"""
The proxy requests to YouTube go through, when one is set (#150, ADR 0014).

YouTube has been blocking this machine's address, not for volume, so waiting
longer does not end it (ADR 0010). With `YOUTUBE_PROXY_URL` set, caption
requests, and the audio downloads Whisper needs (#152), leave through that
proxy instead; unset, nothing changes.

The URL carries the proxy's credentials. Nothing here ever says it, and
`scrub` takes it — and each part of it that would tell a reader where the proxy
is or how to use it — out of any text before that text is logged or written
into a failed message's reason. An error from the network library about a
proxy names the proxy, so anything that passes such an error on scrubs it.
"""
from urllib.parse import unquote, urlsplit

from youtube_transcript_api.proxies import GenericProxyConfig

from app.core.config import settings

# What stands in for the proxy wherever its URL, or a part of it, was said.
HIDDEN = "<YOUTUBE_PROXY_URL>"


def url() -> str | None:
    """The proxy's URL, or None when none is set. Never to be logged."""
    return settings.YOUTUBE_PROXY_URL.get_secret_value() or None


def for_captions() -> GenericProxyConfig | None:
    """The captions library's proxy config — the same proxy for http and https — or None."""
    proxy = url()
    return GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None


def scrub(text: str) -> str:
    """`text` with the proxy's URL and each identifying part of it hidden."""
    proxy = url()
    if not proxy:
        return text
    for secret in _secrets(proxy):
        text = text.replace(secret, HIDDEN)
    return text


def _secrets(proxy: str) -> list[str]:
    """The URL and its parts, longest first so a part never splits a longer match."""
    parts = urlsplit(proxy)
    found = {proxy, parts.netloc}
    for part in (parts.username, parts.password, parts.hostname):
        if part:
            found.update({part, unquote(part)})
    return sorted((s for s in found if s), key=len, reverse=True)
