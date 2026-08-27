"""Synthetic PDF fixtures (spec §2.8): a minimal PDF writer on the stdlib, so no copyrighted
document is ever committed. Run as a script to write every fixture into tests/fixtures/, or
call `build_all(dir)` from conftest. Encryption is the Standard security handler R2/RC4-40,
which is enough for pdfium to enforce the user password and the owner "no copy" flag.
"""

from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAD = bytes.fromhex(
    "28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A"
)  # the Standard security handler's 32-byte padding string
FILE_ID = bytes(range(16))
PERM_ALL = -1
PERM_NO_COPY = -1 & ~0x10 & 0xFFFFFFFF  # bit 5 clear: text copying disallowed by the owner


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


class Encryption:
    def __init__(self, user_pw: str = "", owner_pw: str = "owner", permissions: int = PERM_ALL):
        user = (user_pw.encode("latin-1") + PAD)[:32]
        owner = (owner_pw.encode("latin-1") + PAD)[:32]
        self.permissions = permissions & 0xFFFFFFFF
        okey = hashlib.md5(owner).digest()[:5]
        self.o_entry = rc4(okey, user)
        pbytes = struct.pack("<I", self.permissions)
        self.key = hashlib.md5(user + self.o_entry + pbytes + FILE_ID).digest()[:5]
        self.u_entry = rc4(self.key, PAD)

    def object_key(self, num: int, gen: int = 0) -> bytes:
        extra = struct.pack("<I", num)[:3] + struct.pack("<I", gen)[:2]
        return hashlib.md5(self.key + extra).digest()[:10]

    def encrypt(self, num: int, data: bytes) -> bytes:
        return rc4(self.object_key(num), data)

    def dictionary(self) -> bytes:
        return (
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P %d /O <%s> /U <%s> >>"
            % (struct.unpack("<i", struct.pack("<I", self.permissions))[0],
               self.o_entry.hex().encode(), self.u_entry.hex().encode())
        )  # fmt: skip


