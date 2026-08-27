"""End-to-end run against a mocked CDN: rows, selection, charging, budget, KV writes (§1.7)."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from conftest import GREY_404, ChargeResult, FakeActor, mock_video

from src import main, probe

ID = "dQw4w9WgXcQ"
OLD = "jNQXAC9IVRw"
DEAD = "0lZNDRuNLKY"
ALL5 = set(probe.SIZES)


async def run(cdn, client, base_input, actor=None, **overrides):
    actor = actor or FakeActor()
    inp = {**base_input, **overrides}
    budget = await main.Run(inp, actor=actor, client=client).run()
    return actor, budget


async def test_ok_row_shape(cdn, client, base_input):
    mock_video(cdn, ID, ALL5 | {"oar1"})
    actor, budget = await run(cdn, client, base_input, videos=[f"https://youtu.be/{ID}?si=x"])
    (row,) = actor.rows()
    assert row["recordType"] == "video" and row["status"] == "ok"
    assert row["videoId"] == ID and row["inputUrl"] == f"https://youtu.be/{ID}?si=x"
    assert row["canonicalUrl"] == f"https://www.youtube.com/watch?v={ID}"
    assert row["title"] == "Never Gonna Give You Up"
    assert row["authorName"] == "Rick Astley" and row["metadataSource"] == "oembed"
    assert row["isVertical"] is False and row["aspectHint"] == "16:9"
    assert row["best"]["size"] == "maxresdefault"
    assert row["best"]["width"] == 1280 and row["best"]["height"] == 720
    assert row["best"]["webpUrl"] == f"https://i.ytimg.com/vi_webp/{ID}/maxresdefault.webp"
    assert row["availableSizes"] == [*probe.SIZES, "oar1"]
    assert set(row["thumbnails"]) == set(probe.PROBED)
    assert row["thumbnails"]["oar1"]["width"] is None  # not downloaded, so not measured
    assert row["files"] == [
        {
            "size": "maxresdefault",
            "format": "jpg",
            "key": f"{ID}_maxresdefault.jpg",
            "storeId": "STORE123",
            "url": f"https://api.apify.com/v2/key-value-stores/STORE123/records/{ID}_maxresdefault.jpg",
        }
    ]
    assert actor.kv[f"{ID}_maxresdefault.jpg"] == (b"JPEGDATA-maxresdefault", "image/jpeg")
    assert row["fetchedAt"].endswith("Z") and row["errorMessage"] is None
    assert budget.delivered == 1 and budget.charged == 1
    assert [e for _, e in actor.pushed] == ["video"]


async def test_best_falls_back_and_oar1_404_tolerated(cdn, client, base_input):
    mock_video(cdn, OLD, {"hqdefault", "mqdefault", "default"})
    actor, _ = await run(cdn, client, base_input, videos=[OLD])
    (row,) = actor.rows()
    assert row["status"] == "ok" and row["best"]["size"] == "hqdefault"
    assert row["availableSizes"] == ["hqdefault", "mqdefault", "default"]
    assert row["thumbnails"]["maxresdefault"]["available"] is False
    assert row["thumbnails"]["oar1"]["available"] is False
    assert list(actor.kv) == [f"{OLD}_hqdefault.jpg"]


async def test_not_found_is_free_and_grey_jpeg_never_saved(cdn, client, base_input):
    mock_video(cdn, DEAD, set())
    actor, budget = await run(cdn, client, base_input, videos=[DEAD])
    (row,) = actor.rows()
    assert row["status"] == "not_found" and row["recordType"] == "error"
    assert actor.kv == {} and budget.charged == 0 and budget.delivered == 0
    assert [e for _, e in actor.pushed] == [None]
    assert all(c.request.method == "HEAD" for c in cdn.calls)  # no frame0, no oEmbed
    assert not any(v[0] == GREY_404 for v in actor.kv.values())


async def test_free_rows_for_invalid_playlist_duplicate(cdn, client, base_input):
    mock_video(cdn, ID, ALL5)
    videos = [ID, "https://www.youtube.com/playlist?list=PL1", "junk", f"https://youtu.be/{ID}"]
    actor, budget = await run(cdn, client, base_input, videos=videos)
    statuses = {r["inputUrl"]: r["status"] for r in actor.rows()}
    assert statuses == {
        ID: "ok",
        "https://www.youtube.com/playlist?list=PL1": "playlist_not_supported",
        "junk": "invalid_input",
        f"https://youtu.be/{ID}": "duplicate",
    }
    assert [e for _, e in actor.pushed].count("video") == 1
    assert budget.free == 3 and budget.charged == 1


async def test_all_sizes_both_formats(cdn, client, base_input):
    mock_video(cdn, ID, ALL5)
    actor, _ = await run(cdn, client, base_input, videos=[ID], sizes=["all"], format="both")
    assert len(actor.kv) == 10
    assert actor.kv[f"{ID}_default.webp"] == (b"WEBPDATA-default", "image/webp")
    (row,) = actor.rows()
    assert {f["format"] for f in row["files"]} == {"jpg", "webp"}


async def test_oar_selection_probes_oar2_oar3_and_measures(cdn, client, base_input):
    mock_video(cdn, ID, ALL5 | {"oar1", "oar2"})
    oar_jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08\x04\x38\x07\x80\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    cdn.get(probe.thumb_url(ID, "oar1")).mock(return_value=httpx.Response(200, content=oar_jpeg))
    actor, _ = await run(cdn, client, base_input, videos=[ID], sizes=["best", "oar"])
    (row,) = actor.rows()
    assert row["thumbnails"]["oar1"] == {
        "available": True,
        "bytes": 1004,
        "etag": '"oar1-etag"',
        "url": probe.thumb_url(ID, "oar1"),
        "webpUrl": probe.thumb_url(ID, "oar1", "webp"),
        "width": 1920,
        "height": 1080,
    }
    assert row["thumbnails"]["oar2"]["available"] is True
    assert row["thumbnails"]["oar3"]["available"] is False
    assert row["availableSizes"] == [*probe.SIZES, "oar1", "oar2"]
    assert sorted(actor.kv) == [f"{ID}_maxresdefault.jpg", f"{ID}_oar1.jpg", f"{ID}_oar2.jpg"]
    # oar3 answered 404 on HEAD, so it is neither fetched nor reported as a failure.
    assert row["errorMessage"] is None
    assert not [c for c in cdn.calls if c.request.method == "GET" and "oar3" in str(c.request.url)]


async def test_save_off_urls_only_same_row(cdn, client, base_input):
    mock_video(cdn, ID, ALL5)
    actor, budget = await run(cdn, client, base_input, videos=[ID], saveImages=False)
    (row,) = actor.rows()
    assert row["status"] == "ok" and row["files"] == [] and actor.kv == {}
    assert budget.charged == 1
    assert not [
        c
        for c in cdn.calls
        if c.request.method == "GET"
        and "/vi/" in str(c.request.url)
        and "frame0" not in str(c.request.url)
    ]


async def test_metadata_off_skips_oembed(cdn, client, base_input):
    mock_video(cdn, ID, ALL5, oembed=None)
    actor, _ = await run(cdn, client, base_input, videos=[ID], includeMetadata=False)
    (row,) = actor.rows()
    assert row["title"] is None and row["metadataSource"] is None
    assert not [c for c in cdn.calls if c.request.url.host == "www.youtube.com"]


async def test_oembed_404_row_still_ok(cdn, client, base_input):
    mock_video(cdn, ID, ALL5, oembed=404)
    actor, budget = await run(cdn, client, base_input, videos=[ID])
    (row,) = actor.rows()
    assert row["status"] == "ok" and row["title"] is None and row["authorName"] is None
    assert budget.charged == 1


async def test_oembed_4x3_hint_when_frame0_missing(cdn, client, base_input):
    mock_video(cdn, OLD, {"hqdefault"}, oembed={"title": "t", "width": 200, "height": 150})
    cdn.get(probe.thumb_url(OLD, "frame0")).mock(return_value=httpx.Response(404, content=GREY_404))
    actor, _ = await run(cdn, client, base_input, videos=[OLD])
    (row,) = actor.rows()
    assert row["isVertical"] is None and row["aspectHint"] == "4:3"


async def test_failed_file_download_keeps_row_ok(cdn, client, base_input, monkeypatch):
    async def no_sleep(s):
        return None

    monkeypatch.setattr(probe.asyncio, "sleep", no_sleep)
    mock_video(cdn, ID, ALL5)
    cdn.get(probe.thumb_url(ID, "maxresdefault")).mock(return_value=httpx.Response(503))
    actor, budget = await run(cdn, client, base_input, videos=[ID])
    (row,) = actor.rows()
    assert row["status"] == "ok" and row["files"] == [] and budget.charged == 1
    assert row["errorMessage"] == "maxresdefault.jpg not saved (CDN did not serve it)"


async def test_store_write_failure_keeps_row_ok_and_charged(cdn, client, base_input):
    """A KV write that fails after the client's own retries drops the file, not the row."""
    mock_video(cdn, ID, ALL5)
    actor = FakeActor()

    async def broken_set_value(key, value, *, content_type=None):
        raise RuntimeError("429 Too Many Requests")

    actor.set_value = broken_set_value
    actor, budget = await run(cdn, client, base_input, actor, videos=[ID])
    (row,) = actor.rows()
    assert row["status"] == "ok" and row["files"] == [] and budget.charged == 1
    assert row["errorMessage"] == "maxresdefault.jpg not saved (store write failed: RuntimeError)"


