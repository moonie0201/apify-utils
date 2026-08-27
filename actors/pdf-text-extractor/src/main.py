"""Actor entrypoint for `pdf-text-extractor` (UTILS_SPEC §2).

PDF URLs in; page / document / chunk rows out. Two documents in flight, each downloaded to
disk and parsed in a memory-capped child; charged rows are pushed first and the free
document row last; `page` / `ocr-page` are charged only for rows that landed (SDK
`push_data(charged_event_name=)` in page mode, `Actor.charge(count=)` after the push in
document and chunk mode). Budget, `maxPages` and the run-timeout guard are checked before
a document is fetched, so nothing is extracted that cannot be delivered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

from .chunk import chunk_pages
from .extract import Options, parse_page_range
from .fetch import HostGate, download, log_event, make_client
from .worker import parse_in_child

PAGE_EVENT = "page"
OCR_EVENT = "ocr-page"
CONCURRENCY = 2
WALL_LIMIT_TEXT = 120.0
WALL_LIMIT_OCR = 600.0
TIMEOUT_MARGIN = 30.0
MAX_ITEM_BYTES = 8 * 1024 * 1024
MAX_URLS = 100
WORK_DIR = Path(tempfile.gettempdir())
FATAL_CODES = frozenset({"password_required", "permissions_restricted", "malformed", "timeout"})
EMPTY_METADATA = {
    "title": None, "author": None, "subject": None, "keywords": None, "creator": None,
    "producer": None, "creationDate": None, "modificationDate": None, "pdfVersion": None,
}  # fmt: skip


@dataclass
class Settings:
    urls: list[str]
    output_mode: str = "page"
    include_markdown: bool = True
    extract_tables: bool = False
    page_range: list[tuple[int, int | None]] | None = None
    max_pages_per_pdf: int = 100
    max_pages: int = 5000
    max_pdf_mb: int = 50
    ocr: bool = False
    ocr_language: str = "eng"
    max_ocr_pages_per_pdf: int = 50
    chunk_size: int = 1500
    chunk_overlap: int = 200
    password: str | None = None


def _int(raw: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = raw.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise ValueError(f"{key} must be an integer")
    return min(hi, max(lo, int(value)))


def parse_input(raw: dict) -> Settings:
    urls_raw = raw.get("urls")
    if not isinstance(urls_raw, list):
        raise ValueError("urls must be a list of PDF URLs")
    urls = [u.strip() for u in urls_raw if isinstance(u, str) and u.strip()]
    if not urls:
        raise ValueError("urls is empty")
    if len(urls) > MAX_URLS:
        raise ValueError(f"at most {MAX_URLS} URLs per run")
    mode = raw.get("outputMode") or "page"
    if mode not in ("page", "document", "chunk"):
        raise ValueError("outputMode must be page, document or chunk")
    try:
        page_range = parse_page_range(raw.get("pageRange"))
    except ValueError as exc:
        raise ValueError(f"pageRange: {exc}") from exc
    language = raw.get("ocrLanguage") or "eng"
    if language != "eng":
        raise ValueError("ocrLanguage: only eng is shipped")
    password = raw.get("pdfPassword")
    return Settings(
        urls=urls,
        output_mode=mode,
        include_markdown=bool(raw.get("includeMarkdown", True)),
        extract_tables=bool(raw.get("extractTables", False)),
        page_range=page_range,
        max_pages_per_pdf=_int(raw, "maxPagesPerPdf", 100, 1, 2000),
        max_pages=_int(raw, "maxPages", 5000, 0, 100000),
        max_pdf_mb=_int(raw, "maxPdfMb", 50, 1, 100),
        ocr=bool(raw.get("ocr", False)),
        ocr_language=language,
        max_ocr_pages_per_pdf=_int(raw, "maxOcrPagesPerPdf", 50, 1, 500),
        chunk_size=_int(raw, "chunkSize", 1500, 200, 20000),
        chunk_overlap=_int(raw, "chunkOverlap", 200, 0, 5000),
        password=password if isinstance(password, str) and password else None,
    )


def charge_cap(actor: Any, input_cap: int | None, event: str) -> int | None:
    """None = unlimited. calculate_max_event_charge_count_within_limit returns None when the
    event has no price (every non-PPE run: `apify run` locally, the dev's own runs, FREE-record
    runs) or when the limit is infinite (apify 4.0.1 _charging.py:516-523) — never min() it raw."""
    cm = actor.get_charging_manager()
    within_limit = cm.calculate_max_event_charge_count_within_limit(event)
    caps = [c for c in (input_cap or None, within_limit) if c is not None]
    return min(caps) if caps else None


class Run:
    def __init__(
        self, actor: Any, settings: Settings, *, memory_mb: int, work_dir: Path = WORK_DIR
    ):
        self.actor = actor
        self.settings = settings
        self.memory_mb = memory_mb
        self.work_dir = work_dir
        self.cap_bytes = min(settings.max_pdf_mb, max(1, memory_mb // 4)) * 1024 * 1024
        self.wall_limit = WALL_LIMIT_OCR if settings.ocr else WALL_LIMIT_TEXT
        self.gate = HostGate()
        self.seen_urls: set[str] = set()
        self.seen_ids: set[str] = set()
        self.pages_delivered = 0
        self.pages_charged = 0
        self.ocr_charged = 0
        self.documents = 0
        self.unprocessed = 0
        self.stopped: str | None = None

    # ----------------------------------------------------------------- orchestration

    async def run(self) -> None:
        pending = iter(self.settings.urls)
        async with make_client() as client:

            async def worker() -> None:
                for url in pending:
                    await self.process(client, url)

            await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))

    def summary(self) -> str:
        if self.stopped == "run_timeout":
            return f"Stopped before run timeout; {self.unprocessed} documents not processed"
        text = (
            f"Done: {self.documents} documents, {self.pages_delivered} pages delivered, "
            f"{self.pages_charged} page + {self.ocr_charged} OCR page events charged"
        )
        if self.stopped == "budget_exhausted":
            text += f"; stopped on budget, {self.unprocessed} documents not processed"
        return text

    def stop_reason(self) -> str | None:
        if self.stopped:
            return self.stopped
        if self.settings.max_pages and self.pages_delivered >= self.settings.max_pages:
            self.stopped = "budget_exhausted"
        timeout_at = getattr(self.actor.configuration, "timeout_at", None)
        if timeout_at is not None:
            if timeout_at.tzinfo is None:
                timeout_at = timeout_at.replace(tzinfo=UTC)
            remaining = (timeout_at - datetime.now(UTC)).total_seconds()
            if remaining < self.wall_limit + TIMEOUT_MARGIN:
                self.stopped = "run_timeout"
        return self.stopped

    def page_budget(self) -> int | None:
        remaining = None
        if self.settings.max_pages:
            remaining = max(0, self.settings.max_pages - self.pages_delivered)
        return charge_cap(self.actor, remaining, PAGE_EVENT) if remaining != 0 else 0

    # ----------------------------------------------------------------- one document

    async def process(self, client: Any, url: str) -> None:
        started = datetime.now(UTC)
        stop = self.stop_reason()
        if stop:
            self.unprocessed += 1
            await self.push_document(url, None, None, error_code=stop, started=started)
            return
        if url in self.seen_urls:
            await self.push_document(url, None, None, error_code="duplicate", started=started)
            return
        self.seen_urls.add(url)
        fetched = await download(
            client, url, gate=self.gate, cap_bytes=self.cap_bytes, work_dir=self.work_dir
        )
        if fetched.error_code:
            await self.push_document(
                url, fetched, None, error_code=fetched.error_code, started=started
            )
            return
        if fetched.document_id in self.seen_ids:
            fetched.path.unlink(missing_ok=True)
            await self.push_document(url, fetched, None, error_code="duplicate", started=started)
            return
        self.seen_ids.add(fetched.document_id)
        try:
            budget = self.page_budget()
            if budget == 0:
                self.stopped = "budget_exhausted"
                self.unprocessed += 1
                await self.push_document(
                    url, fetched, None, error_code="budget_exhausted", started=started
                )
                return
            options = Options(
                document_id=fetched.document_id,
                work_dir=self.work_dir,
                password=self.settings.password,
                page_range=self.settings.page_range,
                max_pages_per_pdf=self.settings.max_pages_per_pdf,
                page_budget=budget,
                include_markdown=self.settings.include_markdown,
                extract_tables=self.settings.extract_tables,
                ocr=self.settings.ocr,
                ocr_language=self.settings.ocr_language,
                max_ocr_pages=charge_cap(
                    self.actor, self.settings.max_ocr_pages_per_pdf, OCR_EVENT
                ),
            )
            parsed = await parse_in_child(
                fetched.path, options, memory_mb=self.memory_mb, wall_limit=self.wall_limit
            )
        finally:
            for leftover in self.work_dir.glob(f"{fetched.document_id}*"):
                leftover.unlink(missing_ok=True)
        if parsed.get("tablesUnavailable"):
            log_event(
                "tables_unavailable", host=_host(fetched.final_url), document_id=fetched.document_id
            )
        code = parsed.get("errorCode")
        if code in FATAL_CODES:
            log_event(
                "document_error",
                host=_host(fetched.final_url),
                document_id=fetched.document_id,
                error=code,
                detail=parsed.get("exception"),
            )
            await self.push_document(url, fetched, parsed, error_code=code, started=started)
            return
        await self.deliver(url, fetched, parsed, started)

    async def deliver(self, url: str, fetched: Any, parsed: dict, started: datetime) -> None:
        pages = parsed["pages"]
        mode = self.settings.output_mode
        base = {"url": url, "documentId": fetched.document_id}
        delivered = charged_pages = charged_ocr = 0
        error_code = parsed.get("errorCode")
        if mode == "page":
            for kind, group in groupby(pages, key=_page_kind):
                rows = [self.page_row(base, p) for p in group]
                if kind == "free":
                    await self.actor.push_data(rows)
                    delivered += len(rows)
                    continue
                event = OCR_EVENT if kind == "ocr" else PAGE_EVENT
                landed, charged, limit = await self.push_charged(rows, event)
                delivered += landed
                if kind == "ocr":
                    charged_ocr += charged
                else:
                    charged_pages += charged
                if limit:
                    self.stopped = "budget_exhausted"
                    break
            if delivered < len(pages):
                error_code = "budget_exhausted"
        elif mode == "chunk":
            chunks = chunk_pages(pages, self.settings.chunk_size, self.settings.chunk_overlap)
            if chunks:
                await self.actor.push_data([{"recordType": "chunk", **base, **c} for c in chunks])
            delivered = len(pages)
            charged_pages, charged_ocr = await self.charge_pages(pages)
        else:
            text, ocr = _billable(pages)
            # The charge follows the push, so the row carries the counts the charge will request.
            row = self.document_row(
                url, fetched, parsed, error_code=error_code, started=started,
                pages_extracted=len(pages), pages_charged=text, ocr_charged=ocr,
            )  # fmt: skip
            row.update(self.document_body(parsed))
            if len(json.dumps(row)) > MAX_ITEM_BYTES:
                row["markdown"] = None
                for page in row["pages"]:
                    page.pop("text", None)
                row["status"] = "partial"
                row["errorCode"] = error_code = "item_too_large"
            try:
                await self.actor.push_data(row)
            except ValueError:
                # The SDK refuses an item over its 9 MB limit before any request; `text` alone
                # can exceed it. Deliver the free summary as an error row instead of crashing.
                await self.push_document(
                    url, fetched, parsed, error_code="item_too_large", started=started
                )
                return
            delivered = len(pages)
            charged_pages, charged_ocr = await self.charge_pages(pages)
        self.pages_delivered += delivered
        self.pages_charged += charged_pages
        self.ocr_charged += charged_ocr
        self.documents += 1
        log_event(
            "document",
            host=_host(fetched.final_url),
            document_id=fetched.document_id,
            pages=parsed["pageCount"],
            delivered=delivered,
            charged=charged_pages,
            ocr=charged_ocr,
            error=error_code,
        )
        if mode == "document":
            return
        await self.push_document(
            url, fetched, parsed, error_code=error_code, started=started,
            pages_extracted=delivered, pages_charged=charged_pages, ocr_charged=charged_ocr,
        )  # fmt: skip

    # ----------------------------------------------------------------- charging

    async def push_charged(self, rows: list[dict], event: str) -> tuple[int, int, bool]:
        """Push rows under `event`. Returns (rows landed, events charged, limit reached).

        On a pay-per-event run the SDK pushes only what it can charge and charges exactly
        that many, so `charged_count` is the number of rows that landed. On a non-PPE run
        (local `apify run`, FREE record) charged is 0, the limit is never reached and every
        row landed."""
        result = await self.actor.push_data(rows, charged_event_name=event)
        charged = getattr(result, "charged_count", 0) or 0
        limit = bool(getattr(result, "event_charge_limit_reached", False))
        landed = len(rows) if (charged == 0 and not limit) else min(charged, len(rows))
        return landed, charged, limit

    async def charge_pages(self, pages: list[dict]) -> tuple[int, int]:
        """Document and chunk mode: rows are already pushed; charge by count, after the push."""
        text, ocr = _billable(pages)
        charged = {PAGE_EVENT: 0, OCR_EVENT: 0}
        for event, count in ((PAGE_EVENT, text), (OCR_EVENT, ocr)):
            if count <= 0:
                continue
            result = await self.actor.charge(event, count=count)
            charged[event] = getattr(result, "charged_count", 0) or 0
            if getattr(result, "event_charge_limit_reached", False):
                self.stopped = "budget_exhausted"
        return charged[PAGE_EVENT], charged[OCR_EVENT]

    # ----------------------------------------------------------------- rows

    @staticmethod
    def page_row(base: dict, page: dict) -> dict:
        row = {"recordType": "page", **base}
        row.update({k: v for k, v in page.items() if k != "headings"})
        return row

    @staticmethod
    def document_body(parsed: dict) -> dict:
        pages = parsed["pages"]
        text = "\n\n".join(p["text"] for p in pages)
        markdown = "\n\n".join(p["markdown"] for p in pages if p["markdown"]) or None
        tables = [{"page": p["page"], "rows": t} for p in pages for t in (p["tables"] or [])]
        return {
            "text": text,
            "markdown": markdown,
            "pages": [
                {
                    "page": p["page"],
                    "text": p["text"],
                    "charCount": p["charCount"],
                    "ocrApplied": p["ocrApplied"],
                }
                for p in pages
            ],
            "tables": tables or None,
            "charCount": len(text),
            "wordCount": len(text.split()),
        }

    def document_row(
        self,
        url: str,
        fetched: Any,
        parsed: dict | None,
        *,
        error_code: str | None,
        started: datetime,
        pages_extracted: int = 0,
        pages_charged: int = 0,
        ocr_charged: int = 0,
    ) -> dict:
        parsed = parsed or {}
        if error_code is None:
            status = "ok"
        elif pages_extracted > 0:
            status = "partial"
        else:
            status = "error"
        return {
            "recordType": "document",
            "url": url,
            "finalUrl": fetched.final_url if fetched else url,
            "documentId": fetched.document_id if fetched else None,
            "fileName": fetched.file_name if fetched else None,
            "status": status,
            "errorCode": error_code,
            "httpStatus": fetched.http_status if fetched else None,
            "contentType": fetched.content_type if fetched else None,
            "bytes": fetched.bytes if fetched else 0,
            "pageCount": parsed.get("pageCount"),
            "pagesExtracted": pages_extracted,
            "pagesCharged": pages_charged,
            "ocrPagesCharged": ocr_charged,
            "imageOnlyPages": parsed.get("imageOnlyPages", 0),
            "metadata": parsed.get("metadata") or dict(EMPTY_METADATA),
            "encrypted": parsed.get("encrypted"),
            "permissionsCopyAllowed": parsed.get("permissionsCopyAllowed"),
            "durationMs": int((datetime.now(UTC) - started).total_seconds() * 1000),
            "fetchedAt": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

    async def push_document(
        self,
        url: str,
        fetched: Any,
        parsed: dict | None,
        *,
        error_code: str | None,
        started: datetime,
        **counts: int,
    ) -> None:
        row = self.document_row(
            url, fetched, parsed, error_code=error_code, started=started, **counts
        )
        if fetched is None or not counts:
            log_event(
                "document",
                host=_host(url),
                document_id=row["documentId"],
                status=row["status"],
                error=error_code,
            )
        await self.actor.push_data(row)


def _page_kind(page: dict) -> str:
    """`ocr` → ocr-page event; `page` → page event; `free` → image-only page not OCRed."""
    if page["ocrApplied"]:
        return "ocr"
    return "free" if page["needsOcr"] else "page"


def _billable(pages: list[dict]) -> tuple[int, int]:
    """(text pages charged as `page`, OCR pages charged as `ocr-page`); needsOcr rows are free."""
    kinds = [_page_kind(p) for p in pages]
    return kinds.count("page"), kinds.count("ocr")


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "?").lower()
    except ValueError:
        return "?"


async def run_actor(actor: Any, *, work_dir: Path = WORK_DIR) -> Run | None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    raw = await actor.get_input() or {}
    try:
        settings = parse_input(raw)
    except ValueError as exc:
        await actor.fail(exit_code=1, status_message=f"Invalid input: {exc}")
        return None
    memory = getattr(actor.configuration, "memory_mbytes", None) or 1024
    run = Run(actor, settings, memory_mb=int(memory), work_dir=work_dir)
    await run.run()
    await actor.set_status_message(run.summary())
    return run


async def main() -> None:
    from apify import Actor

    async with Actor:
        await run_actor(Actor)


if __name__ == "__main__":
    asyncio.run(main())
