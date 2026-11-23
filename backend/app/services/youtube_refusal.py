"""
What a YouTube error means: a refusal of us, a problem this video may recover
from, one it never will, or none of the three — our own tooling being out of
date (#130, #137, #139).

The captions library says a refusal with its own exception; yt-dlp, downloading
audio for Whisper, says it in words. Both are the same refusal (ADR 0009), and
this is the one place that tells it apart. Read too widely, a problem with one
video — an age check, a server error — would hold every request to YouTube up
for a cool-down and never be given up.

The same seam read once more tells a problem with one video that is temporary —
no captions yet, which YouTube often adds hours after an upload — from one that
is permanent, which is given up at once instead of retried for a day (#137,
ADR 0012).

And read once more again, it tells both of those from a failure that is neither
the platform's nor the video's but ours: a yt-dlp whose extractor no longer
matches YouTube's player. That one costs the video nothing and is waited out
until somebody upgrades the tool (#139). The same goes for the proxy requests
leave through when one is set, down or rejecting our credentials: ours, waited
out until somebody fixes it (#153, ADR 0014). Every verdict here asks
`is_refusal` first, so none of them can widen what a refusal is.
"""
from collections.abc import Iterable

from youtube_transcript_api import RequestBlocked

from app.services import youtube_proxy

# What yt-dlp says when YouTube is refusing us. Not "Sign in to confirm" alone:
# "Sign in to confirm your age" is one video's age check.
_IN_WORDS = ("HTTP Error 429", "not a bot")

# What yt-dlp says when no later attempt can succeed either, and what to tell a
# person. Only outcomes that are the video's own and final belong here: a
# refusal, an age check, a server error and a video with no captions yet are all
# worth asking again for.
_NEVER = (
    ("This live event has ended",
     "it is a live event that has ended and YouTube offers no recording of it"),
    ("Requested format is not available",
     "YouTube offers no audio for it to download"),
)

# What yt-dlp says when its extractor no longer matches YouTube's player, and
# what to tell a person: the cause is ours and so is the fix, so the words name
# the command rather than leaving a reader to guess (#139).
_OUT_OF_DATE = (
    ("Failed to extract any player response",
     "our yt-dlp can no longer read YouTube's player. Refresh it with "
     "`pip install -U yt-dlp`, pin the new version in backend/requirements.txt "
     "and restart; the videos it failed are then picked up by an ordinary retry"),
)


# What requests and yt-dlp say when the proxy in YOUTUBE_PROXY_URL is what
# failed, and what to tell a person: the setting to fix, never the URL (#153).
# A 407 is asked first, for the proxy that answered it was reached.
_FIX_IT = (
    "; fix the proxy or the setting and the videos it failed are picked up by an "
    "ordinary retry"
)
_PROXY_FAILED = (
    ("Proxy Authentication Required",
     "the proxy in YOUTUBE_PROXY_URL rejected our credentials (407)" + _FIX_IT),
    ("Unable to connect to proxy",
     "the proxy in YOUTUBE_PROXY_URL could not be reached" + _FIX_IT),
)


def is_refusal(exc: BaseException) -> bool:
    if isinstance(exc, RequestBlocked):          # the library's IpBlocked, for a 429, is one
        return True
    return any(words in str(exc) for words in _IN_WORDS)


def never_transcribable(exc: BaseException) -> str | None:
    """
    Why this video can never produce a transcript, in words a person reads, or
    None when another attempt may yet succeed.

    A refusal is asked first and answers None: it is the platform's doing, not
    the video's, and every video met during a block would read the same way
    (ADR 0009).
    """
    if is_refusal(exc):
        return None
    return _said(exc, _NEVER)


def extractor_out_of_date(exc: BaseException) -> str | None:
    """
    Why our own tooling, rather than this video, is what failed — and what to
    run about it — or None when the failure is not ours.

    A stale extractor fails every video it is asked for and no retry changes
    that, but an upgrade changes all of it at once. So this is neither the
    video's own failure, which would spend its three attempts on our bug and
    give up a backlog the upgrade would have fetched, nor a refusal, which
    would hold every request to YouTube up for a cool-down (ADR 0009).
    """
    if is_refusal(exc):
        return None
    return _said(exc, _OUT_OF_DATE)


def _said(exc: BaseException, verdicts: tuple[tuple[str, str], ...]) -> str | None:
    """What one of these verdicts says about this error, if any of them does."""
    return _said_in(str(exc), verdicts)


def _said_in(text: str, verdicts: tuple[tuple[str, str], ...]) -> str | None:
    return next((why for words, why in verdicts if words in text), None)


def proxy_failed(exc: BaseException, warned: Iterable[str] = ()) -> str | None:
    """
    Why the proxy in YOUTUBE_PROXY_URL, rather than YouTube or this video, is
    what failed — saying the setting to fix and nothing of the URL — or None
    when it is not (#153).

    `warned` is what the tool said on the way to `exc`. yt-dlp needs it: when
    it cannot get through the proxy it warns so for each page and then fails in
    the words of a stale extractor, which, read alone, would send a person to
    upgrade a tool that is fine.

    Only possible while a proxy is set: without one, nothing of ours stands
    between us and YouTube to fail. A refusal is asked first, and so is a video
    that can never be transcribed — a proxy that stumbled on the way to that
    answer did not stop it being given.
    """
    if is_refusal(exc) or never_transcribable(exc) or youtube_proxy.url() is None:
        return None
    for said in (str(exc), *warned):
        why = _said_in(said, _PROXY_FAILED)
        if why:
            return why
    return None
