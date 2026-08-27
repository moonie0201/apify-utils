"""Network tests (opt in with `pytest -m live`): US-government public-domain PDFs on irs.gov
and a Google-Docs viewer URL. The OCR test needs a real `tesseract` binary (run inside the
Docker image)."""

import pytest
import respx
from conftest import FakeActor, fixture_url, serve
from src.main import run_actor

pytestmark = pytest.mark.live

FW9 = "https://www.irs.gov/pub/irs-pdf/fw9.pdf"
P17 = "https://www.irs.gov/pub/irs-pdf/p17.pdf"
VIEWER = "https://docs.google.com/viewer?url=https%3A%2F%2Fwww.irs.gov%2Fpub%2Firs-pdf%2Ffw9.pdf"


async def test_fw9_six_pages(work_dir):
    actor = FakeActor({"urls": [FW9]}, prices={"page": 0.0003, "ocr-page": 0.003}, max_total=1.0)
    await run_actor(actor, work_dir=work_dir)
    doc = actor.rows("document")[0]
    assert doc["status"] == "ok" and doc["pageCount"] == 6 and doc["pagesCharged"] == 6
    pages = actor.rows("page")
    assert len(pages) == 6 and "Request for Taxpayer" in pages[0]["text"]
    assert pages[0]["markdown"] and pages[0]["hasTextLayer"]
    assert list(work_dir.iterdir()) == []


async def test_p17_pages_capped(work_dir):
    actor = FakeActor({"urls": [P17], "maxPagesPerPdf": 100, "outputMode": "document"}, prices=None)
    await run_actor(actor, work_dir=work_dir)
    doc = actor.dataset[0]
    assert (
        doc["pageCount"] > 100 and doc["errorCode"] == "pages_capped" and doc["status"] == "partial"
    )
    assert len(doc["pages"]) == 100 and doc["durationMs"] < 60_000


async def test_google_viewer_is_not_pdf(work_dir):
    actor = FakeActor({"urls": [VIEWER]}, prices=None)
    await run_actor(actor, work_dir=work_dir)
    doc = actor.rows("document")[0]
    assert doc["errorCode"] in ("not_pdf", "download_failed") and doc["status"] == "error"
    assert actor.rows("page") == []


async def test_real_tesseract_reads_fixture(resolver, no_wait, work_dir, fixtures):
    """Requires the tesseract binary (the Docker image has it)."""
    actor = FakeActor({"urls": [fixture_url("image_only.pdf")], "ocr": True}, prices=None)
    with respx.mock:
        serve(respx, fixtures, "image_only.pdf")
        await run_actor(actor, work_dir=work_dir)
    first = actor.rows("page")[0]
    assert first["ocrApplied"] is True
    assert "HELLO" in first["text"].upper() and "12345" in first["text"]