async def test_frame0_network_error_keeps_row_ok(cdn, client, base_input):
    mock_video(cdn, ID, ALL5)
    cdn.get(probe.thumb_url(ID, "frame0")).mock(side_effect=httpx.ReadTimeout("slow"))
    actor, budget = await run(cdn, client, base_input, videos=[ID])
    (row,) = actor.rows()
    assert row["status"] == "ok" and row["isVertical"] is None and row["aspectHint"] is None
    assert budget.charged == 1


async def test_oversized_image_is_not_saved(cdn, client, base_input):
    mock_video(cdn, ID, ALL5)
    cdn.get(probe.thumb_url(ID, "maxresdefault")).mock(
        return_value=httpx.Response(200, content=b"x" * (probe.MAX_IMAGE_BYTES + 1))
    )
    actor, budget = await run(cdn, client, base_input, videos=[ID])
    (row,) = actor.rows()
    assert row["status"] == "ok" and actor.kv == {} and budget.charged == 1
    assert row["errorMessage"] == "maxresdefault.jpg not saved (CDN did not serve it)"


@pytest.mark.parametrize(("limit", "max_videos"), [(3, 0), (None, 3), (5, 3), (3, 10)])
async def test_budget_stops_at_cap(cdn, client, base_input, limit, max_videos):
    ids = [ID, OLD, "029uWKeBdEo", "9bZkp7q19f0", "kJQP7kiw5Fk"]
    for vid in ids:
        mock_video(cdn, vid, ALL5)
    actor = FakeActor(limit=limit)
    actor, budget = await run(cdn, client, base_input, actor, videos=ids, maxVideos=max_videos)
    assert budget.cap == 3
    assert len(actor.rows("ok")) == 3 and len(actor.rows("budget_exhausted")) == 2
    assert [e for _, e in actor.pushed].count("video") == 3
    probed = {
        str(c.request.url).split("/vi/")[1][:11] for c in cdn.calls if "/vi/" in str(c.request.url)
    }
    assert len(probed) == 3  # videos past the cap were never touched


