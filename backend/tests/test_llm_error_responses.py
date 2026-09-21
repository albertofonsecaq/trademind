"""
Regression tests for #1 — an Anthropic failure surfaced as a bare 500.

A revoked key made /ask return 500 with no body, so a client could not tell a
server bug from a provider problem. One handler on anthropic.APIError covers
every route, since Starlette matches handlers by MRO.
"""
import anthropic
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.llm_errors import install_llm_error_handlers

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
