"""
Maps Anthropic SDK failures to meaningful HTTP responses.

Without this, an upstream problem (revoked key, rate limit, timeout) surfaces as
a bare 500 with no body — the client cannot tell a server bug from a provider
outage. Registered once in main.py; Starlette matches by MRO, so a single
handler on APIError covers every SDK exception.
"""
import logging

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

_UNAVAILABLE = "The AI service is temporarily unavailable. Please try again shortly."


def _classify(exc: anthropic.APIError) -> tuple[int, str]:
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        # Our credentials, not the caller's — never surface the provider's wording.
        return 502, "The AI service is not configured correctly. Please contact support."
    if isinstance(exc, anthropic.RateLimitError):
        return 429, "The AI service is rate limited right now. Please try again in a moment."
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return 504, "The AI service did not respond in time. Please try again."
    return 502, _UNAVAILABLE


async def _handle_api_error(request: Request, exc: anthropic.APIError) -> JSONResponse:
    status_code, detail = _classify(exc)
    log.error(
        "Anthropic call failed on %s %s: %s: %s",
        request.method, request.url.path, type(exc).__name__, exc,
    )
    return JSONResponse(status_code=status_code, content={"detail": detail})


def install_llm_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(anthropic.APIError, _handle_api_error)
