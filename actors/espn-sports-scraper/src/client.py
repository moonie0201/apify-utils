"""One httpx client for the three ESPN JSON hosts (UTILS_SPEC §3.4 steps 1-2).

* one fixed User-Agent — httpx's own default string, the truthful identity of the library
  making the request. No rotation, no fallback, no input to change it. A 403 raises
  ``EdgeBlocked`` and the run fails without charging (§3.4 step 1).
* ``Semaphore(4)``, 30 s timeout, at most two retries on 5xx / transport errors (1 s, 3 s),
  429 honoured via ``Retry-After`` (10 s default).
* 4xx other than 403 raise ``EspnError`` — the caller turns it into a free error row.
* bodies are streamed and counted after decompression; past 32 MB the response is dropped
  (``EspnError``) before it is held whole — a size guard, not a post-hoc check.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = f"python-httpx/{httpx.__version__}"

SITE = "https://site.api.espn.com/apis/site/v2/sports/"
STANDINGS = "https://site.web.api.espn.com/apis/v2/sports/"
CORE = "https://sports.core.api.espn.com/v2/sports/"
ALLOWED_HOSTS = frozenset(
    {"site.api.espn.com", "site.web.api.espn.com", "sports.core.api.espn.com"}
)

TIMEOUT_S = 30.0
CONCURRENCY = 4
RETRY_SLEEPS = (1.0, 3.0)
RATE_LIMIT_SLEEP_S = 10.0
MAX_BODY_BYTES = 32 * 1024 * 1024
TOO_LARGE = "response larger than 32 MB, dropped"


class EdgeBlocked(Exception):
    """ESPN's edge answered 403. The run stops; nothing further is charged."""


class EspnError(Exception):
    """A non-retryable failure for one request (400/404, bad JSON, retries exhausted)."""

    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class EspnClient:
    def __init__(self, client: httpx.AsyncClient | None = None, *, concurrency: int = CONCURRENCY):
        self._own = client is None
        self.http = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT_S,
            follow_redirects=False,
        )
        self.http.headers["User-Agent"] = USER_AGENT
        self._sem = asyncio.Semaphore(concurrency)
        self.requests = 0

    async def aclose(self) -> None:
        if self._own:
            await self.http.aclose()

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        host = httpx.URL(url).host
        if host not in ALLOWED_HOSTS:
            raise EspnError(None, f"refusing to contact {host}")
        last: str = "unknown error"
        for attempt in range(len(RETRY_SLEEPS) + 1):
            if attempt:
                await asyncio.sleep(RETRY_SLEEPS[attempt - 1])
            async with self._sem:
                self.requests += 1
                try:
                    async with self.http.stream("GET", url, params=params) as resp:
                        status = resp.status_code
                        body = b"" if _unreadable(status) else await _read_capped(resp)
                except httpx.HTTPError as exc:
                    last = f"{type(exc).__name__}: {exc}"
                    logger.info("retryable transport error on %s: %s", url, last)
                    continue
            if status == 403:
                raise EdgeBlocked(f"ESPN edge returned 403 for {url}")
            if status == 429:
                delay = _retry_after(resp.headers.get("Retry-After"))
                logger.info("429 from %s; sleeping %.0fs", url, delay)
                await asyncio.sleep(delay)
                last = "429 rate limited"
                continue
            if status >= 500:
                last = f"HTTP {status}"
                continue
            if status >= 400:
                raise EspnError(status, _error_message(status, body))
            try:
                data = json.loads(body)
            except ValueError as exc:
                raise EspnError(status, f"non-JSON response: {exc}") from exc
            if not isinstance(data, dict):
                raise EspnError(status, "unexpected payload shape")
            return data
        raise EspnError(None, f"gave up after {len(RETRY_SLEEPS) + 1} attempts: {last}")


def _unreadable(status: int) -> bool:
    """Statuses whose body we never read (retried, or the run stops)."""
    return status in (403, 429) or status >= 500


async def _read_capped(resp: httpx.Response) -> bytes:
    """Decoded bytes, or ``EspnError`` as soon as the declared or streamed size passes the cap."""
    try:
        declared = int(resp.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BODY_BYTES:
        raise EspnError(resp.status_code, TOO_LARGE)
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise EspnError(resp.status_code, TOO_LARGE)
    return bytes(buf)


def _retry_after(value: str | None) -> float:
    try:
        return min(max(float(value or ""), 1.0), 120.0)
    except ValueError:
        return RATE_LIMIT_SLEEP_S


def _error_message(status: int, body: bytes) -> str:
    try:
        doc = json.loads(body)
        if isinstance(doc, dict) and doc.get("message"):
            return f"HTTP {status}: {doc['message']}"
    except ValueError:
        pass
    return f"HTTP {status}"
