"""
Regression tests for #1 — an Anthropic failure surfaced as a bare 500.

A revoked key made /ask return 500 with no body, so a client could not tell a
server bug from a provider problem. One handler on anthropic.APIError covers
every route, since Starlette matches handlers by MRO.

The statuses only matter if something acts on them, and for a while nothing
did: the Ask view showed "Could not get an answer" whichever of them came
back, which is the same nothing the 500 said, one layer further on. The last
class here holds the two ends together — it is in pytest because pytest is the
only runner this repo has, the same reason the locale parity guard lives here
(test_i18n_parity.py).
"""
import re
from pathlib import Path

import anthropic
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.llm_errors import install_llm_error_handlers

ASK_VIEW = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "Ask.tsx"
)

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status, message):
    response = httpx.Response(status, request=REQUEST, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


CASES = {
    "auth": (_status_error(anthropic.AuthenticationError, 401, "API key is invalid."), 502),
    "permission": (_status_error(anthropic.PermissionDeniedError, 403, "forbidden"), 502),
    "rate_limit": (_status_error(anthropic.RateLimitError, 429, "slow down"), 429),
    "timeout": (anthropic.APITimeoutError(request=REQUEST), 504),
    "connection": (anthropic.APIConnectionError(request=REQUEST), 504),
    "upstream_500": (_status_error(anthropic.InternalServerError, 500, "overloaded"), 502),
}


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    install_llm_error_handlers(app)

    for name, (exc, _) in CASES.items():
        # A closure, not a default argument: FastAPI would read `exc=exc` as a
        # query parameter and fail before the route body ever ran.
        def make_route(to_raise):
            async def route():
                raise to_raise
            return route

        app.add_api_route(f"/{name}", make_route(exc), methods=["GET"])

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("name,expected", [(n, s) for n, (_, s) in CASES.items()])
def test_maps_to_a_meaningful_status(client, name, expected):
    assert client.get(f"/{name}").status_code == expected


@pytest.mark.parametrize("name", list(CASES))
def test_never_returns_a_bare_500(client, name):
    """500 was the bug: it told the caller nothing and blamed the wrong side."""
    assert client.get(f"/{name}").status_code != 500


@pytest.mark.parametrize("name", list(CASES))
def test_answers_with_a_json_detail(client, name):
    body = client.get(f"/{name}").json()
    assert isinstance(body.get("detail"), str) and body["detail"]


def test_does_not_leak_the_providers_wording(client):
    """'API key is invalid.' is about our credentials — it belongs in the log."""
    detail = client.get("/auth").json()["detail"]
    assert "API key" not in detail


# ── the statuses reach somebody who acts on them ─────────────────────────────


class TestTheAskViewTellsThemApart:
    """
    Every status the ask path can hand the view has its own sentence there.

    Verified to bite: delete a `case` from `askFailureKey` in Ask.tsx and this
    fails naming the status. Nothing else would — the frontend's only check is
    `npm run build`, and a missing branch of a switch builds perfectly well.
    """

    # The spend gate in api/ask.py, which is ours rather than the provider's and
    # so is not in CASES. A user whose workspace has stopped asking because the
    # budget ran out is the one who most needs to be told why: the help page
    # describes that error, and the view used not to show it.
    OVERAGE_CEILING = 402

    @staticmethod
    def _handled_statuses() -> set[int]:
        """The statuses `askFailureKey` gives a message of their own."""
        source = ASK_VIEW.read_text()
        start = source.index("const askFailureKey")
        body = source[start:source.index("};", start)]
        return {int(status) for status in re.findall(r"case (\d{3}):", body)}

    def test_every_provider_failure_status_has_its_own_message(self):
        from_provider = {status for _, status in CASES.values()}

        missing = from_provider - self._handled_statuses()

        assert not missing, (
            f"Ask.tsx shows the generic message for {sorted(missing)}, "
            "which the server went to the trouble of distinguishing"
        )

    def test_the_budget_gate_has_its_own_message(self):
        assert self.OVERAGE_CEILING in self._handled_statuses()

    def test_the_fallback_is_still_there(self):
        """
        A status nobody anticipated must still say something, so the switch
        keeps a default — one more status from the provider should not render an
        empty answer bubble.
        """
        source = ASK_VIEW.read_text()
        start = source.index("const askFailureKey")

        assert "default:" in source[start:source.index("};", start)]
