import subprocess
import time
from pathlib import Path

import pytest
from src import extract
from src.extract import (
    Options,
    normalize_text,
    parse_document,
    parse_page_range,
    pdf_date_to_iso,
    render_scale,
    select_pages,
)
from src.worker import parse_blocking


def opts(work_dir: Path, **kw) -> Options:
    return Options(document_id="doc1", work_dir=work_dir, **kw)


# ----------------------------------------------------------------------------- pure helpers


def test_normalize_text():
    assert normalize_text("a\r\nb\rc\n") == "a\nb\nc"
    assert normalize_text("é") == "é"  # NFC


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("1-5, 8, 12-", [(1, 5), (8, 8), (12, None)]),
        ("3", [(3, 3)]),
        ("", None),
        (None, None),
        ("  2 - 4 ", [(2, 4)]),
    ],
)
def test_parse_page_range(spec, expected):
    assert parse_page_range(spec) == expected


@pytest.mark.parametrize("junk", ["a-b", "5-3", "0", "1,,2", "1;2", "-3", "1-2-3"])
def test_parse_page_range_junk(junk):
    with pytest.raises(ValueError):
        parse_page_range(junk)


def test_select_pages():
    assert select_pages(None, 5, 100) == ([1, 2, 3, 4, 5], False)
    assert select_pages(None, 5, 2) == ([1, 2], True)
    assert select_pages([(2, 3), (9, None), (3, 3)], 10, 100) == ([2, 3, 9, 10], False)
    assert select_pages([(20, None)], 10, 100) == ([], False)


def test_pdf_date_to_iso():
    assert pdf_date_to_iso("D:20240102030405+02'00'") == "2024-01-02T03:04:05+02:00"
    assert pdf_date_to_iso("D:20240102030405Z") == "2024-01-02T03:04:05Z"
    assert pdf_date_to_iso("D:2024") == "2024-01-01T00:00:00Z"
    assert pdf_date_to_iso("garbage") is None
    assert pdf_date_to_iso(None) is None
    assert pdf_date_to_iso("D:20241399") is None


def test_render_scale_clamped_to_10_mpx():
    assert render_scale(612, 792) == pytest.approx(200 / 72)
    scale = render_scale(14400, 14400)
    assert 14400 * scale * 14400 * scale <= extract.MAX_RENDER_PIXELS + 1
    assert scale < 200 / 72


# ----------------------------------------------------------------------------- in-process parse


def test_text_pages_and_metadata(fixtures, work_dir):
    result = parse_document(fixtures["simple.pdf"], opts(work_dir))
    assert result["errorCode"] is None
    assert result["pageCount"] == 3
    assert result["encrypted"] is False and result["permissionsCopyAllowed"] is True
    meta = result["metadata"]
    assert meta["title"] == "Fixture Title"
    assert meta["creationDate"] == "2024-01-02T03:04:05+02:00"
    assert meta["modificationDate"] == "2024-01-02T03:04:05Z"
    assert meta["pdfVersion"] == "1.7"
    page = result["pages"][0]
    assert "\r" not in page["text"]
    assert page["text"].startswith("The quick brown fox jumps over the lazy dog.\nSecond line")
    assert page["hasTextLayer"] is True and page["needsOcr"] is False
    assert page["charCount"] == len(page["text"])
    assert page["wordCount"] == len(page["text"].split())
    assert (page["width"], page["height"]) == (612.0, 792.0)
    assert result["pages"][2]["rotation"] == 90
    # paragraphs from line gaps
    assert page["markdown"].count("\n\n") == 1


def test_markdown_heading_ranks(fixtures, work_dir):
    page = parse_document(fixtures["fonts.pdf"], opts(work_dir))["pages"][0]
    md = page["markdown"]
    assert md.startswith("# Document Title\n\n## Section One\n\nBody text line one")
    assert "\n\n## Section Two\n\n### Subsection\n\n" in md
    assert [h["level"] for h in page["headings"]] == [1, 2, 2, 3]


def test_rotated_text_stays_one_line(fixtures, work_dir):
    page = parse_document(fixtures["rotated.pdf"], opts(work_dir))["pages"][0]
    assert "Print or type. See instructions on page 3." in page["markdown"]
    assert page["markdown"].count("\n\n") <= 2  # two horizontal lines + the vertical sentence


def test_markdown_off(fixtures, work_dir):
    page = parse_document(fixtures["fonts.pdf"], opts(work_dir, include_markdown=False))["pages"][0]
    assert page["markdown"] is None and page["headings"] == []


