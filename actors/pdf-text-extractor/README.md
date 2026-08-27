# PDF Text & Table Extractor — Markdown, OCR

> **Unofficial tool.** Not affiliated with, endorsed by or sponsored by Google (PDFium) or the
> maintainers of Tesseract. Built with PDFium (BSD-3) and Tesseract (Apache-2.0); all trademarks
> belong to their owners. **Removal requests:**
> [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) — honoured in
> 48 hours. **Privacy:** [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).

Paste PDF URLs, get text back — as one row per page, one row per document, or as RAG chunks
with page and character offsets. PDFium does the reading: a 142-page US tax publication came
back in 1.2 s in our measurements. Optional heuristic markdown, ruled-table extraction and
Tesseract OCR for scanned pages ride along in the same Actor. You pay **per page delivered**;
the document summary row, error rows, duplicates and pages you skipped cost nothing.

## What it extracts

| Content | Delivered? | Notes |
|---|---|---|
| Text layer | Yes | PDFium text extraction, `\n` line breaks, Unicode NFC |
| Markdown | Yes | Headings from font-size rank, paragraphs from line gaps — a heuristic, not a layout model |
| Ruled tables | Yes | Tables drawn with ruling lines, as arrays of rows and as GitHub tables in the markdown |
| Borderless tables | No | Borderless tables stay as text |
| OCR (English) | Opt-in | Tesseract on image-only pages, charged as `ocr-page` |
| Images | No | Not exported |
| Form fields | No | Field values are not read |
| LLM post-processing | No | Deterministic: identical bytes and input give identical charged rows |

## How to use

1. **Paste direct PDF links** into `urls`, one per line — the file itself, not a viewer page.
2. **Pick an output mode.** `page` for spreadsheets and Google Sheets, `document` for one item per
   file, `chunk` for a vector store. Turn on `extractTables` or `ocr` if you need them.
3. **Run it.** Set `maxPages` (default 5,000) or the run's maximum charge to bound the spend. The
   input form shows every option with its default; the prefill runs the six-page IRS Form W-9.

## Output modes

**Page** (`recordType: "page"`, charged) — one row per page. The example is page 1 of the IRS
Form W-9 (`https://www.irs.gov/pub/irs-pdf/fw9.pdf`, a US-government public-domain work,
6 pages, 141 KB) exactly as the prefill run produced it; only `text` and `markdown` are cut short:

```json
{
  "recordType": "page",
  "url": "https://www.irs.gov/pub/irs-pdf/fw9.pdf",
  "documentId": "2d420cbb4123dcf1",
  "page": 1,
  "pageCount": 6,
  "text": "Form W-9\n(Rev. March 2024)\nRequest for Taxpayer \nIdentification Number and Certification\nDepartment of the Treasury \nInternal Revenue Service ...",
  "markdown": "# Form W-9\n\n(Rev. March 2024)\n\n## Request for Taxpayer\n\n## Identification Number and Certification\n\nDepartment of the Treasury Internal Revenue Service ...",
  "tables": null,
  "tableCount": 0,
  "charCount": 5704,
  "wordCount": 933,
  "width": 611.98,
  "height": 791.97,
  "rotation": 0,
  "hasTextLayer": true,
  "ocrApplied": false,
  "needsOcr": false
}
```

**Document** (`recordType: "document"`) — one row per URL. It is always written, free, as the last
row for that document, and carries `status`, `errorCode`, `documentId`, `fileName`, HTTP status,
byte size, page counts, PDF metadata, `encrypted`, `permissionsCopyAllowed`, `durationMs` and
`fetchedAt`. In `outputMode: document` the same row also carries `text`, `markdown`,
`pages[{page, text, charCount, ocrApplied}]`, `tables`, `charCount` and `wordCount`, and is charged
by page count after it has been pushed. A viewer page instead of a PDF comes back like this:

```json
{
  "recordType": "document",
  "url": "https://docs.google.com/viewer?url=...",
  "status": "error",
  "errorCode": "not_pdf",
  "httpStatus": 200,
  "contentType": "text/html; charset=utf-8",
  "bytes": 0,
  "pageCount": null,
  "pagesExtracted": 0,
  "pagesCharged": 0
}
```

**Chunk** (`recordType: "chunk"`) — `chunkSize` characters (default 1,500 ≈ 375 tokens), split at
paragraph, then sentence, then hard cut, with `chunkOverlap` repeated from the previous tail.
Each row has `chunkIndex`, `chunkCount`, `pageStart`, `pageEnd`, `charStart`, `charEnd`,
`tokenEstimate` and `headingPath` (the nearest markdown headings above the chunk). Chunks are
charged per **page covered**, so the chunk size never changes the bill.

