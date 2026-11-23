"""
YouTube refusing us, and the cool-down that waits it out (#124, #130, ADR 0009).

Every request to YouTube — for captions, or the audio Whisper needs — is held
by `not_refused` before it is made, through its pace turn (#151): asking anyway
is what got it to refuse. Its own module, so that both kinds of request reach
it without one importing the other.
"""
from app.core.clock import now_utc
from app.core.platform_refusal import Cooldown, PlatformRefused
from app.services import refusal_store


class YouTubeRefused(PlatformRefused):
    """
    YouTube refusing us — its captions, or the audio Whisper needs (a 429, our
    address blocked, a demand to prove we are not a bot).

    Not the same as a video without captions: every video after this one
    would be refused too, and it says nothing about the video (#124, #125, #130).
    """


def refusal(cooldown: Cooldown) -> YouTubeRefused:
    """
    The refusal to raise for a cool-down, saying which refusal in a row earned
    it and until when.

    This reason is what the source page shows for every message the wait holds
    up, so it says both halves ADR 0010 asks for, not only the log line's: the
    day as well as the hour, because an escalating wait can outlast one, and
    how many refusals in a row there have been, because "until tomorrow" alone
    says nothing about how long YouTube has been refusing us.

    Nothing is claimed about a run when the platform has answered us since
    (`consecutive` is 0): the recorded end still holds and is still waited out,
    but there is no run of refusals to count.
    """
    in_a_row = f" ({cooldown.consecutive} in a row)" if cooldown.consecutive else ""
    return YouTubeRefused(
        f"YouTube is refusing our requests{in_a_row} "
        f"until {cooldown.until:%Y-%m-%d %H:%M} UTC",
        until=cooldown.until,
    )


async def not_refused() -> Cooldown | None:
    """The recorded cool-down, once it has passed; YouTubeRefused while it has not."""
    cooldown = await refusal_store.current(refusal_store.YOUTUBE)
    if cooldown is not None and now_utc() < cooldown.until:
        raise refusal(cooldown)
    return cooldown
