"""Stage 2 - read the document: native text layer if present, OCR for scanned pages.

Both paths produce the same layout representation (rows of positioned cells) so the
downstream parser does not care whether a PDF was born-digital or scanned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

import numpy as np
import pymupdf

_OCR = None  # lazy singleton - model load is ~1s


def _ocr_engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


@dataclass
class Cell:
    x0: float
    x1: float
    text: str
    conf: float = 1.0
    page: int = 0
    box: tuple = (0.0, 0.0, 0.0, 0.0)   # normalised (x0, y0, x1, y1) on the page, 0..1 - for evidence highlighting


@dataclass
class Row:
    y: float
    cells: list[Cell]

    @property
    def text(self) -> str:
        return "  ".join(c.text for c in self.cells)


@dataclass
class Document:
    method: str                    # "text-layer" | "ocr"
    pages: int
    rows: list[Row] = field(default_factory=list)
    ocr_confidence: float | None = None
    page_width: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(r.text for r in self.rows)


def _group_rows(items: list[tuple], gap_factor: float) -> list[Row]:
    """items: (x0, y0, x1, y1, text, conf, page, nbox). Group into rows by vertical centre, then merge into cells."""
    if not items:
        return []
    heights = [it[3] - it[1] for it in items]
    tol = 0.7 * median(heights)
    items = sorted(items, key=lambda it: (it[1] + it[3]) / 2)
    rows: list[list] = []
    centres: list[float] = []
    for it in items:
        cy = (it[1] + it[3]) / 2
        if rows and abs(cy - centres[-1]) <= tol:
            rows[-1].append(it)
            centres[-1] = sum((i[1] + i[3]) / 2 for i in rows[-1]) / len(rows[-1])
        else:
            rows.append([it])
            centres.append(cy)
    out = []
    for r, cy in zip(rows, centres):
        r.sort(key=lambda it: it[0])
        cells: list[Cell] = []
        h = median([i[3] - i[1] for i in r])
        for x0, y0, x1, y1, txt, conf, page, nb in r:
            if cells and x0 - cells[-1].x1 < gap_factor * h:
                c = cells[-1]
                c.text += " " + txt
                c.x1 = x1
                c.conf = min(c.conf, conf)
                b = c.box
                c.box = (min(b[0], nb[0]), min(b[1], nb[1]), max(b[2], nb[2]), max(b[3], nb[3]))
            else:
                cells.append(Cell(x0, x1, txt, conf, page, nb))
        out.append(Row(cy, cells))
    return out


def read_pdf(path: str) -> Document:
    doc = pymupdf.open(path)
    words = []
    for pno, page in enumerate(doc):
        offset = pno * 10000
        W, H = page.rect.width, page.rect.height
        for x0, y0, x1, y1, w, *_ in page.get_text("words"):
            words.append((x0, y0 + offset, x1, y1 + offset, w, 1.0, pno, (x0 / W, y0 / H, x1 / W, y1 / H)))
    text_chars = sum(len(w[4]) for w in words)
    if text_chars >= 40:
        return Document("text-layer", len(doc), _group_rows(words, gap_factor=0.9), None, doc[0].rect.width)

    # scanned / image-only: OCR every page
    engine = _ocr_engine()
    boxes, confs = [], []
    width = 0.0
    for pno, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        width = pix.width
        result, _ = engine(img)
        offset = pno * 100000
        for box, txt, conf in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            nb = (min(xs) / pix.width, min(ys) / pix.height, max(xs) / pix.width, max(ys) / pix.height)
            boxes.append((min(xs), min(ys) + offset, max(xs), max(ys) + offset, txt, float(conf), pno, nb))
            confs.append(float(conf))
    ocr_conf = round(sum(confs) / len(confs), 3) if confs else 0.0
    return Document("ocr", len(doc), _group_rows(boxes, gap_factor=1.2), ocr_conf, width)


def render_page_png(path: str, page: int = 0, dpi: int = 144) -> bytes:
    doc = pymupdf.open(path)
    return doc[page].get_pixmap(dpi=dpi).tobytes("png")