async def test_unlimited_when_manager_returns_none(cdn, client, base_input):
    actor = FakeActor(limit=None)
    assert main.charge_cap(0, "video", actor) is None
    assert main.charge_cap(7, "video", actor) == 7
    assert main.charge_cap(7, "video", FakeActor(limit=2)) == 2
    for vid in (ID, OLD):
        mock_video(cdn, vid, ALL5)
    actor, budget = await run(cdn, client, base_input, actor, videos=[ID, OLD], maxVideos=0)
    assert budget.cap is None and len(actor.rows("ok")) == 2


async def test_platform_limit_reached_midway(cdn, client, base_input):
    """The SDK reports the limit on the last chargeable push; later videos get free rows."""
    ids = [ID, OLD, "029uWKeBdEo"]
    for vid in ids:
        mock_video(cdn, vid, ALL5)
    actor = FakeActor(limit=2)
    inp = {**base_input, "videos": ids, "maxVideos": 0}
    r = main.Run(inp, actor=actor, client=client)
    r.budget.cap = (
        None  # pretend the pre-computed cap was unknown; the push result must still stop us
    )
    r.sem = asyncio.Semaphore(1)
    budget = await r.run()
    assert budget.exhausted and budget.delivered == 2
    assert len(actor.rows("budget_exhausted")) == 1


async def test_non_ppe_run_delivers_without_charging(cdn, client, base_input):
    """Local `apify run`: charged_count is 0 on every push and the row still counts."""
    mock_video(cdn, ID, ALL5)
    actor = FakeActor(ppe=False)
    actor, budget = await run(cdn, client, base_input, actor, videos=[ID])
    assert budget.delivered == 1 and budget.charged == 0 and not budget.exhausted
    assert len(actor.rows("ok")) == 1


