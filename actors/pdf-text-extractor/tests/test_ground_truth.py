"""Known answers from our PDF writer; no customer or downloaded documents."""

import sys

import pytest
import respx
from conftest import FakeActor, fixture_url, serve
from make_fixtures import Pdf, text_ops
from src.main import run_actor

PRICES = {"page": 0.0003, "ocr-page": 0.003}
TEXTS = [
    "Synthetic invoice A. Due 2028-02-29. USD 100.10; paid 0.10; open 100.00. " * 4,
    "Synthetic invoice B. Due 2028-03-01. EUR 90.00; paid 30.00; open 60.00. " * 4,
    "Synthetic invoice C. Due 2028-01-31. GBP 0.30; paid 0.01; open 0.29. " * 4,
]


@pytest.mark.parametrize("mode", ["page", "document", "chunk"])
@pytest.mark.parametrize("selected", [[1, 2, 3], [1, 3]])
async def test_known_text_survives_all_output_modes(
    resolver, no_wait, work_dir, tmp_path, mode, selected
):
    pdf = Pdf()
    for text in TEXTS:
        # Short lines remain inside the page, including the final decimal digit.
        lines = text.rstrip().split(" ")
        wrapped = [" ".join(lines[i : i + 8]) for i in range(0, len(lines), 8)]
        pdf.add_page(text_ops([(40, 740 - 16 * i, 10, line) for i, line in enumerate(wrapped)]))
    path = tmp_path / "known.pdf"
    path.write_bytes(pdf.build())
    actor = FakeActor(
        {
            "urls": [fixture_url(path.name)],
            "outputMode": mode,
            "pageRange": ",".join(map(str, selected)),
            "chunkSize": 200,
            "chunkOverlap": 40,
        },
        prices=PRICES,
        max_total=1,
    )
    with respx.mock:
        serve(respx, {path.name: path}, path.name)
        await run_actor(actor, work_dir=work_dir)
    expected = []
    for number in selected:
        words = TEXTS[number - 1].split()
        expected.append("\n".join(" ".join(words[i : i + 8]) for i in range(0, len(words), 8)))
    full = "\n\n".join(expected)
    doc = actor.rows("document")[0]
    assert doc["status"] == "ok"
    billing = actor.values[doc["billingReceiptKey"]] if mode == "document" else doc
    assert (doc["pageCount"], doc["pagesExtracted"], billing["pagesCharged"]) == (
        3,
        len(selected),
        len(selected),
    )
    assert actor.cm.charged == {"page": len(selected)}
    if mode == "page":
        assert [p["page"] for p in actor.rows("page")] == selected
        assert [p["text"] for p in actor.rows("page")] == expected
    elif mode == "document":
        assert doc["text"] == full
        assert [p["page"] for p in doc["pages"]] == selected
        assert [p["text"] for p in doc["pages"]] == expected
        assert (doc["charCount"], doc["wordCount"]) == (len(full), len(full.split()))
    else:
        covered = set()
        bounds = []
        offset = 0
        for number, text in zip(selected, expected, strict=True):
            bounds.append((number, offset, offset + len(text)))
            offset += len(text) + 2
        for c in actor.rows("chunk"):
            start, end = c["charStart"], c["charEnd"]
            assert c["text"] == full[start:end] and c["text"].strip()
            assert len(c["text"]) <= 200
            assert c["pageStart"] == next(n for n, a, b in bounds if a <= start < b)
            assert c["pageEnd"] == next(n for n, a, b in bounds if a <= end - 1 < b)
            covered.update(range(start, end))
        assert all(i in covered for i, ch in enumerate(full) if not ch.isspace())
    assert list(work_dir.iterdir()) == []


@pytest.mark.parametrize("mode", ["page", "document", "chunk"])
@pytest.mark.parametrize("output", ["", " \t\n\f"])
async def test_empty_ocr_is_not_a_successful_paid_page(
    resolver, no_wait, work_dir, fixtures, fake_tesseract, mode, output
):
    fake_tesseract.write_text(f"#!{sys.executable}\nprint({output!r})\n")
    actor = FakeActor(
        {"urls": [fixture_url("image_only.pdf")], "outputMode": mode, "ocr": True},
        prices=PRICES,
        max_total=1,
    )
    with respx.mock:
        serve(respx, fixtures, "image_only.pdf")
        await run_actor(actor, work_dir=work_dir)
    doc = actor.rows("document")[0]
    billing = actor.values[doc["billingReceiptKey"]] if mode == "document" else doc
    assert billing["ocrPagesCharged"] == 0
    assert actor.cm.charged == {"page": 1}
    if mode == "page":
        scan = actor.rows("page")[0]
        assert scan["needsOcr"] and not scan["ocrApplied"] and scan["text"] == ""
    assert list(work_dir.iterdir()) == []


@pytest.mark.parametrize("mode", ["document", "chunk"])
async def test_bad_document_is_free_and_next_document_survives(
    resolver, no_wait, work_dir, fixtures, mode
):
    names = ["truncated.pdf", "simple.pdf"]
    actor = FakeActor(
        {"urls": [fixture_url(n) for n in names], "outputMode": mode}, prices=PRICES, max_total=1
    )
    with respx.mock:
        serve(respx, fixtures, *names)
        await run_actor(actor, work_dir=work_dir)
    docs = {d["fileName"]: d for d in actor.rows("document")}
    assert docs["truncated.pdf"]["errorCode"] == "malformed"
    assert docs["truncated.pdf"]["pagesCharged"] == 0
    good = docs["simple.pdf"]
    billing = actor.values[good["billingReceiptKey"]] if mode == "document" else good
    assert billing["pagesCharged"] == 3
    assert actor.cm.charged == {"page": 3}
    assert list(work_dir.iterdir()) == []
