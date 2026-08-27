"""SOF parser, HEAD matrix, oEmbed handling and the two forbidden-request rules (§1.7)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import FIXTURES, GREY_404, OEMBED_OK, mock_video

from src import probe

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    ("name", "dims", "hint", "vertical"),
    [
        ("frame0_16x9.jpg", (480, 268), "16:9", False),
        ("frame0_9x16.jpg", (268, 480), "9:16", True),
        ("frame0_4x3.jpg", (320, 240), "4:3", False),
    ],
)
def test_jpeg_dimensions_on_real_frame0(name, dims, hint, vertical):
    data = (FIXTURES / name).read_bytes()
    assert probe.jpeg_dimensions(data) == dims
    assert probe.aspect_hint(*dims) == hint
    assert (dims[1] > dims[0]) is vertical


def test_jpeg_dimensions_rejects_junk_and_truncated():
    assert probe.jpeg_dimensions(b"") is None
    assert probe.jpeg_dimensions(b"RIFF....WEBP") is None
    assert probe.jpeg_dimensions(b"\xff\xd8\xff\xe0\x00\x10JFIF") is None
    # Fill bytes before a marker are legal and must be skipped.
    sof = b"\xff\xd8\xff\xff\xff\xc0\x00\x11\x08\x01\x00\x02\x00\x03\x01\x22\x00\x02\x11\x01"
    sof += b"\x03\x11\x01"
    assert probe.jpeg_dimensions(sof) == (512, 256)
    assert probe.jpeg_dimensions(GREY_404) == (120, 90)


def test_aspect_hint_unknown_ratio():
    assert probe.aspect_hint(100, 70) is None


def test_thumb_urls():
    assert probe.thumb_url(ID, "maxresdefault") == f"https://i.ytimg.com/vi/{ID}/maxresdefault.jpg"
    assert probe.thumb_url(ID, "oar1", "webp") == f"https://i.ytimg.com/vi_webp/{ID}/oar1.webp"


async def test_head_matrix_uses_status_not_body(cdn, client):
    mock_video(cdn, ID, {"hqdefault", "mqdefault", "default", "oar1"})
    heads = await probe.head_sizes(client, ID)
    assert set(heads) == set(probe.PROBED)
    assert heads["maxresdefault"] == {"available": False, "bytes": None, "etag": None}
    assert heads["hqdefault"] == {"available": True, "bytes": 1009, "etag": '"hqdefault-etag"'}
    # No /vi_webp/ request of any kind: WebP is derived from the JPEG probe.
    assert not [c for c in cdn.calls if "/vi_webp/" in str(c.request.url)]
    assert all(c.request.method == "HEAD" for c in cdn.calls)


async def test_head_all_404(cdn, client):
    mock_video(cdn, "0lZNDRuNLKY", set())
    heads = await probe.head_sizes(client, "0lZNDRuNLKY")
    assert not any(h["available"] for h in heads.values())


async def test_head_honours_retry_after(cdn, client, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)
    route = cdn.head(probe.thumb_url(ID, "default"))
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "3"}),
        httpx.Response(200, headers={"content-length": "10", "etag": '"e"'}),
    ]
    heads = await probe.head_sizes(client, ID, ("default",))
    assert heads["default"]["available"] is True
    assert sleeps == [3.0]


async def test_frame0_404_returns_none_and_never_parses_grey(cdn, client):
    cdn.get(probe.thumb_url(ID, "frame0")).mock(return_value=httpx.Response(404, content=GREY_404))
    assert await probe.frame0_dimensions(client, ID) is None


async def test_frame0_dimensions(cdn, client):
    cdn.get(probe.thumb_url(ID, "frame0")).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "frame0_9x16.jpg").read_bytes())
    )
    assert await probe.frame0_dimensions(client, ID) == (268, 480)


async def test_oembed_watch_form_only(cdn, client):
    route = cdn.get(probe.OEMBED).mock(return_value=httpx.Response(200, json=OEMBED_OK))
    meta = await probe.fetch_oembed(client, ID)
    assert meta["author_name"] == "Rick Astley"
    url = str(route.calls[0].request.url)
    assert "watch%3Fv%3D" + ID in url and "%2Fembed%2F" not in url
    assert route.calls[0].request.url.host == "www.youtube.com"


@pytest.mark.parametrize("status", [400, 404])
async def test_oembed_client_error_is_null_without_retry(cdn, client, status):
    route = cdn.get(probe.OEMBED).mock(return_value=httpx.Response(status))
    assert await probe.fetch_oembed(client, ID) is None
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "headers", "expected_sleeps"),
    [(429, {"retry-after": "7"}, [7.0]), (429, {}, [2.0]), (500, {}, [2]), (503, {}, [2])],
)
async def test_oembed_transient_error_retries_once_then_null(
    cdn, client, monkeypatch, status, headers, expected_sleeps
):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)
    route = cdn.get(probe.OEMBED).mock(return_value=httpx.Response(status, headers=headers))
    assert await probe.fetch_oembed(client, ID) is None
    assert route.call_count == 2
    assert sleeps == expected_sleeps  # 429 honours Retry-After; 5xx waits the fixed 2 s
    # respx assert_all_mocked: had a third-party fallback (noembed.com) been contacted it
    # would have raised here.
    assert {c.request.url.host for c in cdn.calls} == {"www.youtube.com"}


async def test_oembed_429_then_success_honours_retry_after(cdn, client, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)
    route = cdn.get(probe.OEMBED)
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "5"}),
        httpx.Response(200, json=OEMBED_OK),
    ]
    assert (await probe.fetch_oembed(client, ID))["title"] == OEMBED_OK["title"]
    assert sleeps == [5.0]


async def test_frame0_429_honours_retry_after(cdn, client, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)
    route = cdn.get(probe.thumb_url(ID, "frame0"))
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "4"}),
        httpx.Response(200, content=(FIXTURES / "frame0_16x9.jpg").read_bytes()),
    ]
    assert await probe.frame0_dimensions(client, ID) == (480, 268)
    assert sleeps == [4.0]


async def test_frame0_network_error_is_none(cdn, client):
    cdn.get(probe.thumb_url(ID, "frame0")).mock(side_effect=httpx.ReadTimeout("slow"))
    assert await probe.frame0_dimensions(client, ID) is None


async def test_oembed_network_error_then_success(cdn, client, monkeypatch):
    async def no_sleep(s):
        return None

    monkeypatch.setattr(probe.asyncio, "sleep", no_sleep)
    route = cdn.get(probe.OEMBED)
    route.side_effect = [httpx.ConnectError("boom"), httpx.Response(200, json=OEMBED_OK)]
    assert (await probe.fetch_oembed(client, ID))["title"] == OEMBED_OK["title"]


async def test_oembed_bad_json_is_null(cdn, client):
    cdn.get(probe.OEMBED).mock(return_value=httpx.Response(200, content=b"<html>"))
    assert await probe.fetch_oembed(client, ID) is None


async def test_download_retries_5xx_with_backoff(cdn, client, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)
    route = cdn.get(probe.thumb_url(ID, "default"))
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(502),
        httpx.Response(200, content=b"ok"),
    ]
    assert await probe.download(client, probe.thumb_url(ID, "default")) == b"ok"
    assert sleeps == [1, 2]


async def test_download_gives_up_after_three_retries(cdn, client, monkeypatch):
    async def no_sleep(s):
        return None

    monkeypatch.setattr(probe.asyncio, "sleep", no_sleep)
    route = cdn.get(probe.thumb_url(ID, "default")).mock(return_value=httpx.Response(500))
    assert await probe.download(client, probe.thumb_url(ID, "default")) is None
    assert route.call_count == 4


async def test_download_404_is_none_not_grey_body(cdn, client):
    cdn.get(probe.thumb_url(ID, "sddefault")).mock(
        return_value=httpx.Response(404, content=GREY_404)
    )
    assert await probe.download(client, probe.thumb_url(ID, "sddefault")) is None


@pytest.mark.parametrize("declared", [True, False])
async def test_download_rejects_oversized_body(cdn, client, declared):
    big = b"x" * (probe.MAX_IMAGE_BYTES + 1)
    headers = {"content-length": str(len(big))} if declared else {}
    cdn.get(probe.thumb_url(ID, "default")).mock(
        return_value=httpx.Response(200, content=big, headers=headers)
    )
    assert await probe.download(client, probe.thumb_url(ID, "default")) is None


async def test_download_accepts_body_at_cap(cdn, client):
    body = b"x" * probe.MAX_IMAGE_BYTES
    cdn.get(probe.thumb_url(ID, "default")).mock(return_value=httpx.Response(200, content=body))
    assert await probe.download(client, probe.thumb_url(ID, "default")) == body


def test_client_identity():
    c = probe.make_client()
    try:
        assert c.headers["user-agent"] == probe.USER_AGENT
        assert "mooniegilog@gmail.com" in probe.USER_AGENT
        assert probe.CONCURRENCY_LIMIT == 10
        assert c._transport._pool._max_connections == probe.CONCURRENCY_LIMIT
    finally:
        asyncio.run(c.aclose())


async def test_at_most_ten_requests_in_flight_to_cdn(monkeypatch, unused_tcp_port):
    """§1.4 step 4 / §1.8: ≤10 concurrent to i.ytimg.com — measured against a real socket
    server, because respx bypasses the connection pool that enforces it."""
    in_flight = peak = 0

    async def handle(reader, writer):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.sleep(0.05)
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
        await writer.drain()
        in_flight -= 1
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", unused_tcp_port)
    monkeypatch.setattr(probe, "CDN", f"http://127.0.0.1:{unused_tcp_port}")
    ids = [ID, "jNQXAC9IVRw", "029uWKeBdEo", "9bZkp7q19f0", "kJQP7kiw5Fk"]
    try:
        async with probe.make_client() as c:
            heads = await asyncio.gather(*(probe.head_sizes(c, vid) for vid in ids))
    finally:
        server.close()
        await server.wait_closed()
    assert all(h["available"] for hs in heads for h in hs.values())  # 30 requests made
    assert 1 < peak <= probe.CONCURRENCY_LIMIT


def test_no_forbidden_hosts_in_source():
    src = "".join(p.read_text() for p in (FIXTURES.parent.parent / "src").glob("*.py"))
    for needle in ("youtubei", "feeds/videos.xml", "get_video_info", "googleapis", "noembed"):
        assert needle not in src, needle
