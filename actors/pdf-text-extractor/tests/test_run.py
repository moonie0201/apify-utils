"""End-to-end runs against a fake Actor and respx-served fixtures: charging, budget,
run-timeout guard, size rule, cleanup and log hygiene (spec §2.8)."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from conftest import PUBLIC_IP, FakeActor, fixture_url, serve
from src import main as main_mod
from src.main import PAGE_EVENT, Run, charge_cap, parse_input, run_actor

PRICES = {"page": 0.0003, "ocr-page": 0.003}


def inp(*names, **extra):
    return {"urls": [fixture_url(n) for n in names], **extra}


async def run(
    actor: FakeActor, work_dir, fixtures, *names, serve_names=None, monkeypatch=None, concurrency=1
):
    if monkeypatch is not None:
        monkeypatch.setattr(main_mod, "CONCURRENCY", concurrency)
    with respx.mock:
        serve(respx, fixtures, *(serve_names or names))
        return await run_actor(actor, work_dir=work_dir)


# ----------------------------------------------------------------------------- input


def test_parse_input_defaults():
    s = parse_input({"urls": [" https://a.test/x.pdf ", ""]})
    assert s.urls == ["https://a.test/x.pdf"]
    assert (s.output_mode, s.max_pages, s.max_pages_per_pdf, s.max_pdf_mb) == (
        "page",
        5000,
        100,
        50,
    )
    assert s.password is None and s.page_range is None


def test_parse_input_clamps_and_rejects():
    s = parse_input(
        {"urls": ["https://a.test/x.pdf"], "maxPdfMb": 500, "chunkSize": 10, "pdfPassword": "pw"}
    )
    assert (s.max_pdf_mb, s.chunk_size, s.password) == (100, 200, "pw")
    for bad in (
        {},
        {"urls": []},
        {"urls": "x"},
        {"urls": ["u"], "outputMode": "xml"},
        {"urls": ["u"], "maxPages": "9"},
    ):
        with pytest.raises(ValueError):
            parse_input(bad)


async def test_invalid_page_range_fails_run_without_rows(work_dir):
    actor = FakeActor(inp("simple.pdf", pageRange="a-b"), prices=PRICES)
    assert await run_actor(actor, work_dir=work_dir) is None
    assert actor.failed["exit_code"] == 1 and "pageRange" in actor.failed["status_message"]
    assert actor.dataset == [] and actor.cm.charged == {}


def test_charge_cap_none_is_unlimited():
    actor = FakeActor({}, prices=None)
    assert charge_cap(actor, None, PAGE_EVENT) is None
    assert charge_cap(actor, 7, PAGE_EVENT) == 7
    actor = FakeActor({}, prices=PRICES, max_total=0.0009)
    assert charge_cap(actor, None, PAGE_EVENT) == 3
    assert charge_cap(actor, 2, PAGE_EVENT) == 2


# ----------------------------------------------------------------------------- page mode


async def test_page_mode_charges_each_pushed_row(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(inp("simple.pdf"), prices=PRICES, max_total=1.0)
    result = await run(actor, work_dir, fixtures, "simple.pdf")
    page_rows = actor.rows("page")
    assert len(page_rows) == 3 and actor.cm.charged == {"page": 3}
    assert [r["page"] for r in page_rows] == [1, 2, 3]
    doc = actor.rows("document")[0]
    assert actor.dataset[-1] is doc  # free summary row last
    assert doc["status"] == "ok" and doc["errorCode"] is None
    assert (doc["pagesExtracted"], doc["pagesCharged"], doc["ocrPagesCharged"]) == (3, 3, 0)
    assert doc["documentId"] == page_rows[0]["documentId"] and len(doc["documentId"]) == 16
    assert doc["fileName"] == "simple.pdf" and doc["httpStatus"] == 200 and doc["bytes"] > 0
    assert doc["metadata"]["title"] == "Fixture Title" and doc["fetchedAt"].endswith("Z")
    assert set(page_rows[0]) == set(
        "recordType url documentId page pageCount text markdown tables tableCount charCount "
        "wordCount width height rotation hasTextLayer ocrApplied needsOcr".split()
    )
    assert result.pages_charged == 3 and "3 page" in actor.status
    assert list(work_dir.iterdir()) == []


async def test_non_ppe_run_delivers_without_charging(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(inp("simple.pdf"), prices=None)  # calculate_max… → None everywhere
    await run(actor, work_dir, fixtures, "simple.pdf")
    assert len(actor.rows("page")) == 3 and actor.cm.charged == {}
    assert actor.rows("document")[0]["pagesCharged"] == 0


async def test_page_mode_budget_stop_and_free_rows(
    resolver, no_wait, work_dir, fixtures, monkeypatch
):
    actor = FakeActor(
        inp("simple.pdf", "long.pdf", "fonts.pdf"), prices=PRICES, max_total=0.0003 * 5
    )
    await run(
        actor, work_dir, fixtures, "simple.pdf", "long.pdf", "fonts.pdf", monkeypatch=monkeypatch
    )
    assert len(actor.rows("page")) == 5 and actor.cm.charged == {"page": 5}
    docs = {d["url"].rsplit("/", 1)[1]: d for d in actor.rows("document")}
    assert docs["simple.pdf"]["status"] == "ok"
    assert (
        docs["long.pdf"]["status"] == "partial"
        and docs["long.pdf"]["errorCode"] == "budget_exhausted"
    )
    assert docs["long.pdf"]["pagesCharged"] == 2
    assert (
        docs["fonts.pdf"]["status"] == "error"
        and docs["fonts.pdf"]["errorCode"] == "budget_exhausted"
    )
    assert docs["fonts.pdf"]["bytes"] == 0  # never fetched
    assert "stopped on budget" in actor.status


async def test_max_pages_cap(resolver, no_wait, work_dir, fixtures, monkeypatch):
    actor = FakeActor(inp("long.pdf", "simple.pdf", maxPages=4), prices=None)
    await run(actor, work_dir, fixtures, "long.pdf", "simple.pdf", monkeypatch=monkeypatch)
    assert len(actor.rows("page")) == 4
    docs = actor.rows("document")
    assert docs[0]["errorCode"] == "budget_exhausted" and docs[0]["pagesExtracted"] == 4
    assert docs[1]["errorCode"] == "budget_exhausted" and docs[1]["pagesExtracted"] == 0


async def test_ocr_rows_charged_as_ocr_page(resolver, no_wait, work_dir, fixtures, fake_tesseract):
    actor = FakeActor(inp("image_only.pdf", ocr=True), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "image_only.pdf")
    rows = actor.rows("page")
    assert rows[0]["ocrApplied"] is True and "HELLO OCR WORLD" in rows[0]["text"]
    assert rows[1]["ocrApplied"] is False
    assert actor.cm.charged == {"ocr-page": 1, "page": 1}
    doc = actor.rows("document")[0]
    assert (doc["pagesCharged"], doc["ocrPagesCharged"], doc["imageOnlyPages"]) == (1, 1, 1)
    assert list(work_dir.iterdir()) == []


async def test_ocr_off_image_pages_free(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(inp("image_only.pdf"), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "image_only.pdf")
    rows = actor.rows("page")
    assert rows[0]["needsOcr"] is True and rows[0]["text"] == ""
    assert actor.cm.charged == {"page": 1}  # the empty needsOcr row is delivered free
    doc = actor.rows("document")[0]
    assert (doc["pagesExtracted"], doc["pagesCharged"], doc["status"]) == (2, 1, "ok")


@pytest.mark.parametrize("mode", ["document", "chunk"])
async def test_ocr_off_image_pages_free_by_count(resolver, no_wait, work_dir, fixtures, mode):
    actor = FakeActor(inp("image_only.pdf", outputMode=mode), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "image_only.pdf")
    assert actor.charge_calls == [("page", 1)] and actor.cm.charged == {"page": 1}
    doc = actor.rows("document")[0]
    assert (doc["pagesExtracted"], doc["pagesCharged"], doc["imageOnlyPages"]) == (2, 1, 1)


async def test_page_mode_budget_reached_at_group_end(
    resolver, no_wait, work_dir, fixtures, fake_tesseract
):
    # The OCR page lands and exhausts the budget exactly; the text page group is never pushed.
    actor = FakeActor(inp("image_only.pdf", ocr=True), prices=PRICES, max_total=0.003)
    await run(actor, work_dir, fixtures, "image_only.pdf")
    assert len(actor.rows("page")) == 1 and actor.cm.charged == {"ocr-page": 1}
    doc = actor.rows("document")[0]
    assert doc["status"] == "partial" and doc["errorCode"] == "budget_exhausted"
    assert (doc["pagesExtracted"], doc["pagesCharged"], doc["ocrPagesCharged"]) == (1, 0, 1)
    assert "stopped on budget" in actor.status


async def test_push_failure_never_charges(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(inp("simple.pdf"), prices=PRICES, max_total=1.0)

    async def boom(data, *, charged_event_name=None):
        if charged_event_name:
            raise RuntimeError("dataset down")
        actor.dataset.extend(data if isinstance(data, list) else [data])

    actor.push_data = boom
    with pytest.raises(RuntimeError):
        await run(actor, work_dir, fixtures, "simple.pdf")
    assert actor.cm.charged == {} and actor.charge_calls == []
    assert list(work_dir.iterdir()) == []


# ----------------------------------------------------------------------------- document / chunk


async def test_document_mode_charges_after_push(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(
        inp("simple.pdf", outputMode="document", extractTables=True), prices=PRICES, max_total=1.0
    )
    await run(actor, work_dir, fixtures, "simple.pdf")
    assert len(actor.dataset) == 1
    doc = actor.dataset[0]
    assert doc["recordType"] == "document" and doc["status"] == "ok"
    assert doc["text"].startswith("The quick brown fox") and "Page three closes" in doc["text"]
    assert doc["markdown"] and len(doc["pages"]) == 3 and doc["pages"][0]["text"]
    assert doc["charCount"] == len(doc["text"]) and doc["tables"] is None
    assert actor.charge_calls == [("page", 3)] and actor.cm.charged == {"page": 3}
    assert doc["pagesCharged"] == 3


async def test_document_mode_item_too_large(resolver, no_wait, work_dir, fixtures, monkeypatch):
    monkeypatch.setattr(main_mod, "MAX_ITEM_BYTES", 500)
    actor = FakeActor(inp("simple.pdf", outputMode="document"), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "simple.pdf")
    doc = actor.dataset[0]
    assert doc["status"] == "partial" and doc["errorCode"] == "item_too_large"
    assert doc["text"].startswith("The quick brown fox")
    assert doc["markdown"] is None
    assert all(
        "text" not in p and {"page", "charCount", "ocrApplied"} <= set(p) for p in doc["pages"]
    )
    assert actor.cm.charged == {"page": 3}


async def test_document_mode_sdk_size_refusal_is_free_error_row(
    resolver, no_wait, work_dir, fixtures
):
    actor = FakeActor(inp("simple.pdf", outputMode="document"), prices=PRICES, max_total=1.0)
    real, calls = actor.push_data, []

    async def refuse_once(data, *, charged_event_name=None):
        calls.append(data)
        if len(calls) == 1:
            raise ValueError("Data item at index 0 is too large (size: 9.06 MB, limit: 9.00 MB)")
        return await real(data, charged_event_name=charged_event_name)

    actor.push_data = refuse_once
    await run(actor, work_dir, fixtures, "simple.pdf")
    assert len(actor.dataset) == 1
    doc = actor.dataset[0]
    assert doc["status"] == "error" and doc["errorCode"] == "item_too_large"
    assert not {"text", "markdown", "pages", "tables"} & set(doc)
    assert (doc["pagesExtracted"], doc["pagesCharged"]) == (0, 0)
    assert actor.cm.charged == {} and actor.charge_calls == []


async def test_chunk_mode_charges_pages_not_chunks(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(
        inp("long.pdf", outputMode="chunk", chunkSize=400, chunkOverlap=50),
        prices=PRICES,
        max_total=1.0,
    )
    await run(actor, work_dir, fixtures, "long.pdf")
    chunks = actor.rows("chunk")
    assert len(chunks) > 6
    assert all(c["chunkCount"] == len(chunks) for c in chunks)
    assert chunks[0]["pageStart"] == 1 and chunks[-1]["pageEnd"] == 6
    assert set(chunks[0]) == {
        "recordType", "url", "documentId", "chunkIndex", "chunkCount", "pageStart", "pageEnd",
        "charStart", "charEnd", "text", "tokenEstimate", "headingPath",
    }  # fmt: skip
    assert actor.charge_calls == [("page", 6)] and actor.cm.charged == {"page": 6}
    assert actor.dataset[-1]["recordType"] == "document" and actor.dataset[-1]["pagesCharged"] == 6


async def test_chunk_mode_budget_limit_sets_stop(
    resolver, no_wait, work_dir, fixtures, monkeypatch
):
    actor = FakeActor(
        inp("long.pdf", "simple.pdf", outputMode="chunk"), prices=PRICES, max_total=0.0003 * 6
    )
    await run(actor, work_dir, fixtures, "long.pdf", "simple.pdf", monkeypatch=monkeypatch)
    assert actor.cm.charged == {"page": 6}
    assert actor.rows("document")[1]["errorCode"] == "budget_exhausted"


# ----------------------------------------------------------------------------- errors and guards


async def test_error_rows_are_free(resolver, no_wait, work_dir, fixtures, monkeypatch):
    actor = FakeActor(
        inp("encrypted.pdf", "nocopy.pdf", "truncated.pdf", "bomb.pdf", "missing.pdf"),
        prices=PRICES,
        max_total=1.0,
    )
    with respx.mock:
        serve(respx, fixtures, "encrypted.pdf", "nocopy.pdf", "truncated.pdf", "bomb.pdf")
        respx.get(f"https://{PUBLIC_IP}/missing.pdf").mock(return_value=httpx.Response(404))
        monkeypatch.setattr(main_mod, "CONCURRENCY", 1)
        await run_actor(actor, work_dir=work_dir)
    codes = [d["errorCode"] for d in actor.rows("document")]
    assert codes == [
        "password_required",
        "permissions_restricted",
        "malformed",
        "malformed",
        "download_failed",
    ]
    assert all(d["status"] == "error" and d["pagesCharged"] == 0 for d in actor.rows("document"))
    assert actor.rows("document")[1]["permissionsCopyAllowed"] is False
    assert actor.cm.charged == {} and actor.rows("page") == []
    assert list(work_dir.iterdir()) == []


async def test_password_unlocks(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(inp("encrypted.pdf", pdfPassword="secret"), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "encrypted.pdf")
    assert (
        actor.rows("document")[0]["status"] == "ok"
        and actor.rows("document")[0]["encrypted"] is True
    )
    assert actor.cm.charged == {"page": 1}


async def test_timeout_row_and_cleanup(resolver, no_wait, work_dir, fixtures, monkeypatch):
    monkeypatch.setattr(main_mod, "WALL_LIMIT_TEXT", 0.01)
    actor = FakeActor(inp("simple.pdf"), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "simple.pdf")
    doc = actor.rows("document")[0]
    assert doc["errorCode"] == "timeout" and doc["status"] == "error"
    assert actor.cm.charged == {} and list(work_dir.iterdir()) == []


async def test_duplicates_free(resolver, no_wait, work_dir, fixtures, monkeypatch):
    actor = FakeActor(inp("simple.pdf", "simple.pdf", "copy.pdf"), prices=PRICES, max_total=1.0)
    with respx.mock:
        serve(respx, fixtures, "simple.pdf")
        respx.get(f"https://{PUBLIC_IP}/copy.pdf").mock(
            return_value=httpx.Response(200, content=fixtures["simple.pdf"].read_bytes())
        )
        monkeypatch.setattr(main_mod, "CONCURRENCY", 1)
        await run_actor(actor, work_dir=work_dir)
    docs = actor.rows("document")
    assert [d["errorCode"] for d in docs] == [None, "duplicate", "duplicate"]
    assert docs[2]["documentId"] == docs[0]["documentId"]
    assert actor.cm.charged == {"page": 3}


async def test_run_timeout_guard(resolver, no_wait, work_dir, fixtures):
    soon = datetime.now(UTC) + timedelta(seconds=100)  # < 120 s wall + 30 s margin
    actor = FakeActor(inp("simple.pdf", "long.pdf"), prices=PRICES, max_total=1.0, timeout_at=soon)
    with respx.mock:
        route = respx.get(url__regex=rf"https://{PUBLIC_IP}/.*").mock(
            return_value=httpx.Response(200, content=b"%PDF-")
        )
        await run_actor(actor, work_dir=work_dir)
    assert not route.called
    docs = actor.rows("document")
    assert [d["errorCode"] for d in docs] == ["run_timeout", "run_timeout"]
    assert actor.cm.charged == {} and actor.rows("page") == []
    assert actor.status == "Stopped before run timeout; 2 documents not processed"


async def test_run_timeout_far_away_is_ignored(resolver, no_wait, work_dir, fixtures):
    later = datetime.now(UTC) + timedelta(hours=1)
    actor = FakeActor(inp("simple.pdf"), prices=PRICES, max_total=1.0, timeout_at=later)
    await run(actor, work_dir, fixtures, "simple.pdf")
    assert actor.cm.charged == {"page": 3}


async def test_blocked_url_row(no_wait, work_dir, fixtures):
    actor = FakeActor(
        {"urls": ["http://169.254.169.254/latest/meta-data"]}, prices=PRICES, max_total=1.0
    )
    await run_actor(actor, work_dir=work_dir)
    doc = actor.rows("document")[0]
    assert doc["errorCode"] == "blocked_url" and doc["httpStatus"] is None


BAD_URLS = ["http://[::1/a.pdf", "http://[foo]/a.pdf", "http://1.1.1.1/a b/\x7f?q=é"]


async def test_unparseable_urls_are_free_rows_and_run_continues(
    resolver, no_wait, work_dir, fixtures, monkeypatch
):
    actor = FakeActor({"urls": [*BAD_URLS, fixture_url("simple.pdf")]}, prices=PRICES, max_total=1)
    await run(actor, work_dir, fixtures, "simple.pdf", monkeypatch=monkeypatch)
    docs = actor.rows("document")
    assert [d["errorCode"] for d in docs] == ["blocked_url"] * 3 + [None]
    assert all(d["status"] == "error" and d["bytes"] == 0 for d in docs[:3])
    assert actor.cm.charged == {"page": 3} and actor.failed is None


async def test_unparseable_url_on_the_stop_path(no_wait, work_dir):
    soon = datetime.now(UTC) + timedelta(seconds=10)
    actor = FakeActor({"urls": BAD_URLS[:1]}, prices=PRICES, max_total=1.0, timeout_at=soon)
    await run_actor(actor, work_dir=work_dir)
    assert actor.rows("document")[0]["errorCode"] == "run_timeout"


async def test_blocklisted_host_is_free_row_without_request(
    resolver, no_wait, work_dir, fixtures, monkeypatch
):
    from src import guard

    monkeypatch.setattr(guard, "BLOCKED_HOSTS", frozenset({"files.test"}))
    actor = FakeActor(inp("simple.pdf"), prices=PRICES, max_total=1.0)
    with respx.mock:
        route = respx.get(url__regex=rf"https://{PUBLIC_IP}/.*").mock(
            return_value=httpx.Response(200, content=fixtures["simple.pdf"].read_bytes())
        )
        await run_actor(actor, work_dir=work_dir)
    assert not route.called
    doc = actor.rows("document")[0]
    assert doc["errorCode"] == "blocked_url" and doc["status"] == "error"
    assert actor.cm.charged == {} and actor.rows("page") == []


def test_file_cap_bounded_by_memory():
    actor = FakeActor(inp("simple.pdf", maxPdfMb=100), prices=None)
    settings = parse_input(actor.input)
    assert Run(actor, settings, memory_mb=256).cap_bytes == 64 * 1024 * 1024  # memory / 4 wins
    assert Run(actor, settings, memory_mb=1024).cap_bytes == 100 * 1024 * 1024  # maxPdfMb wins


async def test_logs_never_contain_url_or_text(resolver, no_wait, work_dir, fixtures, caplog):
    caplog.set_level(logging.INFO)
    actor = FakeActor(inp("simple.pdf"), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "simple.pdf")
    assert actor.cm.charged == {"page": 3}
    assert "simple.pdf" not in caplog.text
    assert "quick brown fox" not in caplog.text
    assert "files.test" in caplog.text  # host and counts are logged


async def test_two_documents_concurrently(resolver, no_wait, work_dir, fixtures):
    actor = FakeActor(inp("simple.pdf", "fonts.pdf", "table.pdf"), prices=PRICES, max_total=1.0)
    await run(actor, work_dir, fixtures, "simple.pdf", "fonts.pdf", "table.pdf")
    assert len(actor.rows("document")) == 3 and actor.cm.charged == {"page": 5}
    assert actor.dataset[-1]["recordType"] == "document"
    assert list(work_dir.iterdir()) == []