Error codes on document rows: `blocked_url`, `download_failed`, `too_large`, `not_pdf`,
`password_required`, `permissions_restricted`, `malformed`, `timeout`, `pages_capped`,
`budget_exhausted`, `run_timeout`, `item_too_large`, `duplicate`. Two dataset views are provided:
**Pages** and **Documents**.

## Coming from memo23, automation-lab or gochujang

| Their input | Here |
|---|---|
| `urls` | `urls` (same) |
| `pageRange` (memo23) | `pageRange` (same syntax: `1-5, 8, 12-`) |
| `startPage` / `endPage` | `pageRange` |
| `ocrScannedPages` / `enableOcr` | `ocr` |
| `maxPdfSizeMb` / `maxPdfMb` | `maxPdfMb` (same) |

**What you gain:** per-page billing instead of per-file, ruled tables, RAG chunks with offsets,
a `documentId` for deduplication, typed error codes on free rows, and `hasTextLayer` /
`needsOcr` flags so you know before paying for OCR.

**What you lose, stated plainly:** no file upload or base64 input (URLs only), no proxy retry on
a refused download, English OCR only, and flat-price Actors are cheaper for long text documents —
see Pricing.

## Pricing

**$0.0003 per page, $0.003 per OCR page, no start fee.** Free, always: document summary rows,
error rows, duplicate rows, pages outside `pageRange`, pages beyond `maxPagesPerPdf` or
`maxPages`, image-only pages that were not OCRed (`ocr` off, `maxOcrPagesPerPdf` reached or
Tesseract failed — delivered empty with `needsOcr: true`), and documents never reached because
the run stopped on budget or before its timeout.

Worked examples, plain arithmetic:

| Job | Pages | Cost |
|---|---|---|
| 2-page invoice | 2 × $0.0003 | **$0.0006** |
| 10-page paper | 10 × $0.0003 | **$0.003** |
| 100-page manual | 100 × $0.0003 | **$0.03** |
| 300-page manual (`maxPagesPerPdf: 300`) | 300 × $0.0003 | **$0.09** |
| 20 scanned pages with OCR | 20 × $0.003 | **$0.06** |

Comparable Actors on the Store, public prices only, as of 2026-08-28:

| Actor | Model | 2 pp | 10 pp | 100 pp | 20 pp OCR |
|---|---|---|---|---|---|
| this Actor | $0.0003/page, $0.003/OCR page, no start fee | $0.0006 | $0.003 | $0.03 | $0.06 |
| memo23 `pdf-text-extractor` | $0.005 start + $0.005/PDF + $0.015/OCR page | $0.01 | $0.01 | $0.01 | $0.31 |
| gochujang `pdf-text-extractor` | $0.001 start + $0.02/PDF + $0.0005/page | $0.022 | $0.026 | $0.071 | $0.031 (no OCR event) |
| automation-lab `pdf-text-extractor` | price not public | — | — | — | — |

Per-page pricing wins on short documents, on runs where many downloads fail or are skipped, and
on OCR. For text documents longer than about 17 pages memo23's flat price is lower. Set
`maxPagesPerPdf` (default 100) and `maxPages` to keep long documents within the budget you
intend; when the run's maximum charge is reached the Actor stops pushing, writes free
`budget_exhausted` rows for what it did not reach, and still finishes successfully.

## Security and limits

- Private and internal URLs are refused before any request (`blocked_url`): only http/https on
  ports 80, 443, 8080 and 8443, no localhost, no `.internal` / `.local`, no private, loopback,
  link-local, multicast or reserved addresses. The resolved address is pinned for the request,
  so DNS cannot be swapped between the check and the download; redirects are re-checked on
  every hop, at most five, never from https to http. Hostnames listed in the Actor's
  `blocklist.txt` (removal requests, see TAKEDOWN.md) are refused the same way, before any
  request.
- Files over `maxPdfMb` (default 50 MB, maximum 100 MB) are refused on Content-Length and
  aborted during download. The effective cap is also bounded by the run's memory (memory / 4).
  Each download hop has a 70-second wall (10 s connect + 60 s body); a server that stalls or
  trickles bytes gets a free `timeout` row instead of holding the run.
- Each document is parsed in its own memory-capped subprocess with a wall-clock limit (120 s,
  600 s with OCR), so a hostile or broken file becomes one free `malformed` or `timeout` row
  and the run continues.
- One plain GET per URL, one download in flight per host and at least one second between
  requests to the same host, no retry on 401, 403 or 429, no proxy, no cookies (a `Set-Cookie`
  is dropped, never replayed), no connection reuse between hosts, no login.
