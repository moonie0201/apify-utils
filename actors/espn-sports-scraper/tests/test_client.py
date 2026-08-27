import httpx
import pytest
import respx
from src.client import SITE, USER_AGENT, EdgeBlocked, EspnClient, EspnError

URL = SITE + "basketball/nba/scoreboard"


@pytest.fixture
def fast_sleep(monkeypatch):
    sleeps: list[float] = []

    async def _sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("src.client.asyncio.sleep", _sleep)
    return sleeps


def test_user_agent_is_httpx_default_and_the_only_one():
    assert USER_AGENT == f"python-httpx/{httpx.__version__}"
    assert "Mozilla" not in USER_AGENT and "curl" not in USER_AGENT


@respx.mock
async def test_every_request_carries_the_one_ua():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"events": []}))
    client = EspnClient()
    try:
        await client.get_json(URL, {"limit": 1000})
        await client.get_json(URL, {"limit": 1000, "dates": "20250101"})
    finally:
        await client.aclose()
    assert route.call_count == 2
    for call in route.calls:
        assert call.request.headers["user-agent"] == USER_AGENT
    assert "limit=1000" in str(route.calls[0].request.url)


@respx.mock
async def test_403_raises_edge_blocked_after_exactly_one_request(fx):
    html = open("tests/fixtures/forbidden_403.html").read()
    route = respx.get(URL).mock(
        return_value=httpx.Response(403, text=html, headers={"content-type": "text/html"})
    )
    client = EspnClient()
    with pytest.raises(EdgeBlocked):
        await client.get_json(URL)
    await client.aclose()
    assert route.call_count == 1 and client.requests == 1
    assert route.calls[0].request.headers["user-agent"] == USER_AGENT


@respx.mock
async def test_400_and_404_become_espn_error_with_message(fx):
    respx.get(SITE + "soccer/xxx.9/scoreboard").mock(
        return_value=httpx.Response(400, json=fx("league_400.json"))
    )
    respx.get(SITE + "football/nfl/summary").mock(
        return_value=httpx.Response(404, json=fx("summary_404.json"))
    )
    client = EspnClient()
    with pytest.raises(EspnError) as e400:
        await client.get_json(SITE + "soccer/xxx.9/scoreboard", {"dates": "20250101"})
    with pytest.raises(EspnError) as e404:
        await client.get_json(SITE + "football/nfl/summary", {"event": "1"})
    await client.aclose()
    assert e400.value.status == 400 and "Failed to get events endpoint" in e400.value.message
    assert e404.value.status == 404 and "404" in e404.value.message


@respx.mock
async def test_non_json_400_body_is_an_error_row_not_a_crash():
    respx.get(URL).mock(
        return_value=httpx.Response(400, text="Bad Request", headers={"content-type": "text/plain"})
    )
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json(URL)
    await client.aclose()
    assert exc.value.message == "HTTP 400"


@respx.mock
async def test_5xx_retried_twice_then_error(fast_sleep):
    route = respx.get(URL).mock(return_value=httpx.Response(502))
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json(URL)
    await client.aclose()
    assert route.call_count == 3 and fast_sleep == [1.0, 3.0]
    assert "HTTP 502" in exc.value.message


@respx.mock
async def test_5xx_then_success(fast_sleep):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    client = EspnClient()
    assert await client.get_json(URL) == {"ok": 1}
    await client.aclose()
    assert route.call_count == 2 and fast_sleep == [1.0]


@respx.mock
async def test_timeout_retried(fast_sleep):
    route = respx.get(URL).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": 1})]
    )
    client = EspnClient()
    assert await client.get_json(URL) == {"ok": 1}
    await client.aclose()
    assert route.call_count == 2


@respx.mock
async def test_429_honours_retry_after(fast_sleep):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429),
            httpx.Response(200, json={}),
        ]
    )
    client = EspnClient()
    assert await client.get_json(URL) == {}
    await client.aclose()
    assert route.call_count == 3
    assert fast_sleep == [7.0, 1.0, 10.0, 3.0]  # retry-after, backoff, default 10 s, backoff


@respx.mock
async def test_non_json_200_is_error():
    respx.get(URL).mock(
        return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    )
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json(URL)
    await client.aclose()
    assert "non-JSON" in exc.value.message


@respx.mock
async def test_body_over_32mb_is_dropped_while_streaming():
    chunk = b" " * (1024 * 1024)
    served: list[int] = []

    async def chunks():
        for i in range(40):
            served.append(i)
            yield chunk

    respx.get(URL).mock(return_value=httpx.Response(200, content=chunks()))
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json(URL)
    await client.aclose()
    assert "32 MB" in exc.value.message
    assert len(served) < 40  # stopped as the cap was crossed, not after the whole body


@respx.mock
async def test_declared_oversize_body_is_never_read():
    async def never():
        raise AssertionError("body must not be read")
        yield b""  # pragma: no cover

    headers = {"content-length": "99999999"}
    respx.get(URL).mock(return_value=httpx.Response(200, headers=headers, content=never()))
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json(URL)
    await client.aclose()
    assert "32 MB" in exc.value.message


@respx.mock
async def test_non_object_json_is_error():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json(URL)
    await client.aclose()
    assert exc.value.message == "unexpected payload shape"


async def test_refuses_other_hosts():
    client = EspnClient()
    with pytest.raises(EspnError) as exc:
        await client.get_json("https://www.espn.com/nfl/scoreboard")
    await client.aclose()
    assert "refusing" in exc.value.message and client.requests == 0
