"""Locate each extracted value on the page so the UI can highlight its source ("show me where").

Works for both engines: it searches the reader's positioned text for the value as printed,
so a Claude-extracted field is checked against the document too. A field we cannot locate
simply has no highlight - which is itself a useful signal to a reviewer.
"""
from __future__ import annotations

import re
from datetime import datetime

from .reader import Cell, Document

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _norm(s: str) -> str:
    return re.sub(r"[\s$]", "", (s or "")).lower()


def _money_forms(x: float) -> list[str]:
    return [f"{x:,.2f}", f"{x:.2f}"]


def _date_forms(iso: str) -> list[str]:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return []
    m, day, y = d.month, d.day, d.year
    return [iso, f"{day:02d}/{m:02d}/{y}", f"{m:02d}/{day:02d}/{y}", f"{day}/{m}/{y}", f"{m}/{day}/{y}",
            f"{_MON[m - 1]} {day}, {y}", f"{_MONTH[m - 1]} {day}, {y}", f"{day} {_MONTH[m - 1]} {y}"]


def _box(cells: list[Cell]) -> dict:
    return {"page": cells[0].page,
            "box": [round(min(c.box[0] for c in cells), 4), round(min(c.box[1] for c in cells), 4),
                    round(max(c.box[2] for c in cells), 4), round(max(c.box[3] for c in cells), 4)]}


def _find(doc: Document, forms: list[str], prefer_last: bool = False, row_filter=None) -> dict | None:
    forms = [_norm(f) for f in forms if f]
    hits = []
    for row in doc.rows:
        if row_filter and not row_filter(row.text):
            continue
        for c in row.cells:
            t = _norm(c.text)
            if any(f and f in t for f in forms):
                hits.append(c)
    if not hits:
        return None
    return _box([hits[-1] if prefer_last else hits[0]])


def locate(doc: Document, fx: dict) -> dict:
    ev: dict[str, dict] = {}

    def put(key, hit):
        if hit:
            ev[key] = hit

    if fx.get("vendor_name"):
        put("vendor_name", _find(doc, [fx["vendor_name"]]))
    for k in ("invoice_number", "po_number", "vendor_tax_id", "vendor_email", "remit_account", "remit_routing",
              "remit_bank_name", "payment_terms"):
        if fx.get(k):
            put(k, _find(doc, [str(fx[k])]))
    if fx.get("invoice_date"):
        put("invoice_date", _find(doc, _date_forms(fx["invoice_date"])))
    if fx.get("due_date"):
        put("due_date", _find(doc, _date_forms(fx["due_date"])))

    total_row = lambda t: re.search(r"total|amount due|balance due", t, re.I) and not re.search(r"sub\s*-?\s*total", t, re.I)
    if fx.get("total") is not None:
        put("total", _find(doc, _money_forms(fx["total"]), prefer_last=True, row_filter=total_row)
            or _find(doc, _money_forms(fx["total"]), prefer_last=True))
    if fx.get("subtotal") is not None:
        put("subtotal", _find(doc, _money_forms(fx["subtotal"]), row_filter=lambda t: re.search(r"sub\s*-?\s*total", t, re.I)))
    if fx.get("tax_amount"):
        put("tax_amount", _find(doc, _money_forms(fx["tax_amount"]), row_filter=lambda t: re.search(r"tax|vat|gst", t, re.I)))

    # line items: highlight the whole printed row that carries the line amount
    for i, li in enumerate(fx.get("line_items") or []):
        if li.get("amount") is None:
            continue
        forms = [_norm(f) for f in _money_forms(li["amount"])]
        desc = _norm((li.get("description") or "")[:12])
        for row in doc.rows:
            t = _norm(row.text)
            if any(f in t for f in forms) and (not desc or desc[:8] in t):
                ev[f"line_{i}"] = _box(row.cells)
                break

    # notices (e.g. "our banking details have changed")
    for i, n in enumerate(fx.get("notices") or []):
        key = _norm(n)[:30]
        for row in doc.rows:
            if key and key in _norm(row.text):
                ev[f"notice_{i}"] = _box(row.cells)
                break
    return ev
