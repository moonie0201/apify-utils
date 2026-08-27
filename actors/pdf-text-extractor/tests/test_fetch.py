import asyncio
import logging

import httpx
import pytest
import respx
from conftest import PUBLIC_IP, fixture_url
from src import fetch
from src.fetch import HostGate, download, file_name_for, make_client

PDF = b"%PDF-1.7\n" + b"x" * 2000


async def _download(url, work_dir, cap=50 * 1024 * 1024, gate=None):
    async with make_client() as client:
        return await download(
            client, url, gate=gate or HostGate(), cap_bytes=cap, work_dir=work_dir
        )


@respx.mock
async def test_request_goes_to_pinned_ip_with_host_and_sni(resolver, no_wait, work_dir):
    route = respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    result = await _download(fixture_url("a.pdf"), work_dir)
    assert result.error_code is None
    request = route.calls[0].request
    assert request.url.host == PUBLIC_IP
    assert request.headers["host"] == "files.test"
    assert request.extensions["sni_hostname"] == "files.test"
    assert request.headers["user-agent"].startswith("pdf-text-extractor/")
    assert result.path.name == f"{result.document_id}.pdf"
    assert result.bytes == len(PDF)
    result.path.unlink()


@respx.mock
async def test_content_length_over_cap_refused_before_body(resolver, no_wait, work_dir):
    respx.get(f"https://{PUBLIC_IP}/big.pdf").mock(
        return_value=httpx.Response(200, headers={"content-length": "999999"}, content=PDF)
    )
    result = await _download(fixture_url("big.pdf"), work_dir, cap=1000)
    assert result.error_code == "too_large"
    assert list(work_dir.iterdir()) == []


@respx.mock
async def test_stream_overrun_aborts(resolver, no_wait, work_dir):
    body = b"%PDF-1.7\n" + b"y" * 5000
    respx.get(f"https://{PUBLIC_IP}/big.pdf").mock(return_value=httpx.Response(200, content=body))
    result = await _download(fixture_url("big.pdf"), work_dir, cap=3000)
    assert result.error_code == "too_large"
    assert list(work_dir.iterdir()) == []


@respx.mock
async def test_html_is_not_pdf(resolver, no_wait, work_dir):
    html = b"<!doctype html><html><body>Google Docs viewer</body></html>" * 40
    respx.get(f"https://{PUBLIC_IP}/view").mock(
        return_value=httpx.Response(200, content=html, headers={"content-type": "application/pdf"})
    )
    result = await _download(fixture_url("view"), work_dir)
    assert result.error_code == "not_pdf"
    assert result.content_type == "application/pdf"
    assert list(work_dir.iterdir()) == []


@respx.mock
async def test_short_body_still_checked(resolver, no_wait, work_dir):
    respx.get(f"https://{PUBLIC_IP}/tiny").mock(return_value=httpx.Response(200, content=b"nope"))
    result = await _download(fixture_url("tiny"), work_dir)
    assert result.error_code == "not_pdf"


@respx.mock
async def test_redirect_to_private_blocked(resolver, no_wait, work_dir):
    respx.get(f"https://{PUBLIC_IP}/r").mock(
        return_value=httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )
    )
    result = await _download(fixture_url("r"), work_dir)
    assert result.error_code == "blocked_url"


@respx.mock
async def test_https_to_http_downgrade_blocked(resolver, no_wait, work_dir):
    respx.get(f"https://{PUBLIC_IP}/r").mock(
        return_value=httpx.Response(302, headers={"location": "http://files.test/a.pdf"})
    )
    plain = respx.get(f"http://{PUBLIC_IP}/a.pdf").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    result = await _download(fixture_url("r"), work_dir)
    assert result.error_code == "blocked_url"
    assert not plain.called


@respx.mock
async def test_redirect_followed_and_repinned(resolver, no_wait, work_dir):
    respx.get(f"https://{PUBLIC_IP}/r").mock(
        return_value=httpx.Response(301, headers={"location": "https://other.test/final.pdf"})
    )
    final = respx.get("https://93.184.216.35/final.pdf").mock(
        return_value=httpx.Response(
            200, content=PDF, headers={"content-disposition": 'attachment; filename="report.pdf"'}
        )
    )
    result = await _download(fixture_url("r"), work_dir)
    assert result.error_code is None
    assert result.final_url == "https://other.test/final.pdf"
    assert result.file_name == "report.pdf"
    assert final.calls[0].request.headers["host"] == "other.test"
    result.path.unlink()


