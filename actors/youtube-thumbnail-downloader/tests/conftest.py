"""Shared fixtures: a fake Actor (no platform) and a respx-mocked CDN."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from src import probe

FIXTURES = Path(__file__).parent / "fixtures"
GREY_404 = (FIXTURES / "grey_404.jpg").read_bytes()
FRAME0_16x9 = (FIXTURES / "frame0_16x9.jpg").read_bytes()
OEMBED_OK = {
    "title": "Never Gonna Give You Up",
    "author_name": "Rick Astley",
    "author_url": "https://www.youtube.com/@RickAstleyYT",
    "width": 200,
    "height": 113,
    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
}


@dataclass
class ChargeResult:
    event_charge_limit_reached: bool = False
    charged_count: int = 0


@dataclass
class PricingInfo:
    is_pay_per_event: bool


class FakeStore:
    id = "STORE123"


class FakeChargingManager:
    def __init__(self, limit: int | None, ppe: bool):
        self.limit = limit
        self.ppe = ppe

    def calculate_max_event_charge_count_within_limit(self, event: str) -> int | None:
        return self.limit

    def get_pricing_info(self) -> PricingInfo:
        return PricingInfo(is_pay_per_event=self.ppe)


class FakeActor:
    """Just enough of `apify.Actor` for `src.main.Run`; PPE priced at $1 per `video`.

    `ppe=False` models a local `apify run` / FREE-record run: nothing is ever charged and
    `calculate_max_event_charge_count_within_limit` returns None.
    """

    log = logging.getLogger("apify")

    def __init__(self, *, limit: int | None = None, fail_push: bool = False, ppe: bool = True):
        self.limit = limit
        self.fail_push = fail_push
        self.ppe = ppe
        self.pushed: list[tuple[dict[str, Any], str | None]] = []
        self.kv: dict[str, tuple[bytes, str | None]] = {}
        self.charged = 0

    def get_charging_manager(self) -> FakeChargingManager:
        return FakeChargingManager(self.limit, self.ppe)

    async def push_data(self, data: dict, *, charged_event_name: str | None = None) -> ChargeResult:
        if self.fail_push and charged_event_name:
            raise RuntimeError("dataset unavailable")
        if charged_event_name is None or not self.ppe:
            self.pushed.append((data, charged_event_name))
            return ChargeResult()
        if self.limit is not None and self.charged >= self.limit:
            return ChargeResult(event_charge_limit_reached=True, charged_count=0)
        self.pushed.append((data, charged_event_name))
        self.charged += 1
        return ChargeResult(
            event_charge_limit_reached=self.limit is not None and self.charged >= self.limit,
            charged_count=1,
        )

    async def set_value(self, key: str, value: bytes, *, content_type: str | None = None) -> None:
        self.kv[key] = (value, content_type)

    async def open_key_value_store(self) -> FakeStore:
        return FakeStore()

    def rows(self, status: str | None = None) -> list[dict[str, Any]]:
        return [r for r, _ in self.pushed if status is None or r["status"] == status]


def mock_video(
    router: respx.Router, video_id: str, available: set[str], *, oembed: Any = OEMBED_OK
):
    """Mock one video on the CDN: HEAD/GET per size, frame0 GET, oEmbed."""
    for name in (*probe.SIZES, *probe.OAR):
        ok = name in available
        router.head(probe.thumb_url(video_id, name)).mock(
            return_value=httpx.Response(
                200 if ok else 404,
                headers={"content-length": str(1000 + len(name)), "etag": f'"{name}-etag"'}
                if ok
                else {"content-length": str(len(GREY_404))},
            )
        )
        router.get(probe.thumb_url(video_id, name)).mock(
            return_value=httpx.Response(200, content=b"JPEGDATA-" + name.encode())
            if ok
            else httpx.Response(404, content=GREY_404)
        )
        router.get(probe.thumb_url(video_id, name, "webp")).mock(
            return_value=httpx.Response(200, content=b"WEBPDATA-" + name.encode())
            if ok
            else httpx.Response(404, content=GREY_404)
        )
    router.get(probe.thumb_url(video_id, "frame0")).mock(
        return_value=httpx.Response(200, content=FRAME0_16x9)
        if available
        else httpx.Response(404, content=GREY_404)
    )
    if oembed is not None:
        router.get(
            probe.OEMBED, params={"url": f"https://www.youtube.com/watch?v={video_id}"}
        ).mock(
            return_value=httpx.Response(200, json=oembed)
            if isinstance(oembed, dict)
            else httpx.Response(oembed)
        )


@pytest.fixture
def cdn():
    """A strict respx router: any request to an unmocked host or URL raises."""
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client():
    async with httpx.AsyncClient(headers={"User-Agent": probe.USER_AGENT}) as c:
        yield c


@pytest.fixture
def base_input():
    return {
        "videos": [],
        "sizes": ["best"],
        "format": "jpg",
        "includeMetadata": True,
        "saveImages": True,
        "maxVideos": 0,
    }
