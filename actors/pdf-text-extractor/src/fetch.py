"""Download one PDF to disk (spec §2.4 step 2).

Streamed to `{work_dir}/{documentId}.pdf`, never held in memory; refused by Content-Length
and aborted on stream overrun; `%PDF-` required in the first 1024 bytes regardless of
Content-Type; 401/403/429 fail immediately with no retry and no proxy; 5xx and transport
errors get two retries (2 s, 4 s). Redirects are followed by hand, at most five hops, each
hop re-guarded and pinned, never https → http. One download in flight per host (the slot is
held until the body is on disk) and at least one second between requests to the same host.
Each hop has a hard wall of connect + download timeout, enforced with `asyncio.timeout` so a
server that trickles bytes cannot hold a worker past it. No cookie is ever stored or sent;
no connection is kept alive, so a TLS session verified for one host is never reused for
another host on the same pinned IP.

Logging goes through `log_event`, which emits host + documentId + counts only. Exceptions
are logged by class name because `str(exc)` embeds the URL, and presigned links carry tokens.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .guard import BlockedUrl, PinnedUrl, check_url

USER_AGENT = "pdf-text-extractor/0.1 (Apify Actor; +mailto:mooniegilog@gmail.com)"
CONNECT_TIMEOUT = 10.0
DOWNLOAD_TIMEOUT = 60.0
HOP_WALL = CONNECT_TIMEOUT + DOWNLOAD_TIMEOUT
MAX_HOPS = 5
RETRY_DELAYS = (2.0, 4.0)
HOST_GAP = 1.0
MAGIC_WINDOW = 1024
CHUNK = 64 * 1024

# Indirected so tests can drive the clock and skip the real waits.
sleep = asyncio.sleep
clock = time.monotonic

log = logging.getLogger("apify.pdf")


def log_event(event: str, **fields: object) -> None:
    """The one logging wrapper: host, documentId, codes and counts only. Never a URL or text."""
    log.info("%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))


class HostGate:
    """One in-flight request per host, ≥ HOST_GAP seconds between requests to the same host.

    # ponytail: per-host lock + fixed gap; upgrade path = honour robots Crawl-delay per host.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = {}

    @asynccontextmanager
    async def slot(self, host: str):
        async with self._locks[host]:
            last = self._last.get(host)
            if last is not None:
                wait = last + HOST_GAP - clock()
                if wait > 0:
                    await sleep(wait)
            try:
                yield
            finally:
                self._last[host] = clock()


@dataclass
class Fetched:
    url: str
    final_url: str
    error_code: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    bytes: int = 0
    file_name: str | None = None
    document_id: str | None = None
    path: Path | None = None


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, connect=CONNECT_TIMEOUT),
        headers={"User-Agent": USER_AGENT, "Accept": "application/pdf, */*;q=0.5"},
        max_redirects=0,
        limits=httpx.Limits(max_keepalive_connections=0),
    )


_DISPOSITION = re.compile(r"""filename\*?\s*=\s*(?:UTF-8''|"|')?([^";']+)""", re.IGNORECASE)


def file_name_for(url: str, disposition: str | None) -> str:
    if disposition:
        match = _DISPOSITION.search(disposition)
        if match:
            name = unquote(match.group(1)).strip()
            name = os.path.basename(name.replace("\\", "/"))
            if name:
                return name
    base = os.path.basename(unquote(urlsplit(url).path))
    return base or "document.pdf"


async def _send(
    client: httpx.AsyncClient, pinned: PinnedUrl
) -> tuple[httpx.Response | None, str | None]:
    """One request with the retry policy. Returns (response, error_code)."""
    client.cookies.clear()  # a Set-Cookie from an earlier hop or document is never replayed
    try:
        request = client.build_request(
            "GET",
            pinned.url,
            headers={"Host": pinned.host_header},
            extensions={"sni_hostname": pinned.host},
        )
    except httpx.InvalidURL:
        log_event("blocked_url", reason="url", host=pinned.host)
        return None, "blocked_url"
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = await client.send(request, stream=True)
        except httpx.TransportError as exc:
            log_event("request_error", host=pinned.host, error=type(exc).__name__, attempt=attempt)
            response = None
        except httpx.HTTPError as exc:
            log_event("request_error", host=pinned.host, error=type(exc).__name__)
            return None, "download_failed"
        if response is not None and response.status_code < 500:
            return response, None
        if response is not None:
            log_event(
                "server_error", host=pinned.host, status=response.status_code, attempt=attempt
            )
            await response.aclose()
        if attempt < len(RETRY_DELAYS):
            await sleep(RETRY_DELAYS[attempt])
    return None, "download_failed"


