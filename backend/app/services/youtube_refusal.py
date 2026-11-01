"""
Whether an error is YouTube refusing us, rather than a problem with one video (#130).

The captions library says it with its own exception; yt-dlp, downloading audio
for Whisper, says it in words. Both are the same refusal (ADR 0009), and this
is the one place that tells it apart. Read too widely, a problem with one video
— an age check, a server error — would hold every request to YouTube up for a
cool-down and never be given up.
"""
from youtube_transcript_api import RequestBlocked

# What yt-dlp says when YouTube is refusing us. Not "Sign in to confirm" alone:
# "Sign in to confirm your age" is one video's age check.
_IN_WORDS = ("HTTP Error 429", "not a bot")


def is_refusal(exc: BaseException) -> bool:
    if isinstance(exc, RequestBlocked):          # the library's IpBlocked, for a 429, is one
        return True
    return any(words in str(exc) for words in _IN_WORDS)
