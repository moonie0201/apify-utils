"""Parse one PDF file (spec §2.4 steps 4-8). Runs inside the memory-capped child process.

PDFium (pypdfium2) for text, font sizes, image detection and rendering; pdfplumber only for
ruled tables (`lines` strategy, `page.close()` after every page); Tesseract via subprocess
for image-only pages when OCR is on. No AGPL PDF library, no direct pdfminer import.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .markdown import Table, build_markdown, page_lines

TEXT_LAYER_MIN_CHARS = 20
COPY_PERMISSION_BIT = 0x10
OCR_DPI = 200
MAX_RENDER_PIXELS = 10_000_000
TESSERACT_TIMEOUT = 60
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "tesseract")  # the binary; tests point it at a stub
TABLE_SETTINGS = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
_PDF_DATE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:([+\-Z])(\d{2})?'?(\d{2})?'?)?"
)


class ExtractError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Options:
    document_id: str
    work_dir: Path
    password: str | None = None
    page_range: list[tuple[int, int | None]] | None = None
    max_pages_per_pdf: int = 100
    page_budget: int | None = None  # None = unlimited
    include_markdown: bool = True
    extract_tables: bool = False
    ocr: bool = False
    ocr_language: str = "eng"
    max_ocr_pages: int | None = 50  # None = unlimited

    def to_json(self) -> dict:
        return {**self.__dict__, "work_dir": str(self.work_dir)}

    @classmethod
    def from_json(cls, data: dict) -> Options:
        data = dict(data)
        data["work_dir"] = Path(data["work_dir"])
        if data.get("page_range") is not None:
            data["page_range"] = [tuple(r) for r in data["page_range"]]
        return cls(**data)


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).rstrip()


def parse_page_range(spec: str | None) -> list[tuple[int, int | None]] | None:
    """`"1-5, 8, 12-"` → [(1,5), (8,8), (12,None)]; empty → None (all); junk → ValueError."""
    if spec is None or not spec.strip():
        return None
    ranges: list[tuple[int, int | None]] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            raise ValueError("empty range item")
        match = re.fullmatch(r"(\d+)\s*(?:(-)\s*(\d*))?", token)
        if not match:
            raise ValueError(f"invalid page range item {token!r}")
        start = int(match.group(1))
        if start < 1:
            raise ValueError("pages are 1-based")
        if not match.group(2):
            ranges.append((start, start))
        elif match.group(3):
            end = int(match.group(3))
            if end < start:
                raise ValueError(f"range end before start in {token!r}")
            ranges.append((start, end))
        else:
            ranges.append((start, None))
    return ranges


def select_pages(
    ranges: list[tuple[int, int | None]] | None, page_count: int, cap: int
) -> tuple[list[int], bool]:
    """Pages in the range ∩ [1, page_count], capped at `cap`; second value = whether capped."""
    wanted: list[int] = []
    if ranges is None:
        wanted = list(range(1, page_count + 1))
    else:
        seen: set[int] = set()
        for start, end in ranges:
            for page in range(start, min(end or page_count, page_count) + 1):
                if page not in seen:
                    seen.add(page)
                    wanted.append(page)
        wanted.sort()
    return wanted[:cap], len(wanted) > cap


def pdf_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    match = _PDF_DATE.match(value.strip())
    if not match:
        return None
    y, mo, d, h, mi, s, sign, oh, om = match.groups()
    try:
        when = datetime(int(y), int(mo or 1), int(d or 1), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    iso = when.isoformat()
    if sign in ("+", "-") and oh and int(oh) < 24 and int(om or 0) < 60:
        return f"{iso}{sign}{int(oh):02d}:{int(om or 0):02d}"
    return iso + "Z"


def render_scale(width_pt: float, height_pt: float) -> float:
    """200 dpi, clamped so the bitmap never exceeds MAX_RENDER_PIXELS."""
    inches = (width_pt / 72) * (height_pt / 72)
    if inches <= 0:
        return OCR_DPI / 72
    # 0.9995: PDFium rounds each side up to a whole pixel; keep the product under the cap.
    return min(OCR_DPI / 72, math.sqrt(MAX_RENDER_PIXELS / (inches * 72 * 72)) * 0.9995)


def run_tesseract(png: Path, language: str) -> str | None:
    try:
        proc = subprocess.run(
            [TESSERACT_CMD, str(png), "stdout", "-l", language, "--psm", "1"],
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def ocr_page(page: pdfium.PdfPage, opts: Options, page_number: int) -> str | None:
    width, height = page.get_size()
    bitmap = page.render(scale=render_scale(width, height), grayscale=True)
    png = opts.work_dir / f"{opts.document_id}_{page_number}.png"
    try:
        image = bitmap.to_pil()
        try:
            image.save(png, format="PNG")
        finally:
            image.close()
        return run_tesseract(png, opts.ocr_language)
    finally:
        png.unlink(missing_ok=True)
        bitmap.close()


def _open(path: Path, password: str | None) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path), password=password or None)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise ExtractError("password_required") from exc
        raise ExtractError("malformed") from exc


def _metadata(pdf: pdfium.PdfDocument) -> dict:
    meta = pdf.get_metadata_dict()
    version = pdf.get_version()
    return {
        "title": meta.get("Title") or None,
        "author": meta.get("Author") or None,
        "subject": meta.get("Subject") or None,
        "keywords": meta.get("Keywords") or None,
        "creator": meta.get("Creator") or None,
        "producer": meta.get("Producer") or None,
        "creationDate": pdf_date_to_iso(meta.get("CreationDate")),
        "modificationDate": pdf_date_to_iso(meta.get("ModDate")),
        "pdfVersion": f"{version // 10}.{version % 10}" if version else None,
    }


def _tables(plumber, page_index: int, page_height: float) -> list[Table]:
    try:
        page = plumber.pages[page_index]
    except Exception:  # noqa: BLE001 - pdfminer page-tree failure; text still comes from PDFium
        return []
    try:
        found = page.find_tables(TABLE_SETTINGS)
        tables = []
        for table in found:
            rows = [[" ".join((c or "").split()) for c in row] for row in table.extract()]
            rows = [r for r in rows if r]
            if len(rows) < 2 or max(len(r) for r in rows) < 2:
                continue  # 2×1 false positives (Census p9)
            x0, top, x1, bottom = table.bbox
            tables.append(
                Table(
                    rows=rows, x0=x0, y_bottom=page_height - bottom, x1=x1, y_top=page_height - top
                )
            )
        return tables
    except Exception:  # noqa: BLE001 - a table failure must not lose the page text
        return []
    finally:
        page.close()


def parse_document(path: Path, opts: Options) -> dict:
    pdf = _open(path, opts.password)
    plumber = None
    try:
        # User permissions: the owner-password view reports everything allowed, and the
        # no-copy flag is honoured regardless of which password opened the file.
        permissions = pdfium_c.FPDF_GetDocUserPermissions(pdf.raw)
        encrypted = pdfium_c.FPDF_GetSecurityHandlerRevision(pdf.raw) != -1
        copy_allowed = bool(permissions & COPY_PERMISSION_BIT)
        page_count = len(pdf)
        result = {
            "pageCount": page_count,
            "metadata": _metadata(pdf),
            "encrypted": encrypted,
            "permissionsCopyAllowed": copy_allowed,
            "errorCode": None,
            "pages": [],
            "imageOnlyPages": 0,
            "ocrPages": 0,
            "tablesUnavailable": False,
        }
        if not copy_allowed:
            result["errorCode"] = "permissions_restricted"
            return result
        pages, capped = select_pages(opts.page_range, page_count, opts.max_pages_per_pdf)
        if capped:
            result["errorCode"] = "pages_capped"
        if opts.page_budget is not None and len(pages) > opts.page_budget:
            pages = pages[: opts.page_budget]
            result["errorCode"] = "budget_exhausted"
        if opts.extract_tables and pages:
            import pdfplumber  # heavy import only when needed

            try:
                plumber = pdfplumber.open(str(path), password=opts.password or None)
            except Exception:  # noqa: BLE001 - pdfminer cannot parse what PDFium can: no tables
                plumber = None
                result["tablesUnavailable"] = True
        ocr_done = 0
        for number in pages:
            page = pdf[number - 1]
            try:
                width, height = page.get_size()
                tables = _tables(plumber, number - 1, height) if plumber else []
                row = _extract_page(page, number, page_count, tables, opts)
                if (
                    row["needsOcr"]
                    and opts.ocr
                    and (opts.max_ocr_pages is None or ocr_done < opts.max_ocr_pages)
                ):
                    text = ocr_page(page, opts, number)
                    if text is not None:
                        text = normalize_text(text)
                        row.update(
                            text=text,
                            charCount=len(text),
                            wordCount=len(text.split()),
                            ocrApplied=True,
                            needsOcr=False,
                            markdown=text if opts.include_markdown else None,
                        )
                        ocr_done += 1
                if row["needsOcr"] or row["ocrApplied"]:
                    result["imageOnlyPages"] += 1
                result["pages"].append(row)
            finally:
                page.close()
        result["ocrPages"] = ocr_done
        return result
    finally:
        if plumber is not None:
            plumber.close()
        pdf.close()


def _extract_page(
    page: pdfium.PdfPage, number: int, page_count: int, tables: list[Table], opts: Options
) -> dict:
    textpage = page.get_textpage()
    try:
        text = normalize_text(textpage.get_text_bounded())
        char_count = len(text)
        has_text = char_count >= TEXT_LAYER_MIN_CHARS
        image_only = not has_text and any(
            obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects()
        )
        markdown = None
        headings: list[dict] = []
        if opts.include_markdown and (has_text or tables):
            markdown, headings = build_markdown(page_lines(textpage), tables)
    finally:
        textpage.close()
    width, height = page.get_size()
    return {
        "page": number,
        "pageCount": page_count,
        "text": text,
        "markdown": markdown,
        "headings": headings,
        "tables": [t.rows for t in tables] if tables else None,
        "tableCount": len(tables),
        "charCount": char_count,
        "wordCount": len(text.split()),
        "width": round(width, 2),
        "height": round(height, 2),
        "rotation": page.get_rotation(),
        "hasTextLayer": has_text,
        "ocrApplied": False,
        "needsOcr": image_only,
    }
