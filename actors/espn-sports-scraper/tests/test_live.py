"""Live probes against espn.com. Excluded by default (``-m "not live"``); run with ``-m live``.
They document the feed's behaviour on the day they were run, they are not a bypass."""

from __future__ import annotations

import httpx
import pytest
from src.client import CORE, SITE, USER_AGENT, EdgeBlocked, EspnClient, EspnError

pytestmark = pytest.mark.live

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TOOL_UA_WITH_CONTACT = "apify-utils/espn-sports-scraper/0.1 (+mailto:mooniegilog@gmail.com)"


async def test_shipped_ua_gets_200():
    client = EspnClient()
    try:
        doc = await client.get_json(SITE + "football/nfl/scoreboard")
    finally:
        await client.aclose()
    assert "events" in doc and client.requests == 1


@pytest.mark.parametrize("ua", [BROWSER_UA, TOOL_UA_WITH_CONTACT])
async def test_other_user_agents_are_refused(ua):
    """Documents the rule: a browser UA and the contact-style tool UA answer 403.

    The Actor never sends either of them."""
    async with httpx.AsyncClient(headers={"User-Agent": ua}, timeout=30) as http:
        resp = await http.get(SITE + "football/nfl/scoreboard")
    assert resp.status_code == 403


async def test_403_path_raises_edge_blocked():
    client = EspnClient(httpx.AsyncClient(timeout=30))
    client.http.headers["User-Agent"] = TOOL_UA_WITH_CONTACT
    try:
        with pytest.raises(EdgeBlocked):
            await client.get_json(SITE + "football/nfl/scoreboard")
    finally:
        await client.aclose()
    # the only UA the shipped client sends is httpx's own
    assert USER_AGENT.startswith("python-httpx/")


async def test_nba_january_2025_has_232_events_with_limit():
    client = EspnClient()
    try:
        doc = await client.get_json(
            SITE + "basketball/nba/scoreboard", {"dates": "20250101-20250131", "limit": 1000}
        )
    finally:
        await client.aclose()
    assert len(doc["events"]) == 232


async def test_core_nba_teams_2025_is_30():
    client = EspnClient()
    try:
        doc = await client.get_json(
            CORE + "basketball/leagues/nba/seasons/2025/teams", {"limit": 200}
        )
    finally:
        await client.aclose()
    assert doc["count"] == 30 and len(doc["items"]) == 30


async def test_atp_wimbledon_day_has_groupings():
    client = EspnClient()
    try:
        doc = await client.get_json(SITE + "tennis/atp/scoreboard", {"dates": "20250706"})
    finally:
        await client.aclose()
    assert doc["events"] and doc["events"][0]["groupings"]


async def test_unknown_league_is_400():
    client = EspnClient()
    try:
        with pytest.raises(EspnError) as exc:
            await client.get_json(
                SITE + "soccer/xxx.9/scoreboard", {"dates": "20250101", "limit": 1000}
            )
    finally:
        await client.aclose()
    assert exc.value.status == 400


async def test_mlb_2001_opening_day_is_13_events():
    client = EspnClient()
    try:
        doc = await client.get_json(
            SITE + "baseball/mlb/scoreboard", {"dates": "20010405", "limit": 1000}
        )
    finally:
        await client.aclose()
    assert len(doc["events"]) == 13