async def download(
    client: httpx.AsyncClient, url: str, *, gate: HostGate, cap_bytes: int, work_dir: Path
) -> Fetched:
    result = Fetched(url=url, final_url=url)
    current = url
    previous_scheme: str | None = None
    for _hop in range(MAX_HOPS + 1):
        try:
            pinned = await asyncio.to_thread(check_url, current)
        except BlockedUrl as exc:
            log_event("blocked_url", reason=str(exc))
            result.error_code = "blocked_url"
            return result
        if previous_scheme == "https" and pinned.scheme == "http":
            log_event("blocked_url", reason="downgrade", host=pinned.host)
            result.error_code = "blocked_url"
            return result
        previous_scheme = pinned.scheme
        result.final_url = current
        response: httpx.Response | None = None
        try:
            async with gate.slot(pinned.host), asyncio.timeout(HOP_WALL):
                response, error = await _send(client, pinned)
                if response is None:
                    result.error_code = error
                    return result
                result.http_status = response.status_code
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                return await _receive(response, result, current, pinned.host, cap_bytes, work_dir)
        except TimeoutError:
            log_event("timeout", host=pinned.host, hop=_hop)
            result.error_code = "timeout"
            return result
        finally:
            if response is not None:
                await response.aclose()
    log_event("too_many_redirects", host=pinned.host)
    result.error_code = "download_failed"
    return result


async def _receive(
    response: httpx.Response,
    result: Fetched,
    current: str,
    host: str,
    cap_bytes: int,
    work_dir: Path,
) -> Fetched:
    result.content_type = response.headers.get("content-type")
    if response.status_code != 200:
        log_event("download_failed", host=host, status=response.status_code)
        result.error_code = "download_failed"
        return result
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > cap_bytes:
        log_event("too_large", host=host, content_length=int(length), cap=cap_bytes)
        result.error_code = "too_large"
        return result
    result.file_name = file_name_for(current, response.headers.get("content-disposition"))
    return await _stream_to_disk(response, result, host, cap_bytes, work_dir)


async def _stream_to_disk(
    response: httpx.Response, result: Fetched, host: str, cap_bytes: int, work_dir: Path
) -> Fetched:
    temp = work_dir / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    head = b""
    checked = False
    size = 0
    deadline = clock() + DOWNLOAD_TIMEOUT
    kept = False  # only a complete body survives the finally (early return, error or cancel)
    try:
        with temp.open("wb") as fh:
            async for chunk in response.aiter_bytes(CHUNK):
                if not checked:
                    head += chunk
                    if len(head) >= MAGIC_WINDOW:
                        checked = True
                        if b"%PDF-" not in head[:MAGIC_WINDOW]:
                            log_event("not_pdf", host=host, content_type=result.content_type)
                            result.error_code = "not_pdf"
                            return result
                size += len(chunk)
                if size > cap_bytes:
                    log_event("too_large", host=host, streamed=size, cap=cap_bytes)
                    result.error_code = "too_large"
                    return result
                if clock() > deadline:
                    log_event("timeout", host=host, streamed=size)
                    result.error_code = "timeout"
                    return result
                fh.write(chunk)
                digest.update(chunk)
        if not checked and b"%PDF-" not in head[:MAGIC_WINDOW]:
            log_event("not_pdf", host=host, content_type=result.content_type, bytes=size)
            result.error_code = "not_pdf"
            return result
        kept = True
    except httpx.HTTPError as exc:
        log_event("stream_error", host=host, error=type(exc).__name__, streamed=size)
        result.error_code = (
            "timeout" if isinstance(exc, httpx.TimeoutException) else "download_failed"
        )
        return result
    finally:
        if not kept:
            temp.unlink(missing_ok=True)

    result.bytes = size
    result.document_id = digest.hexdigest()[:16]
    final = work_dir / f"{result.document_id}.pdf"
    try:
        os.link(temp, final)  # atomic: EEXIST means the same bytes are already being parsed
    except FileExistsError:
        temp.unlink(missing_ok=True)
        result.error_code = "duplicate"
        return result
    temp.unlink(missing_ok=True)
    result.path = final
    log_event("downloaded", host=host, document_id=result.document_id, bytes=size)
    return result