def test_ruled_table(fixtures, work_dir):
    page = parse_document(fixtures["table.pdf"], opts(work_dir, extract_tables=True))["pages"][0]
    assert page["tableCount"] == 1
    assert page["tables"] == [[["Name", "Qty", "Price"], ["A2", "B2", "C2"], ["A1", "B1", "C1"]]]
    assert "| Name | Qty | Price |\n|---|---|---|\n| A2 | B2 | C2 |" in page["markdown"]
    assert (
        page["markdown"].index("Table below")
        < page["markdown"].index("| Name")
        < page["markdown"].index("Text after")
    )
    assert page["markdown"].count("A1") == 1  # table text not repeated as a paragraph


def test_two_by_one_filtered(fixtures, work_dir):
    page = parse_document(fixtures["two_by_one.pdf"], opts(work_dir, extract_tables=True))["pages"][
        0
    ]
    assert page["tableCount"] == 0 and page["tables"] is None


def test_pdfplumber_failure_keeps_page_text(fixtures, work_dir, monkeypatch):
    import pdfplumber

    def boom(*args, **kwargs):
        raise RuntimeError("pdfminer choked")

    monkeypatch.setattr(pdfplumber, "open", boom)
    result = parse_document(fixtures["table.pdf"], opts(work_dir, extract_tables=True))
    assert result["errorCode"] is None and result["tablesUnavailable"] is True
    page = result["pages"][0]
    assert "Table below" in page["text"] and page["tables"] is None and page["tableCount"] == 0


def test_surrogate_halves_never_reach_the_row(fixtures, work_dir):
    import json

    page = parse_document(fixtures["surrogate.pdf"], opts(work_dir))["pages"][0]
    assert page["markdown"].startswith("# Hello  \U0001f600")  # pair combined, lone half dropped
    assert page["headings"] == [{"level": 1, "text": "Hello  \U0001f600"}]
    for value in (page["text"], page["markdown"]):
        assert not any("\ud800" <= c <= "\udfff" for c in value)
    json.dumps(page, ensure_ascii=False).encode("utf-8")  # what apify's dataset client does


def test_tables_off_by_default(fixtures, work_dir):
    page = parse_document(fixtures["table.pdf"], opts(work_dir))["pages"][0]
    assert page["tables"] is None and page["tableCount"] == 0


def test_image_only_detection_ocr_off(fixtures, work_dir):
    result = parse_document(fixtures["image_only.pdf"], opts(work_dir))
    first, second = result["pages"]
    assert first["needsOcr"] is True and first["hasTextLayer"] is False and first["text"] == ""
    assert first["ocrApplied"] is False
    assert second["needsOcr"] is False and second["hasTextLayer"] is True
    assert result["imageOnlyPages"] == 1 and result["ocrPages"] == 0


def test_ocr_path_with_mocked_tesseract(fixtures, work_dir, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert Path(cmd[1]).exists()
        return subprocess.CompletedProcess(cmd, 0, stdout=b"HELLO OCR\r\nWORLD\n", stderr=b"")

    monkeypatch.setattr(extract.subprocess, "run", fake_run)
    result = parse_document(fixtures["image_only.pdf"], opts(work_dir, ocr=True))
    first = result["pages"][0]
    assert first["ocrApplied"] is True and first["needsOcr"] is False
    assert first["text"] == "HELLO OCR\nWORLD"
    assert first["markdown"] == "HELLO OCR\nWORLD"
    assert first["hasTextLayer"] is False
    assert result["ocrPages"] == 1
    assert calls and calls[0][3:] == ["-l", "eng", "--psm", "1"]
    assert list(work_dir.iterdir()) == []  # PNG deleted


def test_ocr_failure_leaves_page_free(fixtures, work_dir, monkeypatch):
    monkeypatch.setattr(
        extract.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no tesseract"))
    )
    result = parse_document(fixtures["image_only.pdf"], opts(work_dir, ocr=True))
    first = result["pages"][0]
    assert first["ocrApplied"] is False and first["needsOcr"] is True and first["text"] == ""
    assert result["ocrPages"] == 0


def test_ocr_cap_per_document(fixtures, work_dir, monkeypatch):
    monkeypatch.setattr(
        extract.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout=b"X", stderr=b""),
    )
    result = parse_document(fixtures["image_only.pdf"], opts(work_dir, ocr=True, max_ocr_pages=0))
    assert result["pages"][0]["needsOcr"] is True and result["ocrPages"] == 0


