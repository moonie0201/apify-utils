"""Network layer for `youtube-thumbnail-downloader` (UTILS_SPEC §1.4 steps 3–4).

Only two hosts are ever contacted: `i.ytimg.com` (HEAD for availability, GET for files
and the tiny `frame0.jpg` used for aspect detection) and `www.youtube.com/oembed` for
title and channel. Nothing else — no watch pages, no internal endpoints, no Data API.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import httpx

USER_AGENT = "apify-utils/youtube-thumbnail-downloader/0.1 (+mailto:mooniegilog@gmail.com)"
CDN = "https://i.ytimg.com"
OEMBED = "https://www.youtube.com/oembed"

#: Fixed dimensions of the standard sizes, largest first (measured; maxresdefault is 1280×720).
SIZES: dict[str, tuple[int, int]] = {
    "maxresdefault": (1280, 720),
    "sddefault": (640, 480),
    "hqdefault": (480, 360),
    "mqdefault": (320, 180),
    "default": (120, 90),
}
OAR = ("oar1", "oar2", "oar3")
PROBED = (*SIZES, "oar1")
FRAME0_MAX_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_RETRY_AFTER = 30.0
#: One shared pool caps in-flight requests across both hosts (§1.4 step 4, §1.8: ≤10 per host).
CONCURRENCY_LIMIT = 10


def thumb_url(video_id: str, name: str, fmt: str = "jpg") -> str:
    if fmt == "webp":
        return f"{CDN}/vi_webp/{video_id}/{name}.webp"
    return f"{CDN}/vi/{video_id}/{name}.jpg"


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """`(width, height)` from the first SOF marker of a JPEG, or None if not a JPEG."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return (width, height) if width and height else None
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        i += 2 + length
    return None


def aspect_hint(width: int, height: int) -> str | None:
    ratio = width / height
    if ratio < 1:
        return "9:16"
    if ratio > 1.55:
        return "16:9"
    if 1.2 <= ratio <= 1.4:
        return "4:3"
    return None


async def _request(
    client: httpx.AsyncClient, method: str, url: str, *, stream: bool = False, **kw: Any
) -> httpx.Response:
    """One request, honouring a single `Retry-After` on 429 (§1.4 step 4).

    Every request to either host goes through here. `stream=True` leaves the body unread
    (caller must close it), so oversized bodies can be rejected before they are buffered.
    """
    resp = await client.send(client.build_request(method, url, **kw), stream=stream)
    if resp.status_code == 429:
        await resp.aclose()
        try:
            wait = min(float(resp.headers.get("retry-after", "2")), MAX_RETRY_AFTER)
        except ValueError:
            wait = 2.0
        await asyncio.sleep(wait)
        resp = await client.send(client.build_request(method, url, **kw), stream=stream)
    return resp


def _declared_over(resp: httpx.Response, cap: int) -> bool:
    try:
        return int(resp.headers.get("content-length") or 0) > cap
    except ValueError:
        return False


async def head_sizes(
    client: httpx.AsyncClient, video_id: str, names: tuple[str, ...] = PROBED
) -> dict[str, dict[str, Any]]:
    """HEAD every name; availability is the status code alone, never the body size."""

    async def one(name: str) -> tuple[str, dict[str, Any]]:
        resp = await _request(client, "HEAD", thumb_url(video_id, name))
        length = resp.headers.get("content-length")
        return name, {
            "available": resp.status_code == 200,
            "bytes": int(length) if resp.status_code == 200 and length else None,
            "etag": resp.headers.get("etag") if resp.status_code == 200 else None,
        }

    return dict(await asyncio.gather(*(one(n) for n in names)))


async def frame0_dimensions(client: httpx.AsyncClient, video_id: str) -> tuple[int, int] | None:
    """Width/height of `frame0.jpg`; None on 404, a network error or an unparseable body."""
    try:
        resp = await _request(client, "GET", thumb_url(video_id, "frame0"), stream=True)
        try:
            if resp.status_code != 200:
                return None
            buf = b""
            async for chunk in resp.aiter_bytes(8192):
                buf += chunk
                dims = jpeg_dimensions(buf)
                if dims or len(buf) >= FRAME0_MAX_BYTES:
                    return dims
            return jpeg_dimensions(buf)
        finally:
            await resp.aclose()
    except httpx.HTTPError:
        return None


async def fetch_oembed(client: httpx.AsyncClient, video_id: str) -> dict[str, Any] | None:
    """Title/author from the public oEmbed endpoint, always the `watch?v=` form.

    400/404 → None. 429 → one retry after `Retry-After` (inside `_request`); 5xx or a
    network error → one retry after 2 s; then None. Never a third party.
    """
    params = {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    for attempt in range(2):
        try:
            resp = await _request(client, "GET", OEMBED, params=params)
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
        if resp is not None and resp.status_code < 500:
            return None
        if attempt == 0:
            await asyncio.sleep(2)
    return None


async def _read_capped(resp: httpx.Response, cap: int) -> bytes | None:
    """The body of a streamed 200, or None once it exceeds `cap` (declared or actual bytes)."""
    if _declared_over(resp, cap):
        return None
    buf = bytearray()
    async for chunk in resp.aiter_bytes(65536):
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


async def download(client: httpx.AsyncClient, url: str) -> bytes | None:
    """GET one image. Transient 5xx → 3 retries with 1/2/4 s backoff; 404 → None.

    Bodies over MAX_IMAGE_BYTES are never buffered (the largest real thumbnail is ~300 KB).
    """
    for attempt in range(4):
        try:
            resp = await _request(client, "GET", url, stream=True)
            try:
                if resp.status_code == 200:
                    return await _read_capped(resp, MAX_IMAGE_BYTES)
            finally:
                await resp.aclose()
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code < 500:
            return None
        if attempt < 3:
            await asyncio.sleep(2**attempt)
    return None


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=False,
        timeout=15,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
        limits=httpx.Limits(max_connections=CONCURRENCY_LIMIT),
    )