@respx.mock
async def test_set_cookie_is_never_replayed(resolver, no_wait, work_dir):
    respx.get(f"https://{PUBLIC_IP}/r").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://files.test/a.pdf", "set-cookie": "sid=SECRET"}
        )
    )
    final = respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"set-cookie": "sid=SECRET"})
    )
    async with make_client() as client:
        first = await download(
            client, fixture_url("r"), gate=HostGate(), cap_bytes=10**6, work_dir=work_dir
        )
        second = await download(
            client, fixture_url("a.pdf"), gate=HostGate(), cap_bytes=10**6, work_dir=work_dir
        )
    assert first.error_code is None and second.error_code == "duplicate"
    assert final.call_count == 2
    assert all("cookie" not in call.request.headers for call in final.calls)
    first.path.unlink()


@respx.mock
async def test_non_printable_char_in_url_is_blocked_row(resolver, no_wait, work_dir):
    route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, content=PDF))
    result = await _download("http://1.1.1.1/a b/\x7f?q=é", work_dir)
    assert result.error_code == "blocked_url"
    assert not route.called


@respx.mock
async def test_slow_headers_hit_the_hop_wall(resolver, no_wait, work_dir, monkeypatch):
    monkeypatch.setattr(fetch, "HOP_WALL", 0.05)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, content=PDF)

    respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(side_effect=slow)
    result = await _download(fixture_url("a.pdf"), work_dir)
    assert result.error_code == "timeout"
    assert list(work_dir.iterdir()) == []


@respx.mock
async def test_trickling_body_hits_the_hop_wall_and_leaves_no_part(
    resolver, no_wait, work_dir, monkeypatch
):
    monkeypatch.setattr(fetch, "HOP_WALL", 0.05)

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"%PDF-1.7\n" + b"x" * 2000
            await asyncio.sleep(
                5
            )  # one byte every few seconds: httpx's per-read timeout never fires
            yield b"y"

    respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(return_value=httpx.Response(200, stream=Trickle()))
    result = await _download(fixture_url("a.pdf"), work_dir)
    assert result.error_code == "timeout" and result.http_status == 200
    assert list(work_dir.iterdir()) == []  # no .part left behind by the cancellation


@respx.mock
async def test_host_slot_held_until_body_is_on_disk(resolver, no_wait, work_dir):
    events: list[str] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            events.append("body-start")
            for chunk in (b"%PDF-1.7\n", b"x" * 2000):
                await asyncio.sleep(0)
                yield chunk
            events.append("body-end")

    def serve(request):
        events.append("request")
        return httpx.Response(200, stream=Body())

    respx.get(url__regex=rf"https://{PUBLIC_IP}/.*").mock(side_effect=serve)
    gate = HostGate()
    async with make_client() as client:
        first, second = await asyncio.gather(
            download(client, fixture_url("1.pdf"), gate=gate, cap_bytes=10**6, work_dir=work_dir),
            download(client, fixture_url("2.pdf"), gate=gate, cap_bytes=10**6, work_dir=work_dir),
        )
    assert events == ["request", "body-start", "body-end"] * 2  # never interleaved
    assert first.error_code is None and second.error_code == "duplicate"
    first.path.unlink()


@respx.mock
async def test_no_keepalive_and_own_sni_per_host(resolver, no_wait, work_dir):
    resolver["other.test"] = PUBLIC_IP  # two hostnames behind one CDN address
    route = respx.get(url__regex=rf"https://{PUBLIC_IP}/.*").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    async with make_client() as client:
        assert client._transport._pool._max_keepalive_connections == 0
        for host in ("files.test", "other.test"):
            result = await download(
                client,
                fixture_url("a.pdf", host),
                gate=HostGate(),
                cap_bytes=10**6,
                work_dir=work_dir,
            )
            if result.path:
                result.path.unlink()
    sni = [c.request.extensions["sni_hostname"] for c in route.calls]
    hosts = [c.request.headers["host"] for c in route.calls]
    assert sni == hosts == ["files.test", "other.test"]


