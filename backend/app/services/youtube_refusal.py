"""
What a YouTube error means: a refusal of us, a problem this video may recover
from, or one it never will (#130, #137).

The captions library says a refusal with its own exception; yt-dlp, downloading
audio for Whisper, says it in words. Both are the same refusal (ADR 0009), and
this is the one place that tells it apart. Read too widely, a problem with one
video — an age check, a server error — would hold every request to YouTube up
for a cool-down and never be given up.

The same seam read once more tells a problem with one video that is temporary —
no captions yet, which YouTube often adds hours after an upload — from one that
is permanent, which is given up at once instead of retried for a day (#137).
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
    return next((why for words, why in _NEVER if words in str(exc)), None)
