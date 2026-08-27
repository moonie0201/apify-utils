"""Live contract tests against i.ytimg.com and youtube.com/oembed (§1.7). `pytest -m live`."""

from __future__ import annotations

import pytest
from conftest import FakeActor

from src import main, probe

pytestmark = pytest.mark.live

PREFILL = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/9bZkp7q19f0?si=abc",
    "https://www.youtube.com/shorts/029uWKeBdEo",
    "jNQXAC9IVRw",
]


@pytest.fixture
async def live_client():
    async with probe.make_client() as c:
        yield c


async def test_prefill_plus_dead_id(live_client):
    actor = FakeActor()
    inp = {
        "videos": [*PREFILL, "0lZNDRuNLKY"],
        "sizes": ["best"],
        "format": "jpg",
        "includeMetadata": True,
        "saveImages": False,
        "maxVideos": 0,
    }
    budget = await main.Run(inp, actor=actor, client=live_client).run()
    rows = {r["videoId"]: r for r in actor.rows()}
    assert budget.charged == 4 and rows["0lZNDRuNLKY"]["status"] == "not_found"

    rick = rows["dQw4w9WgXcQ"]
    assert rick["status"] == "ok" and rick["availableSizes"] == [*probe.SIZES, "oar1"]
    assert rick["best"]["size"] == "maxresdefault" and rick["authorName"] == "Rick Astley"
    assert rick["aspectHint"] == "16:9" and rick["isVertical"] is False

    old = rows["jNQXAC9IVRw"]
    assert old["thumbnails"]["maxresdefault"]["available"] is False
    assert old["thumbnails"]["sddefault"]["available"] is False
    assert old["best"]["size"] == "hqdefault" and old["aspectHint"] == "4:3"

    short = rows["029uWKeBdEo"]
    assert short["isVertical"] is True and short["aspectHint"] == "9:16"


async def test_oembed_watch_200_embed_404(live_client):
    meta = await probe.fetch_oembed(live_client, "dQw4w9WgXcQ")
    assert meta and meta["author_name"] == "Rick Astley"
    resp = await live_client.get(
        probe.OEMBED,
        params={"url": "https://www.youtube.com/embed/dQw4w9WgXcQ", "format": "json"},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("video_id", ["dQw4w9WgXcQ", "029uWKeBdEo", "jNQXAC9IVRw"])
async def test_webp_etag_equals_jpg_etag(live_client, video_id):
    jpg = await live_client.head(probe.thumb_url(video_id, "hqdefault"))
    webp = await live_client.head(probe.thumb_url(video_id, "hqdefault", "webp"))
    assert jpg.status_code == webp.status_code == 200
    assert jpg.headers["etag"] == webp.headers["etag"]


async def test_404_is_a_real_status(live_client):
    resp = await live_client.head(probe.thumb_url("0lZNDRuNLKY", "maxresdefault"))
    assert resp.status_code == 404
