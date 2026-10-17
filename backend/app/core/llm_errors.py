"""
What a provider failure means, in the two places we have to decide.

For a request, it means an HTTP status and a sentence for the caller: without
this an upstream problem (revoked key, rate limit, timeout) surfaced as a bare
500 with no body, and the client could not tell a server bug from a provider
outage. Registered once in main.py; Starlette matches by MRO, so one handler on
APIError covers every SDK exception.

For a run over many items — a backfill, a poll tick — it means whether to carry
on. Both answers are judgements about the same failures, so they live together
rather than drifting apart in a worker (#9).

In neither case does the provider's own wording reach a user or a stored row.
"""
import logging

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

_UNAVAILABLE = "The AI service is temporarily unavailable. Please try again shortly."

# An account that has run out and a single unusable message both arrive as 400
# with type invalid_request_error, so the body's type cannot tell them apart and
# the wording is the only signal there is (#9).
_ACCOUNT_LIMIT_MARKERS = ("usage limit", "credit balance")

_FATAL_SUMMARY = "The AI service rejected every request: the account's limit or credit is exhausted."
_CREDENTIALS_SUMMARY = "The AI service is not configured correctly (credentials rejected)."
_PROVIDER_SUMMARY = "The AI service failed."


def _provider_message(exc: anthropic.APIError) -> str:
    """
    The provider's own message, and nothing else.

    str(exc) on an SDK error embeds the serialized request and response, so a
    trading message that happens to discuss a credit balance would match the
    markers below and abandon a whole range. Read the message field instead.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    message = getattr(exc, "message", None)
    return message if isinstance(message, str) else ""


def is_fatal_for_run(exc: BaseException) -> bool:
    """
    True when this failure will repeat for every remaining item in a run.

    A backfill of 1,610 messages once hit an account usage limit partway and
    then made 1,610 more doomed calls, one per message, each rolled back and
    logged — real wall-clock time spent learning nothing after the first (#9).
    Those are worth abandoning the run for. A rate limit is not: it is transient
    by definition, and the answer there is backoff, not throwing the range away.
    An ordinary 400 is not either, or one oversized image would condemn every
    message behind it.
    """
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True            # our credentials: nothing will succeed
    if isinstance(exc, anthropic.RateLimitError):
        return False           # transient
    if isinstance(exc, anthropic.BadRequestError):
        message = _provider_message(exc).lower()
        return any(marker in message for marker in _ACCOUNT_LIMIT_MARKERS)
    return False


def safe_failure_summary(exc: BaseException) -> str:
    """
    One line fit to store on a job row and show to a user.

    A provider's message is theirs, often about our account, and sometimes names
    a key or a billing state — the same reason _classify never passes it to a
    caller. So provider failures are summarized by class, and only our own
    exceptions, which describe our own bugs, keep their text.
    """
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return _CREDENTIALS_SUMMARY
    if isinstance(exc, anthropic.APIError):
        return _FATAL_SUMMARY if is_fatal_for_run(exc) else _PROVIDER_SUMMARY
    return str(exc)


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