class Pdf:
    """Objects are added as raw bodies; streams as (dict, data) pairs so they can be encrypted."""

    def __init__(self, encryption: Encryption | None = None):
        self.objects: list[bytes | tuple[bytes, bytes]] = []
        self.encryption = encryption
        self.pages: list[int] = []
        self.pages_obj = self.add(b"")  # placeholder, rewritten in build()
        self.font = self.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        self.info: int | None = None

    def add(self, body: bytes | tuple[bytes, bytes]) -> int:
        self.objects.append(body)
        return len(self.objects)

    def add_stream(self, dictionary: bytes, data: bytes) -> int:
        return self.add((dictionary, data))

    def add_page(self, content: bytes, width: float = 612, height: float = 792,
                 xobjects: dict[str, int] | None = None, rotate: int = 0,
                 fonts: dict[str, int] | None = None) -> int:  # fmt: skip
        stream = self.add_stream(b"", content)
        extra_fonts = b"".join(b" /%s %d 0 R" % (k.encode(), v) for k, v in (fonts or {}).items())
        xo = b""
        if xobjects:
            xo = (
                b"/XObject << "
                + b" ".join(b"/%s %d 0 R" % (k.encode(), v) for k, v in xobjects.items())
                + b" >>"
            )
        page = self.add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Rotate %d "
            b"/Resources << /Font << /F1 %d 0 R%s >> %s >> /Contents %d 0 R >>"
            % (
                self.pages_obj,
                _num(width),
                _num(height),
                rotate,
                self.font,
                extra_fonts,
                xo,
                stream,
            )
        )
        self.pages.append(page)
        return page

    def add_image(self, image: Image.Image) -> int:
        gray = image.convert("L")
        data = zlib.compress(gray.tobytes())
        return self.add_stream(
            b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Filter /FlateDecode" % gray.size,
            data,
        )

    def set_info(self, **entries: str) -> None:
        body = (
            b"<< "
            + b" ".join(b"/%s (%s)" % (k.encode(), _escape(v)) for k, v in entries.items())
            + b" >>"
        )
        self.info = self.add(body)

    def build(self) -> bytes:
        kids = b" ".join(b"%d 0 R" % p for p in self.pages)
        self.objects[self.pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            kids,
            len(self.pages),
        )
        catalog = self.add(b"<< /Type /Catalog /Pages %d 0 R >>" % self.pages_obj)
        encrypt_obj = self.add(self.encryption.dictionary()) if self.encryption else None
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for num, obj in enumerate(self.objects, start=1):
            offsets.append(len(out))
            if isinstance(obj, tuple):
                dictionary, data = obj
                if self.encryption:
                    data = self.encryption.encrypt(num, data)
                out += b"%d 0 obj\n<< %s /Length %d >>\nstream\n" % (num, dictionary, len(data))
                out += data + b"\nendstream\nendobj\n"
            else:
                body = obj
                if self.encryption and num == self.info:
                    body = _encrypt_strings(self.encryption, num, body)
                out += b"%d 0 obj\n%s\nendobj\n" % (num, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(self.objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        file_id = FILE_ID.hex().encode()
        trailer = b"<< /Size %d /Root %d 0 R /ID [<%s> <%s>]" % (
            len(self.objects) + 1,
            catalog,
            file_id,
            file_id,
        )
        if self.info:
            trailer += b" /Info %d 0 R" % self.info
        if encrypt_obj:
            trailer += b" /Encrypt %d 0 R" % encrypt_obj
        out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
        return bytes(out)


def _num(v: float) -> bytes:
    return (b"%d" % v) if float(v).is_integer() else (b"%.2f" % v)


def _escape(s: str) -> bytes:
    return s.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _encrypt_strings(enc: Encryption, num: int, body: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i : i + 1] == b"(":
            j = body.index(b")", i)
            out += b"<" + enc.encrypt(num, body[i + 1 : j]).hex().encode() + b">"
            i = j + 1
        else:
            out.append(body[i])
            i += 1
    return bytes(out)


def text_ops(lines: list[tuple[float, float, float, str]]) -> bytes:
    """[(x, y, font_size, text)] → content stream."""
    ops = b"BT\n"
    for x, y, size, text in lines:
        ops += b"/F1 %s Tf 1 0 0 1 %s %s Tm (%s) Tj\n" % (
            _num(size),
            _num(x),
            _num(y),
            _escape(text),
        )
    return ops + b"ET\n"


def line_ops(segments: list[tuple[float, float, float, float]]) -> bytes:
    ops = b"q 0 0 0 RG 1 w\n"
    for x1, y1, x2, y2 in segments:
        ops += b"%s %s m %s %s l S\n" % (_num(x1), _num(y1), _num(x2), _num(y2))
    return ops + b"Q\n"


def grid_ops(x0: float, y0: float, cols: list[float], rows: list[float]) -> bytes:
    """Ruled grid: column widths and row heights; y0 is the bottom-left corner."""
    xs = [x0]
    for w in cols:
        xs.append(xs[-1] + w)
    ys = [y0]
    for h in rows:
        ys.append(ys[-1] + h)
    segs = [(xs[0], y, xs[-1], y) for y in ys] + [(x, ys[0], x, ys[-1]) for x in xs]
    return line_ops(segs)


def paragraph_ops(
    x: float, top: float, size: float, paragraphs: list[list[str]], leading: float | None = None
) -> bytes:
    """Paragraphs of lines; a 2× leading gap between paragraphs, 1.2× inside."""
    leading = leading or size * 1.2
    y = top
    lines = []
    for para in paragraphs:
        for line in para:
            lines.append((x, y, size, line))
            y -= leading
        y -= leading  # paragraph gap = 2× leading, i.e. > 1.5× line height
    return text_ops(lines)


def text_image(text: str, size: tuple[int, int] = (900, 160)) -> Image.Image:
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    draw.text((30, 40), text, fill=0, font=ImageFont.load_default(size=48))
    return img


# --------------------------------------------------------------------------- fixtures


def simple_text() -> bytes:
    pdf = Pdf()
    pdf.set_info(Title="Fixture Title", Author="Fixture Author", Subject="Testing", Keywords="a, b",
                 Creator="make_fixtures", Producer="stdlib", CreationDate="D:20240102030405+02'00'",
                 ModDate="D:20240102030405Z")  # fmt: skip
    pdf.add_page(paragraph_ops(72, 720, 11, [
        ["The quick brown fox jumps over the lazy dog.", "Second line of the first paragraph."],
        ["A second paragraph starts here after a gap.", "It also has two lines of text."],
    ]))  # fmt: skip
    pdf.add_page(paragraph_ops(72, 720, 11, [["Page two has a single short paragraph."]]))
    pdf.add_page(paragraph_ops(72, 720, 11, [["Page three closes the document."]]), rotate=90)
    return pdf.build()


def three_fonts() -> bytes:
    pdf = Pdf()
    body = [
        "Body text line one with the modal font size of the document.",
        "Body text line two keeps the same size so it is the mode.",
        "Body text line three, still ten points, still the body.",
    ]
    content = text_ops([(72, 740, 24, "Document Title")]) + text_ops([(72, 700, 16, "Section One")])
    content += paragraph_ops(72, 670, 10, [body, body])
    content += text_ops([(72, 520, 16, "Section Two")]) + text_ops([(72, 495, 13, "Subsection")])
    content += paragraph_ops(72, 470, 10, [body])
    pdf.add_page(content)
    return pdf.build()


def rotated_text() -> bytes:
    """A vertical sentence (text matrix rotated 90 degrees) beside normal lines."""
    pdf = Pdf()
    content = text_ops(
        [(72, 740, 11, "Horizontal line one."), (72, 720, 11, "Horizontal line two.")]
    )
    content += (
        b"BT\n/F1 9 Tf 0 1 -1 0 40 500 Tm (Print or type. See instructions on page 3.) Tj\nET\n"
    )
    pdf.add_page(content)
    return pdf.build()


def ruled_table() -> bytes:
    pdf = Pdf()
    content = text_ops([(72, 740, 12, "Table below")])
    content += grid_ops(72, 600, [100, 100, 100], [24, 24, 24])
    cells = []
    values = [["A1", "B1", "C1"], ["A2", "B2", "C2"], ["Name", "Qty", "Price"]]
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            cells.append((72 + c * 100 + 6, 600 + r * 24 + 8, 10, value))
    content += text_ops(cells)
    content += text_ops([(72, 560, 12, "Text after the table")])
    pdf.add_page(content)
    return pdf.build()


def two_by_one() -> bytes:
    """One column, two rows: the false positive pdfplumber reports on Census p9."""
    pdf = Pdf()
    content = grid_ops(72, 600, [200], [24, 24])
    content += text_ops(
        [(78, 608, 10, "only"), (78, 632, 10, "cells"), (72, 700, 12, "Heading text")]
    )
    pdf.add_page(content)
    return pdf.build()


def image_only(text: str = "HELLO OCR WORLD 12345") -> bytes:
    pdf = Pdf()
    img = pdf.add_image(text_image(text))
    content = b"q 450 0 0 80 72 600 cm /Im1 Do Q\n"
    pdf.add_page(content, xobjects={"Im1": img})
    pdf.add_page(text_ops([(72, 700, 11, "This page has a real text layer and is not scanned.")]))
    return pdf.build()


def surrogate_cmap() -> bytes:
    """A ToUnicode CMap mapping `A` to a lone high surrogate and `B` to a proper pair (U+1F600).
    PDFium reports both as UTF-16 code units; the lone half must never reach the dataset."""
    pdf = Pdf()
    cmap = pdf.add_stream(
        b"",
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"/CMapName /Custom def\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        b"2 beginbfchar\n<41> <D83D>\n<42> <D83DDE00>\nendbfchar\n"
        b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n",
    )
    font = pdf.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode %d 0 R >>" % cmap
    )
    body = ["Body text line one at the modal size.", "Body text line two at the modal size."]
    content = b"BT\n/F2 24 Tf 1 0 0 1 72 740 Tm (Hello A B) Tj\nET\n"
    content += paragraph_ops(72, 700, 10, [body, body])
    pdf.add_page(content, fonts={"F2": font})
    return pdf.build()


def giant_page() -> bytes:
    pdf = Pdf()
    img = pdf.add_image(text_image("GIANT PAGE OCR TEST"))
    content = b"q 9000 0 0 1600 1000 12000 cm /Im1 Do Q\n"
    pdf.add_page(content, width=14400, height=14400, xobjects={"Im1": img})
    return pdf.build()


def many_pages(count: int = 20000) -> bytes:
    pdf = Pdf()
    for _ in range(count):
        pdf.add_page(b"")
    return pdf.build()


def long_text(pages: int = 6, lines_per_page: int = 60) -> bytes:
    pdf = Pdf()
    for p in range(pages):
        paras = []
        for i in range(lines_per_page // 3):
            sentence = (
                f"Page {p + 1} paragraph {i + 1} sentence one is here. "
                "Sentence two follows it! Is there a third? Yes."
            )
            paras.append([sentence] * 3)
        pdf.add_page(paragraph_ops(40, 780, 6, paras, leading=8))
    return pdf.build()


def deflate_bomb(size_mb: int = 512) -> bytes:
    pdf = Pdf()
    comp = zlib.compressobj(9)
    chunk = b" " * (1 << 20)
    data = b"".join(comp.compress(chunk) for _ in range(size_mb)) + comp.flush()
    stream = pdf.add_stream(b"/Filter /FlateDecode", data)
    page = pdf.add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
        % (pdf.pages_obj, pdf.font, stream)
    )
    pdf.pages.append(page)
    return pdf.build()


def encrypted_user() -> bytes:
    pdf = Pdf(Encryption(user_pw="secret", owner_pw="owner"))
    pdf.add_page(text_ops([(72, 700, 12, "Protected by a user password.")]))
    return pdf.build()


def owner_no_copy() -> bytes:
    pdf = Pdf(Encryption(user_pw="", owner_pw="owner", permissions=PERM_NO_COPY))
    pdf.add_page(text_ops([(72, 700, 12, "Opens without a password but copying is disallowed.")]))
    return pdf.build()


def truncated() -> bytes:
    return simple_text()[:300]


FIXTURES = {
    "simple.pdf": simple_text,
    "fonts.pdf": three_fonts,
    "rotated.pdf": rotated_text,
    "table.pdf": ruled_table,
    "two_by_one.pdf": two_by_one,
    "image_only.pdf": image_only,
    "giant.pdf": giant_page,
    "surrogate.pdf": surrogate_cmap,
    "long.pdf": long_text,
    "encrypted.pdf": encrypted_user,
    "nocopy.pdf": owner_no_copy,
    "truncated.pdf": truncated,
}
BIG_FIXTURES = {"many_pages.pdf": many_pages, "bomb.pdf": deflate_bomb}


def build_all(directory: Path, big: bool = True) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    items = dict(FIXTURES)
    if big:
        items.update(BIG_FIXTURES)
    for name, maker in items.items():
        path = directory / name
        if not path.exists():
            path.write_bytes(maker())
        paths[name] = path
    return paths


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "fixtures"
    for name, path in build_all(target).items():
        print(f"{name}: {path.stat().st_size} bytes")
