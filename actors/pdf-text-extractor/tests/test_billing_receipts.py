"""Regression coverage for immutable output and shared delivery limits."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import FakeActor, fixture_url
from src.main import Run, parse_input


def parsed_pages(count=3):
    return {
        "pageCount": count,
        "pages": [
            {
                "page": i + 1,
                "text": f"Page {i + 1}",
                "markdown": None,
                "charCount": 6,
                "ocrApplied": False,
                "needsOcr": False,
                "tables": None,
            }
            for i in range(count)
        ],
    }


def fetched():
    return SimpleNamespace(
        document_id="a" * 16,
        final_url=fixture_url("a.pdf"),
        file_name="a.pdf",
        http_status=200,
        content_type="application/pdf",
        bytes=100,
    )


@pytest.mark.parametrize(
    "prices,cap,expected", [(None, None, 0), ({"page": 0.1}, 1, 3), ({"page": 0.1}, 0.1, 1)]
)
async def test_document_receipt_reports_actual_not_requested(tmp_path, prices, cap, expected):
    actor = FakeActor(
        {"urls": [fixture_url("a.pdf")], "outputMode": "document"}, prices=prices, max_total=cap
    )
    original = actor.push_data

    async def push(data, **kwargs):
        return await original(deepcopy(data), **kwargs)

    actor.push_data = push
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)
    await run.deliver(fixture_url("a.pdf"), fetched(), parsed_pages(), datetime.now(UTC))
    (doc,) = actor.dataset
    assert doc["pagesCharged"] is None
    assert doc["pagesChargeRequested"] == 3
    assert doc["billingReceiptUrl"].endswith("/records/" + doc["billingReceiptKey"])
    receipt = actor.values[doc["billingReceiptKey"]]
    assert receipt["status"] == "confirmed" and receipt["pagesCharged"] == expected
    assert receipt["ocrPagesCharged"] == 0
    assert run.pages_charged == expected and run.pages_delivered == 3


async def test_charge_transport_failure_never_claims_zero(tmp_path):
    actor = FakeActor(
        {"urls": [fixture_url("a.pdf")], "outputMode": "document"}, prices={"page": 0.1}
    )

    async def unknown(*args, **kwargs):
        raise ConnectionError("charge response lost")

    actor.charge = unknown
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)
    with pytest.raises(ConnectionError):
        await run.deliver(fixture_url("a.pdf"), fetched(), parsed_pages(), datetime.now(UTC))
    (doc,) = actor.dataset
    receipt = actor.values[doc["billingReceiptKey"]]
    assert receipt["status"] == "unknown" and receipt["pagesCharged"] is None


async def test_receipt_finalization_failure_is_visible_not_success(tmp_path):
    actor = FakeActor(
        {"urls": [fixture_url("a.pdf")], "outputMode": "document"}, prices={"page": 0.1}
    )
    save = actor.set_value

    async def fail_confirmed(key, value):
        if value["status"] == "confirmed":
            raise ConnectionError("receipt storage unavailable")
        await save(key, value)

    actor.set_value = fail_confirmed
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)
    with pytest.raises(ConnectionError):
        await run.deliver(fixture_url("a.pdf"), fetched(), parsed_pages(), datetime.now(UTC))
    assert actor.cm.charged == {"page": 3}
    assert actor.values[actor.dataset[0]["billingReceiptKey"]]["status"] == "pending"


async def test_zero_ppe_push_is_not_delivered_even_if_explicit_event_fits(tmp_path):
    actor = FakeActor({"urls": [fixture_url("a.pdf")]}, prices={"page": 0.1})

    async def zero(*args, **kwargs):
        return SimpleNamespace(charged_count=0, event_charge_limit_reached=False)

    actor.push_data = zero
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)
    assert await run.push_charged([{}], "page") == (0, 0, True)


@pytest.mark.parametrize("mode", ["page", "document", "chunk"])
async def test_concurrent_delivery_respects_global_page_limit(tmp_path, mode):
    actor = FakeActor({"urls": [fixture_url("a.pdf")], "outputMode": mode, "maxPages": 4})
    push = actor.push_data

    async def delayed(data, **kwargs):
        await asyncio.sleep(0.01)
        return await push(data, **kwargs)

    actor.push_data = delayed
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)

    async def deliver(index):
        info = fetched()
        info.document_id = str(index) * 16
        async with run.delivery_lock:
            await run.deliver(info.final_url, info, parsed_pages(), datetime.now(UTC))

    await asyncio.gather(deliver(1), deliver(2))
    assert run.pages_delivered == 4
    assert sum(d["pagesExtracted"] for d in actor.rows("document")) == 4
    assert actor.rows("document")[-1]["errorCode"] == "budget_exhausted"


@pytest.mark.parametrize(
    "response",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(charged_count=None),
        SimpleNamespace(charged_count=-1),
        SimpleNamespace(charged_count=True),
    ],
)
async def test_missing_charge_count_is_unknown(tmp_path, response):
    actor = FakeActor(
        {"urls": [fixture_url("a.pdf")], "outputMode": "document"}, prices={"page": 0.1}
    )

    async def invalid(*args, **kwargs):
        return response

    actor.charge = invalid
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)
    with pytest.raises(ValueError, match="Invalid charging response"):
        await run.deliver(fixture_url("a.pdf"), fetched(), parsed_pages(), datetime.now(UTC))
    receipt = actor.values[actor.dataset[0]["billingReceiptKey"]]
    assert receipt["status"] == "unknown" and receipt["pagesCharged"] is None


@pytest.mark.parametrize("mode", ["page", "document", "chunk"])
async def test_run_concurrent_parsers_cannot_overdeliver(
    resolver, no_wait, work_dir, fixtures, monkeypatch, mode
):
    import respx
    from conftest import serve
    from src import main as main_mod
    from src.main import run_actor

    arrived = 0
    both = asyncio.Event()

    async def parse(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=2)
        return parsed_pages()

    monkeypatch.setattr(main_mod, "parse_in_child", parse)
    actor = FakeActor(
        {
            "urls": [fixture_url("simple.pdf"), fixture_url("long.pdf")],
            "outputMode": mode,
            "maxPages": 4,
        }
    )
    with respx.mock:
        serve(respx, fixtures, "simple.pdf", "long.pdf")
        result = await run_actor(actor, work_dir=work_dir)
    assert arrived == 2
    assert result.pages_delivered == 4
    assert sum(doc["pagesExtracted"] for doc in actor.rows("document")) == 4
    assert actor.rows("document")[-1]["errorCode"] == "budget_exhausted"