def test_giant_page_render_clamped_and_ocr_runs(fixtures, work_dir, fake_tesseract):
    result = parse_document(fixtures["giant.pdf"], opts(work_dir, ocr=True))
    page = result["pages"][0]
    assert page["ocrApplied"] is True
    size_line = page["text"].splitlines()[0]
    w, h = (int(v) for v in size_line.split()[1].split("x"))
    assert w * h <= extract.MAX_RENDER_PIXELS
    assert "HELLO OCR WORLD" in page["text"]


def test_page_range_and_caps(fixtures, work_dir):
    result = parse_document(fixtures["long.pdf"], opts(work_dir, page_range=[(2, 3), (6, None)]))
    assert [p["page"] for p in result["pages"]] == [2, 3, 6]
    assert result["errorCode"] is None
    capped = parse_document(fixtures["long.pdf"], opts(work_dir, max_pages_per_pdf=2))
    assert [p["page"] for p in capped["pages"]] == [1, 2] and capped["errorCode"] == "pages_capped"
    budget = parse_document(fixtures["long.pdf"], opts(work_dir, page_budget=4))
    assert len(budget["pages"]) == 4 and budget["errorCode"] == "budget_exhausted"
    unlimited = parse_document(fixtures["long.pdf"], opts(work_dir, page_budget=None))
    assert len(unlimited["pages"]) == 6


def test_encrypted_requires_password(fixtures, work_dir):
    with pytest.raises(extract.ExtractError) as info:
        parse_document(fixtures["encrypted.pdf"], opts(work_dir))
    assert info.value.code == "password_required"
    with pytest.raises(extract.ExtractError) as info:
        parse_document(fixtures["encrypted.pdf"], opts(work_dir, password="wrong"))
    assert info.value.code == "password_required"
    ok = parse_document(fixtures["encrypted.pdf"], opts(work_dir, password="secret"))
    assert ok["encrypted"] is True and ok["pages"][0]["text"] == "Protected by a user password."


def test_owner_no_copy_always_restricted(fixtures, work_dir):
    for password in (None, "owner"):
        result = parse_document(fixtures["nocopy.pdf"], opts(work_dir, password=password))
        assert result["errorCode"] == "permissions_restricted"
        assert result["permissionsCopyAllowed"] is False and result["encrypted"] is True
        assert result["pages"] == []


def test_truncated_is_malformed(fixtures, work_dir):
    with pytest.raises(extract.ExtractError) as info:
        parse_document(fixtures["truncated.pdf"], opts(work_dir))
    assert info.value.code == "malformed"


# ----------------------------------------------------------------------------- child process


def test_child_many_pages_capped(fixtures, work_dir):
    result = parse_blocking(
        fixtures["many_pages.pdf"], opts(work_dir), memory_mb=512, wall_limit=60
    )
    assert result["pageCount"] == 20000
    assert result["errorCode"] == "pages_capped" and len(result["pages"]) == 100


def test_child_deflate_bomb_is_malformed_quickly(fixtures, work_dir):
    started = time.monotonic()
    result = parse_blocking(fixtures["bomb.pdf"], opts(work_dir), memory_mb=512, wall_limit=60)
    assert result["errorCode"] == "malformed"
    assert time.monotonic() - started < 30
    assert list(work_dir.iterdir()) == []


def test_child_timeout(fixtures, work_dir):
    result = parse_blocking(fixtures["simple.pdf"], opts(work_dir), memory_mb=512, wall_limit=0.01)
    assert result["errorCode"] == "timeout"


def test_child_error_codes_cross_the_pipe(fixtures, work_dir):
    assert (
        parse_blocking(fixtures["encrypted.pdf"], opts(work_dir), memory_mb=512, wall_limit=30)[
            "errorCode"
        ]
        == "password_required"
    )
    assert (
        parse_blocking(fixtures["truncated.pdf"], opts(work_dir), memory_mb=512, wall_limit=30)[
            "errorCode"
        ]
        == "malformed"
    )
    ok = parse_blocking(fixtures["simple.pdf"], opts(work_dir), memory_mb=512, wall_limit=30)
    assert ok["errorCode"] is None and len(ok["pages"]) == 3


def test_child_ocr_under_limit(fixtures, work_dir, fake_tesseract):
    result = parse_blocking(
        fixtures["giant.pdf"], opts(work_dir, ocr=True), memory_mb=512, wall_limit=120
    )
    assert result["errorCode"] is None
    assert result["pages"][0]["ocrApplied"] is True
    assert list(work_dir.iterdir()) == []