async def test_ppe_zero_charge_means_zero_pushed(cdn, client, base_input):
    """PPE run where the SDK pushed nothing (budget short for video + dataset-item price)
    but reports the limit as not reached: the row must not count as delivered."""
    mock_video(cdn, ID, ALL5)
    actor = FakeActor()

    async def push_nothing(data, *, charged_event_name=None):
        if charged_event_name:
            return ChargeResult(event_charge_limit_reached=False, charged_count=0)
        actor.pushed.append((data, None))
        return ChargeResult()

    actor.push_data = push_nothing
    actor, budget = await run(cdn, client, base_input, actor, videos=[ID])
    assert budget.delivered == 0 and budget.charged == 0 and budget.exhausted
    assert [r["status"] for r in actor.rows()] == ["budget_exhausted"]


async def test_failed_push_never_counts(cdn, client, base_input, caplog):
    mock_video(cdn, ID, ALL5)
    actor = FakeActor(fail_push=True)
    with caplog.at_level(logging.ERROR, logger="apify"):
        actor, budget = await run(cdn, client, base_input, actor, videos=[ID])
    assert budget.delivered == 0 and budget.charged == 0 and budget.reserved == 0
    assert actor.rows() == []
    assert "push failed" in caplog.text


async def test_probe_exception_is_free_row(cdn, client, base_input):
    cdn.head(probe.thumb_url(ID, "maxresdefault")).mock(side_effect=httpx.ConnectError("x"))
    mock_video(cdn, OLD, ALL5)
    actor, budget = await run(cdn, client, base_input, videos=[ID, OLD])
    assert {r["status"] for r in actor.rows()} == {"not_found", "ok"}
    assert budget.charged == 1 and budget.reserved == 1


async def test_kv_writes_at_most_five_in_flight(cdn, client, base_input):
    ids = [ID, OLD, "029uWKeBdEo", "9bZkp7q19f0"]
    for vid in ids:
        mock_video(cdn, vid, ALL5)
    actor = FakeActor()
    in_flight = 0
    peak = 0

    async def slow_set_value(key, value, *, content_type=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        actor.kv[key] = (value, content_type)

    actor.set_value = slow_set_value
    actor, _ = await run(cdn, client, base_input, actor, videos=ids, sizes=["all"], format="both")
    assert len(actor.kv) == 40 and 1 < peak <= main.KV_CONCURRENCY


async def test_logs_never_contain_input_url(cdn, client, base_input, caplog):
    mock_video(cdn, ID, ALL5)
    url = f"https://www.youtube.com/watch?v={ID}&list=PLsecret&t=12"
    with caplog.at_level(logging.DEBUG, logger="apify"):
        await run(
            cdn, client, base_input, videos=[url, "https://www.youtube.com/playlist?list=PLx"]
        )
    assert caplog.text and "youtube.com/" not in caplog.text and "PLsecret" not in caplog.text


async def test_blocklisted_id_is_free_removed_row(cdn, client, base_input, tmp_path, monkeypatch):
    block = tmp_path / "blocklist.txt"
    block.write_text(f"# removed\n{ID}  # request 1\nnot-an-id\n")
    assert main.load_blocklist(block) == {ID}
    assert main.load_blocklist(tmp_path / "missing.txt") == set()
    monkeypatch.setattr(main, "BLOCKLIST", block)
    actor, budget = await run(cdn, client, base_input, videos=[ID])
    (row,) = actor.rows()
    assert row["status"] == "removed" and budget.charged == 0 and cdn.calls.call_count == 0


def test_selected_sizes():
    avail = ["sddefault", "hqdefault", "default"]
    assert main.selected_sizes(["best"], avail) == ["sddefault"]
    assert main.selected_sizes(["all"], avail) == avail
    assert main.selected_sizes(["maxresdefault", "default"], avail) == ["default"]
    assert main.selected_sizes(["best", "oar", "all"], avail) == [
        "sddefault",
        "hqdefault",
        "default",
    ]  # no oar frame available → none selected, none fetched
    assert main.selected_sizes(["best", "oar", "all"], [*avail, "oar1", "oar3"]) == [
        "sddefault",
        "oar1",
        "oar3",
        "hqdefault",
        "default",
    ]
    assert main.selected_sizes(["best"], []) == []


def test_shipped_blocklist_parses():
    assert main.load_blocklist() == set()
