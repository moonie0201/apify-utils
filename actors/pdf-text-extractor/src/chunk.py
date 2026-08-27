"""RAG chunks (spec §2.4 step 9): concatenate page texts, split at paragraph, then sentence,
then hard cut at chunkSize; overlap the previous tail (clamped to chunkSize // 2); record
page and character offsets and the markdown heading stack at chunkStart."""

from __future__ import annotations

import re

PAGE_SEPARATOR = "\n\n"
_SENTENCE_END = re.compile(r"[.!?][\"')\]]?(?:\s)")


def _find_cut(text: str, start: int, end: int) -> int:
    lo = start + (end - start) // 2
    window = text[lo:end]
    para = window.rfind("\n\n")
    if para != -1:
        return lo + para + 2
    last = None
    for match in _SENTENCE_END.finditer(window):
        last = match.end()
    if last is not None:
        return lo + last
    line = window.rfind("\n")
    if line != -1:
        return lo + line + 1
    space = window.rfind(" ")
    if space != -1:
        return lo + space + 1
    return end


def chunk_pages(pages: list[dict], chunk_size: int, overlap: int) -> list[dict]:
    """pages: [{page, text, headings:[{level,text}]}] in page order. Returns chunk rows
    (without url/documentId), each with chunkIndex/chunkCount/pageStart/pageEnd/charStart/
    charEnd/text/tokenEstimate/headingPath."""
    overlap = max(0, min(overlap, chunk_size // 2))
    full_parts: list[str] = []
    bounds: list[tuple[int, int, int]] = []  # (page, start, end)
    heading_events: list[tuple[int, int, str]] = []  # (offset, level, text)
    offset = 0
    for page in pages:
        text = page["text"]
        if full_parts:
            offset += len(PAGE_SEPARATOR)
        start = offset
        for heading in page.get("headings") or []:
            at = text.find(heading["text"])
            heading_events.append(
                (start + (at if at >= 0 else 0), heading["level"], heading["text"])
            )
        full_parts.append(text)
        offset += len(text)
        bounds.append((page["page"], start, offset))
    full = PAGE_SEPARATOR.join(full_parts)
    n = len(full)
    chunks: list[dict] = []
    start = 0
    while start < n:
        while start < n and full[start] in " \n":
            start += 1
        if start >= n:
            break
        end = min(start + chunk_size, n)
        if end < n:
            end = _find_cut(full, start, end)
        text = full[start:end].rstrip()
        real_end = start + len(text)
        chunks.append(
            {
                "pageStart": _page_at(bounds, start),
                "pageEnd": _page_at(bounds, max(real_end - 1, start)),
                "charStart": start,
                "charEnd": real_end,
                "text": text,
                "tokenEstimate": len(text) // 4,
                "headingPath": _heading_path(heading_events, start),
            }
        )
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    for index, chunk in enumerate(chunks):
        chunk["chunkIndex"] = index
        chunk["chunkCount"] = len(chunks)
    return chunks


def _page_at(bounds: list[tuple[int, int, int]], offset: int) -> int:
    page = bounds[0][0] if bounds else 1
    for number, start, end in bounds:
        if offset >= start:
            page = number
        if offset < end:
            break
    return page


def _heading_path(events: list[tuple[int, int, str]], offset: int) -> list[str]:
    stack: list[tuple[int, str]] = []
    for at, level, text in events:
        if at > offset:
            break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
    return [text for _level, text in stack]