- No DRM or paywall bypass. Owner-restricted (no-copy) PDFs are skipped with
  `permissions_restricted`; there is no override. User-password-protected files need
  `pdfPassword`.
- 100 URLs per run, 100 pages per PDF by default (up to 2,000), 2 documents in flight.

## Privacy

The output contains whatever your documents contain. If a document holds personal data, you
are the controller of the resulting dataset. The Actor opens no outbound connection to the
developer and embeds no developer credential, so the Actor T&C creator-access clause is not
exercised; your URL list is visible to us only through Apify's Console if you open an issue
about a run. The file exists only inside the run container (memory or its ephemeral disk) and is
gone when the run ends; the dataset lives in your Apify account under Apify's retention — delete
the run to erase it. We do not log URLs or document text.

## For AI agents and MCP

Use `outputMode: "document"` for one call → one item with the full text, or `"chunk"` to feed a
vector store directly. `maxPages` and the run's maximum charge are honoured **before**
extraction, so an agent never pays for pages it cannot receive, and with `ocr` off there is a
single price to reason about. Errors are typed rows, not exceptions.

```json
{ "urls": ["https://www.irs.gov/pub/irs-pdf/fw9.pdf"], "outputMode": "document", "maxPages": 50 }
```

## Limitations you should know before you buy

- Ruled tables only; borderless tables stay as text. Table detection adds about 0.3–0.5 s per page.
- Multi-column reading order follows the PDF's content stream. PDFium kept column order on a
  two-column paper in our tests, but there is no guarantee.
- Headings are a font-size heuristic; a document with a single font size gets no headings.
- OCR measured 93–97 % similarity on clean 150–200 dpi scans of printed English; worse on
  handwriting, skew or low resolution. English only.
- Encrypted files need `pdfPassword`; owner-restricted files are always skipped.
- No file upload; URLs only. 100 URLs per run; 100 pages per PDF by default (up to 2,000); one
  request per host per second.
- The run-timeout guard stops before the platform timeout and writes free `run_timeout` rows,
  so a run never ends as TIMED-OUT. At defaults that is roughly 100 text PDFs or 20 OCR PDFs
  per run.
- A document-mode row over 8 MB drops `markdown` and `pages[].text` (`errorCode:
  item_too_large`); `text` is kept, so the page charge stands. If `text` alone still exceeds
  the platform's 9 MB item limit, the row is delivered as a free `item_too_large` error row
  without content and nothing is charged — use `page` or `chunk` mode for such documents.
- Image size: 628 MB as measured on the first build (PDFium, pdfplumber, Tesseract and the
  English language pack).

## FAQ

**Is this legal?** The Actor sends one plain GET per URL you supply, with an identifying
User-Agent, no login, no proxy and no retry on a refusal. The text belongs to the document's
copyright owner: you must have the right to download and process each file, and you must follow
the hosting site's terms and robots rules. Owner-restricted PDFs are skipped. Rights-holders
who want a file excluded from future runs are pointed at the hosting site or the user who
supplied the URL — see
[TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md).

**A scanned PDF returns empty text?** Turn on `ocr`. Image-only pages are delivered empty and
free with `needsOcr: true` while it is off.

**How do I know what I will pay before paying for OCR?** Run once with `ocr` off: every page row
carries `hasTextLayer` and `needsOcr`, and the document row counts `imageOnlyPages`.

**Google Drive link?** Use the direct-download form of the link. A viewer page is HTML and comes
back as a free `not_pdf` row.

**How do I dedupe?** `documentId` is a hash of the file bytes: the same file behind two URLs
gets one set of charged rows and a free `duplicate` row.

**What is `pageRange` syntax?** `1-5, 8, 12-` — 1-based, `12-` means 12 to the end. Pages
outside the range are never charged.

## Support

Open an issue on the Actor's Issues tab or in the public repository at
<https://github.com/moonie0201/apify-utils>. Every issue gets a reply within 14 days, usually
within 48 hours. Bug reports that include the run id and the input are fixed fastest. Removal,
takedown, copyright or privacy requests jump the queue: **mooniegilog@gmail.com** ·
[TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) ·
[PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).

## Disclaimer

You are responsible for having the right to download and process each file and for the hosting
site's terms and robots rules. The Actor sends one plain GET per URL with an identifying
User-Agent, no login, no proxy and no retry on a refusal; it bypasses no DRM, paywall or
owner restriction. This tool is unofficial and is not affiliated with, endorsed by or sponsored
by Google (PDFium), the Tesseract maintainers or any site whose files you process. All
trademarks belong to their respective owners.
