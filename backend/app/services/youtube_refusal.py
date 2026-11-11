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
is permanent, which is given up at once instead of retried for a day (#137).

And read once more again, it tells both of those from a failure that is neither
the platform's nor the video's but ours: a yt-dlp whose extractor no longer
matches YouTube's player. That one costs the video nothing and is waited out
until somebody upgrades the tool (#139). Every verdict here asks `is_refusal`
first, so none of them can widen what a refusal is.
"""
from youtube_transcript_api import RequestBlocked

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
    return next((why for words, why in verdicts if words in str(exc)), None)
