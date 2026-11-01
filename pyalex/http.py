"""HTTP transport, authentication, retry handling, and rate-limit parsing."""

import logging
import time
from collections.abc import Generator
from typing import TypedDict
from typing import cast
from typing import override

import httpx2

from pyalex.config import AlexConfig
from pyalex.config import config

logger = logging.getLogger("pyalex")


class RateLimit(TypedDict, total=False):
    """Rate limit and credit data parsed from response headers."""

    limit: int
    remaining: int
    reset: int
    credits_used: int
    credits_remaining: int
    credits_limit: int
    credits_required: int
    credits_reset: int
    cost_usd: float
    limit_usd: float
    remaining_usd: float
    prepaid_remaining_usd: float
    onetime_remaining: int


class QueryError(ValueError):
    """Exception raised for errors in the query."""


class CreditsExhaustedError(QueryError):
    """Raised when the API key has no credits left for the request."""


_client: httpx2.Client | None = None


def _get_client() -> httpx2.Client:
    """Return the shared client used to reuse HTTP connections."""
    global _client
    if _client is None:
        _client = httpx2.Client(
            timeout=config.timeout,
            follow_redirects=True,
            limits=httpx2.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )
    return _client


class OpenAlexAuth(httpx2.Auth):
    """Attach OpenAlex API key, email, and user-agent headers."""

    def __init__(self, config: AlexConfig) -> None:
        self.config = config

    @override
    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response, None]:
        if self.config.api_key:
            request.headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.config.email:
            request.headers["From"] = self.config.email
        if self.config.user_agent:
            request.headers["User-Agent"] = self.config.user_agent
        yield request


_RATELIMIT_PREFIX = "x-ratelimit-"


def _extract_ratelimit(response: httpx2.Response) -> RateLimit:
    """Parse rate-limit headers into native numeric values."""
    values: dict[str, int | float] = {}
    for header, raw in response.headers.items():
        name = header.lower()
        if not name.startswith(_RATELIMIT_PREFIX):
            continue
        key = name.removeprefix(_RATELIMIT_PREFIX).replace("-", "_")
        try:
            values[key] = float(raw) if key.endswith("_usd") else int(raw)
        except ValueError:
            logger.debug("Ignoring unparsable rate limit header %s=%r", header, raw)
    return cast(RateLimit, values)


def _check_credits(response: httpx2.Response) -> None:
    """Raise when a 429 response means the account's credits are exhausted."""
    if response.status_code != 429:
        return
    try:
        remaining = int(response.headers.get("x-ratelimit-remaining", "1"))
        required = int(response.headers.get("x-ratelimit-credits-required", "1"))
    except ValueError:
        return
    if remaining >= required:
        return
    reset = response.headers.get("x-ratelimit-reset")
    reset_hint = f" The budget resets in {reset} seconds." if reset else ""
    raise CreditsExhaustedError(
        f"Insufficient credits: {remaining} remaining, {required} required.{reset_hint}"
    )


def _send(client: httpx2.Client, url: str, *, auth: httpx2.Auth) -> httpx2.Response:
    """Send a request with configured transport and status-code retries."""
    attempts = max(1, config.max_retries + 1)
    for attempt in range(attempts):
        is_last_attempt = attempt + 1 >= attempts
        try:
            response = client.get(url, auth=auth, timeout=config.timeout)
        except httpx2.TransportError:
            if is_last_attempt:
                raise
            time.sleep(config.retry_backoff_factor * (2**attempt))
            continue
        _check_credits(response)
        if response.status_code in config.retry_http_codes and not is_last_attempt:
            time.sleep(config.retry_backoff_factor * (2**attempt))
            continue
        return response
    raise RuntimeError("unreachable: retry loop always returns or raises")