@respx.mock
async def test_too_many_redirects(resolver, no_wait, work_dir):
    respx.get(url__regex=rf"https://{PUBLIC_IP}/loop.*").mock(
        return_value=httpx.Response(302, headers={"location": "https://files.test/loop"})
    )
    result = await _download(fixture_url("loop"), work_dir)
    assert result.error_code == "download_failed"


@pytest.mark.parametrize("status", [401, 403, 429, 404])
@respx.mock
async def test_client_errors_no_retry(resolver, no_wait, work_dir, status):
    route = respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(
        return_value=httpx.Response(status, content=b"no")
    )
    result = await _download(fixture_url("a.pdf"), work_dir)
    assert result.error_code == "download_failed"
    assert result.http_status == status
    assert route.call_count == 1
    assert no_wait.sleeps == []


@respx.mock
async def test_server_error_retried_twice(resolver, no_wait, work_dir):
    route = respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, content=PDF)]
    )
    result = await _download(fixture_url("a.pdf"), work_dir)
    assert result.error_code is None
    assert route.call_count == 3
    assert [s for s in no_wait.sleeps if s >= 2] == [2.0, 4.0]
    result.path.unlink()


@respx.mock
async def test_timeout_retried_then_fails(resolver, no_wait, work_dir):
    route = respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(side_effect=httpx.ConnectTimeout("t"))
    result = await _download(fixture_url("a.pdf"), work_dir)
    assert result.error_code == "download_failed"
    assert route.call_count == 3


@respx.mock
async def test_same_host_gap_and_one_in_flight(resolver, no_wait, work_dir):
    respx.get(url__regex=rf"https://{PUBLIC_IP}/.*").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    gate = HostGate()
    async with make_client() as client:
        first = await download(
            client, fixture_url("1.pdf"), gate=gate, cap_bytes=10**6, work_dir=work_dir
        )
        second = await download(
            client, fixture_url("2.pdf"), gate=gate, cap_bytes=10**6, work_dir=work_dir
        )
    assert first.error_code is None
    assert second.error_code == "duplicate"  # same bytes → same documentId, atomic link refused
    assert no_wait.sleeps and abs(no_wait.sleeps[0] - fetch.HOST_GAP) < 1e-6
    first.path.unlink()


async def test_gate_serialises_same_host(no_wait):
    gate = HostGate()
    order = []
    started = asyncio.Event()

    async def first():
        async with gate.slot("h"):
            order.append("first-in")
            started.set()
            await asyncio.sleep(0)
            order.append("first-out")

    async def second():
        await started.wait()
        async with gate.slot("h"):
            order.append("second-in")

    await asyncio.gather(first(), second())
    assert order == ["first-in", "first-out", "second-in"]


async def test_gate_other_host_not_delayed(no_wait):
    gate = HostGate()
    async with gate.slot("a"):
        pass
    async with gate.slot("b"):
        pass
    assert no_wait.sleeps == []


def test_file_name_for():
    assert file_name_for("https://x.test/dir/report%20v2.pdf?x=1", None) == "report v2.pdf"
    assert file_name_for("https://x.test/", None) == "document.pdf"
    assert file_name_for("https://x.test/a.pdf", 'inline; filename="../../evil.pdf"') == "evil.pdf"
    assert (
        file_name_for("https://x.test/a.pdf", "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
        == "résumé.pdf"
    )


@respx.mock
async def test_logs_never_contain_url_or_error_text(resolver, no_wait, work_dir, caplog):
    caplog.set_level(logging.INFO)
    secret = "presigned-token-XYZ"
    respx.get(f"https://{PUBLIC_IP}/a.pdf").mock(
        side_effect=httpx.ConnectError(f"boom https://files.test/a.pdf?{secret}")
    )
    result = await _download(fixture_url(f"a.pdf?{secret}"), work_dir)
    assert result.error_code == "download_failed"
    assert secret not in caplog.text
    assert "a.pdf" not in caplog.text
    assert "ConnectError" in caplog.text
