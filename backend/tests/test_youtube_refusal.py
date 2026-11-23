"""
Tests for #130, #137 and #139 — one place reads what YouTube's errors mean.

The captions library and yt-dlp each say a refusal their own way. Read too
widely, a problem with one video (an age check, a server error) would hold every
request to YouTube up for a cool-down, count no attempt, and never be given up.

The same place says which problems with one video are final, so those are given
up at once instead of retried for a day (#137), and which are not the video's
at all but our own stale tooling, which an upgrade fixes for all of them (#139).
"""
import pytest
from youtube_transcript_api import IpBlocked, RequestBlocked, TranscriptsDisabled

from app.services.youtube_refusal import extractor_out_of_date, is_refusal, never_transcribable


class TestYouTubeRefusingUs:
    @pytest.mark.parametrize("error", [
        IpBlocked("v"),
        RequestBlocked("v"),
        RuntimeError("ERROR: [youtube] v: Unable to download webpage: HTTP Error 429: Too Many Requests"),
        RuntimeError("ERROR: [youtube] v: Sign in to confirm you’re not a bot. Use --cookies-from-browser"),
        RuntimeError("ERROR: [youtube] v: Sign in to confirm you're not a bot"),
    ])
    def test_is_a_refusal(self, error):
        assert is_refusal(error)

    @pytest.mark.parametrize("error", [
        TranscriptsDisabled("v"),
        RuntimeError("ERROR: [youtube] v: Sign in to confirm your age. This video may be inappropriate"),
        RuntimeError("ERROR: [youtube] v: HTTP Error 500: Internal Server Error"),
        RuntimeError("ERROR: [youtube] v: Video unavailable"),
    ])
    def test_a_problem_with_one_video_is_not(self, error):
        assert not is_refusal(error)


class TestAVideoThatCanNeverBeTranscribed:
    """
    #137: an outcome no retry can change, told apart from one a later attempt
    may well fix — captions YouTube has not added yet.
    """

    @pytest.mark.parametrize("error, says", [
        (RuntimeError("Whisper failed: ERROR: [youtube] A8XsDk4BhyU: This live event has ended"),
         "live event"),
        (RuntimeError("Whisper failed: ERROR: [youtube] BlBoKnm9O_o: Requested format is not available. "
                      "Use --list-formats for a list of available formats"),
         "audio"),
    ])
    def test_says_in_words_why_it_never_will_be(self, error, says):
        why = never_transcribable(error)
        assert why is not None and says in why

    @pytest.mark.parametrize("error", [
        IpBlocked("v"),
        RequestBlocked("v"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: Unable to download webpage: "
                     "HTTP Error 429: Too Many Requests"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: Sign in to confirm you're not a bot"),
    ])
    def test_a_refusal_is_not_the_videos_own_problem(self, error):
        # ADR 0009: read too widely, a block would give up every video it touched.
        assert never_transcribable(error) is None

    @pytest.mark.parametrize("error", [
        TranscriptsDisabled("v"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: Sign in to confirm your age"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: HTTP Error 500: Internal Server Error"),
        RuntimeError("Whisper produced nothing"),
    ])
    def test_anything_else_may_yet_succeed(self, error):
        assert never_transcribable(error) is None


class TestAnExtractorThatNoLongerMatchesYouTube:
    """
    #139: yt-dlp saying it could not read YouTube's player is our own tooling
    being out of date — not the video's problem, and not YouTube refusing us.
    Told apart from both, because an upgrade fixes every video it touched.
    """

    # What the channel source met on sW1axKLV-t4, as the failed message read.
    _STALE = RuntimeError(
        "Whisper failed: ERROR: [youtube] sW1axKLV-t4: Failed to extract any player response; "
        "please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out "
        "the appropriate issue template. Confirm you are on the latest version using yt-dlp -U"
    )

    def test_says_the_extractor_is_out_of_date_and_how_to_refresh_it(self):
        why = extractor_out_of_date(self._STALE)

        assert why is not None
        assert "player" in why
        assert "pip install -U yt-dlp" in why

    def test_is_not_read_as_the_videos_own_final_problem(self):
        # #137 gives a video up at once; this one is ours, and an upgrade fixes it.
        assert never_transcribable(self._STALE) is None

    @pytest.mark.parametrize("error", [
        IpBlocked("v"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: Unable to download webpage: "
                     "HTTP Error 429: Too Many Requests"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: Sign in to confirm you're not a bot"),
    ])
    def test_a_refusal_is_not_our_stale_tooling(self, error):
        # ADR 0009: a refusal is asked first here too, and nothing widens what one is.
        assert extractor_out_of_date(error) is None

    @pytest.mark.parametrize("error", [
        TranscriptsDisabled("v"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: This live event has ended"),
        RuntimeError("Whisper failed: ERROR: [youtube] v: HTTP Error 500: Internal Server Error"),
        RuntimeError("Whisper produced nothing"),
    ])
    def test_and_neither_is_anything_else(self, error):
        assert extractor_out_of_date(error) is None
