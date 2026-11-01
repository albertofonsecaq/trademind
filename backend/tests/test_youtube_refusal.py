"""
Tests for #130 — one place decides whether YouTube is refusing us.

The captions library and yt-dlp each say it their own way. Read too widely, a
problem with one video (an age check, a server error) would hold every request
to YouTube up for a cool-down, count no attempt, and never be given up.
"""
import pytest
from youtube_transcript_api import IpBlocked, RequestBlocked, TranscriptsDisabled

from app.services.youtube_refusal import is_refusal


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
