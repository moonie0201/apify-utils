"""Markdown from a PDFium text page (spec §2.4 step 6).

Per-char font size and baseline from the raw API; lines grouped by baseline; the document
body size is the modal font size; a line ≥ 1.15× body and ≤ 120 chars is a heading (`#` for
the largest distinct heading size, `##` next, `###` for the rest); a vertical gap > 1.5× line
height starts a new paragraph; ruled tables are inserted as GFM at their page position and
the text inside them is not repeated.

# ponytail: font-size heuristic, no layout model; upgrade path = pdfplumber words + column
# clustering.
"""

from __future__ import annotations

import ctypes
import math
from collections import Counter
from dataclasses import dataclass, field

import pypdfium2.raw as pdfium_c

HEADING_RATIO = 1.15
HEADING_MAX_CHARS = 120
PARAGRAPH_GAP = 1.5
BASELINE_TOLERANCE = 0.5


@dataclass
class Line:
    y: float
    size: float
    x0: float
    x1: float
    key: float = 0.0  # grouping coordinate: the baseline y, or x for vertically running text
    rotated: bool = False
    chars: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        # PDFium hands out UTF-16 code units: pair the surrogate halves, drop a lone half
        # (a broken ToUnicode CMap) so the row stays JSON/UTF-8 encodable.
        raw = "".join(self.chars)
        if any("\ud800" <= c <= "\udfff" for c in raw):
            raw = raw.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")
        return raw.strip()


@dataclass
class Table:
    rows: list[list[str]]
    x0: float
    y_bottom: float
    x1: float
    y_top: float  # PDF coordinates, origin bottom-left


def page_lines(textpage) -> list[Line]:
    """Group the text page's chars into lines by baseline."""
    raw = textpage.raw
    count = pdfium_c.FPDFText_CountChars(raw)
    x = ctypes.c_double()
    y = ctypes.c_double()
    matrix = pdfium_c.FS_MATRIX()
    lines: list[Line] = []
    current: Line | None = None
    for index in range(count):
        code = pdfium_c.FPDFText_GetUnicode(raw, index)
        if code in (0x0A, 0x0D):
            current = None
            continue
        ch = chr(code) if code else ""
        pdfium_c.FPDFText_GetCharOrigin(raw, index, ctypes.byref(x), ctypes.byref(y))
        size = float(pdfium_c.FPDFText_GetFontSize(raw, index))
        # Many producers set `Tf 1` and scale through the text matrix; the effective size is
        # the font size times the matrix's vertical scale (length of the mapped up-vector).
        rotated = False
        if pdfium_c.FPDFText_GetMatrix(raw, index, ctypes.byref(matrix)):
            size *= math.hypot(matrix.c, matrix.d) or 1.0
            rotated = abs(matrix.b) > abs(matrix.a)  # text runs along the y axis
        key = x.value if rotated else y.value
        if (
            current is None
            or current.rotated != rotated
            or abs(current.key - key) > max(BASELINE_TOLERANCE, current.size * 0.3)
        ):
            current = Line(y=y.value, size=size, x0=x.value, x1=x.value, key=key, rotated=rotated)
            lines.append(current)
        current.chars.append(ch)
        current.x0 = min(current.x0, x.value)
        current.x1 = max(current.x1, x.value)
        current.size = max(current.size, size) if size > 0 else current.size
    return [line for line in lines if line.text]


def body_size(lines: list[Line]) -> float:
    counts: Counter[float] = Counter()
    for line in lines:
        counts[round(line.size * 2) / 2] += len(line.text)
    if not counts:
        return 0.0
    # Largest count wins; ties go to the smaller size (body text, not a big heading).
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def heading_levels(lines: list[Line], body: float) -> dict[float, int]:
    sizes = sorted(
        {round(line.size * 2) / 2 for line in lines if _is_heading(line, body)}, reverse=True
    )
    return {size: min(rank + 1, 3) for rank, size in enumerate(sizes)}


def _is_heading(line: Line, body: float) -> bool:
    return body > 0 and line.size >= body * HEADING_RATIO and len(line.text) <= HEADING_MAX_CHARS


def _inside(line: Line, table: Table) -> bool:
    return (
        table.y_bottom - 2 <= line.y <= table.y_top + 2
        and line.x0 >= table.x0 - 2
        and line.x1 <= table.x1 + 2
    )


def gfm_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    cells = [[_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(cells[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in cells[1:]]
    return "\n".join(out)


def _cell(value: str | None) -> str:
    return " ".join((value or "").split()).replace("|", "\\|")


def build_markdown(lines: list[Line], tables: list[Table] | None = None) -> tuple[str, list[dict]]:
    """Returns (markdown, headings) where headings are [{level, text}] in page order."""
    tables = sorted(tables or [], key=lambda t: -t.y_top)
    body = body_size(lines)
    levels = heading_levels(lines, body)
    blocks: list[str] = []
    headings: list[dict] = []
    paragraph: list[str] = []
    previous: Line | None = None
    pending = list(tables)

    def flush() -> None:
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()

    for line in lines:
        if any(_inside(line, t) for t in tables):
            continue
        while pending and pending[0].y_top >= line.y:
            flush()
            blocks.append(gfm_table(pending.pop(0).rows))
            previous = None
        if previous is not None:
            gap = previous.y - line.y
            if gap < 0 or gap > PARAGRAPH_GAP * max(previous.size, line.size):
                flush()
        level = levels.get(round(line.size * 2) / 2) if _is_heading(line, body) else None
        if level:
            flush()
            blocks.append("#" * level + " " + line.text)
            headings.append({"level": level, "text": line.text})
        else:
            paragraph.append(line.text)
        previous = line
    flush()
    for table in pending:
        blocks.append(gfm_table(table.rows))
    return "\n\n".join(blocks), headings
