"""Stage 3 (offline engine) - layout-aware rules extractor.

Used when no Claude API key is configured, or as an automatic fallback if the Claude call
fails. Works on the Row/Cell layout produced by the reader, so labels and values that sit in
neighbouring cells ("Invoice No:" | "INV-24817") are paired correctly.
"""
from __future__ import annotations

import re

from .normalize import EMAIL_RE, all_money, parse_date, parse_money, parse_number
from .reader import Document

BUYER_DOMAINS = {"northwind-mfg.example"}

LABELS = {
    "invoice_number": r"(?:invoice\s*(?:no\.?|number|num|#)|bill\s*no\.?|inv\s*#|invoice)",
    "invoice_date": r"(?:invoice\s*date|date\s*of\s*issue|bill\s*date|issue\s*date|date)",
    "due_date": r"(?:due\s*date|payment\s*due)",
    "po_number": r"(?:p\.?\s?o\.?\s*(?:number|no\.?|#)?|purchase\s*order(?:\s*(?:no\.?|number|#))?|customer\s*po|your\s*reference|order\s*ref(?:erence)?)",
    "payment_terms": r"(?:payment\s*terms|terms)",
}
INV_NO_VALUE = re.compile(r"^[#:.\s]*([A-Z]{0,6}[-\s]?\d[\w\-/]*)", re.I)
PO_ANY = re.compile(r"\bP\.?\s?O\.?[\s\-#:]*(\d{6,10})\b", re.I)


def _label_value(doc: Document, field: str) -> tuple[str | None, float]:
    """Find `label: value` either inside one cell or across adjacent cells. Returns (value, confidence)."""
    lab = re.compile(r"^\s*" + LABELS[field] + r"\s*[:#.]*\s*(.*)$", re.I)
    for row in doc.rows:
        for i, cell in enumerate(row.cells):
            m = lab.match(cell.text)
            if not m:
                continue
            # don't let generic labels steal more specific ones
            low = cell.text.lower()
            if field == "invoice_date" and ("due" in low):
                continue
            if field == "invoice_number" and re.match(r"^\s*invoice\s*date", low):
                continue
            if field == "payment_terms" and "payment due" in low:
                continue
            val = m.group(1).strip()
            conf = cell.conf
            if not val and i + 1 < len(row.cells):
                val = row.cells[i + 1].text.strip()
                conf = min(conf, row.cells[i + 1].conf)
            if val:
                return val, conf
    return None, 0.0


def _find_invoice_number(doc: Document):
    val, conf = _label_value(doc, "invoice_number")
    if val:
        m = INV_NO_VALUE.match(val)
        if m and not re.match(r"^\d{4}-\d{2}-\d{2}$", m.group(1)):
            return m.group(1).strip(), 0.95 * conf
    return None, 0.0


def _find_amount_row(doc: Document, pattern: str, exclude: str | None = None):
    rx = re.compile(pattern, re.I)
    ex = re.compile(exclude, re.I) if exclude else None
    hit = None
    for row in doc.rows:
        t = row.text
        if rx.search(t) and not (ex and ex.search(t)):
            vals = all_money(t)
            if vals:
                hit = (vals[-1], min(c.conf for c in row.cells), t)
    return hit


def _line_items(doc: Document):
    rows = doc.rows
    header_idx = None
    for i, r in enumerate(rows):
        t = r.text.lower()
        if re.search(r"\b(description|item|service|product)\b", t) and re.search(r"\b(amount|total|charge)\b", t):
            header_idx = i
            break
    if header_idx is None:
        return [], 0.0
    items, confs = [], []
    for r in rows[header_idx + 1:]:
        t = r.text.lower()
        if re.search(r"\b(sub\s*-?\s*total|total|amount due|balance due|tax)\b", t):
            break
        cells = [c.text for c in r.cells]
        nums = []
        # walk from the right collecting numeric cells
        for c in reversed(cells):
            n = parse_number(c)
            if n is None:
                break
            nums.insert(0, n)
        text_cells = cells[: len(cells) - len(nums)]
        if not nums or not text_cells:
            continue
        sku = None
        if len(text_cells) > 1 and re.fullmatch(r"[A-Z0-9][A-Z0-9\-/.]{2,}", text_cells[0]):
            sku = text_cells[0]
            text_cells = text_cells[1:]
        desc = " ".join(text_cells)
        amount = nums[-1]
        qty = unit = None
        if len(nums) >= 3:
            qty, unit = nums[-3], nums[-2]
        elif len(nums) == 2:
            qty, unit = 1.0, nums[0] if abs(nums[0] - amount) < 0.01 else None
            if unit is None:
                qty, unit = nums[0], round(amount / nums[0], 4) if nums[0] else None
        else:
            qty, unit = 1.0, amount
        items.append(dict(sku=sku, description=desc, qty=qty, unit_price=unit, amount=amount))
        confs.append(min(c.conf for c in r.cells))
    return items, (min(confs) if confs else 0.0)


def extract(doc: Document) -> dict:
    text = doc.text
    conf: dict[str, float] = {}
    f: dict = {}

    # vendor name: top-left cell of the first row that is not a document title
    vendor = None
    for row in doc.rows[:4]:
        cand = row.cells[0].text.strip()
        if cand and not re.fullmatch(r"(tax\s+)?invoice|freight bill|bill to:?", cand, re.I):
            vendor = cand
            conf["vendor_name"] = 0.85 * row.cells[0].conf
            break
    f["vendor_name"] = vendor

    m = re.search(r"\b(?:Tax\s*ID|EIN|TIN|FEIN)\s*[:#]?\s*(\d{2}-\d{7})", text, re.I)
    f["vendor_tax_id"] = m.group(1) if m else None
    conf["vendor_tax_id"] = 0.95 if m else 0.0

    emails = [e for e in EMAIL_RE.findall(text) if e.split("@")[1].lower() not in BUYER_DOMAINS]
    f["vendor_email"] = emails[0].lower() if emails else None

    f["invoice_number"], conf["invoice_number"] = _find_invoice_number(doc)

    raw_date, c = _label_value(doc, "invoice_date")
    iso, ambiguous = parse_date(raw_date)
    f["invoice_date"] = iso
    conf["invoice_date"] = (0.6 if ambiguous else 0.95) * c if iso else 0.0
    f["invoice_date_ambiguous"] = ambiguous

    raw_due, _ = _label_value(doc, "due_date")
    f["due_date"], _ = parse_date(raw_due)

    po_val, c = _label_value(doc, "po_number")
    m = PO_ANY.search(po_val or "") or PO_ANY.search(text)
    if m:
        f["po_number"] = "PO-" + m.group(1)
        conf["po_number"] = 0.95 * (c or 1.0)
    else:
        bare = re.search(r"\b(45\d{5})\b", po_val or "")
        f["po_number"] = "PO-" + bare.group(1) if bare else None
        conf["po_number"] = 0.75 if bare else 0.0

    terms, _ = _label_value(doc, "payment_terms")
    tm = re.search(r"(net\s*\d+|due\s+on\s+receipt)", terms or text, re.I)
    wm = re.search(r"within\s+(\d+)\s+days", text, re.I)
    f["payment_terms"] = tm.group(1).title() if tm else (f"Net {wm.group(1)}" if wm else None)

    items, lc = _line_items(doc)
    f["line_items"] = items
    conf["line_items"] = lc

    sub = _find_amount_row(doc, r"\b(sub\s*-?\s*total|net\s+amount)\b")
    tax = _find_amount_row(doc, r"\b(sales[\s-]*tax|tax|vat|gst)", exclude=r"tax\s*id|sub\s*-?\s*total|total\s*(due|usd)|incl")
    tot = _find_amount_row(doc, r"\b(total|amount\s+due|balance\s+due|grand\s+total)\b", exclude=r"sub\s*-?\s*total|line\s+total")
    f["subtotal"] = sub[0] if sub else None
    f["tax_amount"] = tax[0] if tax else (0.0 if sub and tot and abs(sub[0] - tot[0]) < 0.005 else None)
    f["total"] = tot[0] if tot else None
    conf["subtotal"] = sub[1] if sub else 0.0
    conf["total"] = 0.95 * tot[1] if tot else 0.0
    rate = None
    if tax:
        rm = re.search(r"(\d{1,2}(?:\.\d{1,3})?)\s*%", tax[2])
        rate = float(rm.group(1)) / 100 if rm else None
    f["tax_rate"] = rate
    f["tax_inclusive"] = bool(re.search(r"incl(?:\.|uding|udes)?\s+(?:of\s+)?(?:sales\s+)?tax", text, re.I))
    cur = re.search(r"\b(USD|EUR|GBP|INR|CAD)\b", text)
    f["currency"] = cur.group(1) if cur else "USD"  # assumption: unlabelled amounts are USD

    # remit-to banking
    acct = re.search(r"\b(?:Account|Acct)\.?\s*(?:No\.?|#|Number)?\s*[:#]?\s*(\d{6,17})", text, re.I)
    rout = re.search(r"\b(?:Routing|ABA)\s*(?:No\.?|#)?\s*[:#.]?\s*(\d{9})\b", text, re.I)
    bank = re.search(r"([A-Z][A-Za-z&' ]*?(?:Bank|Trust|Savings)(?:\s*&\s*Trust)?)\b", text)
    bank_name = None
    for row in doc.rows:
        if re.search(r"remit|ach|wire|bank:|payment within", row.text, re.I):
            bm = re.search(r"(?:Bank:\s*|Remit(?: to)?:\s*|ACH:\s*|Wire:\s*|to\s+)([A-Z][A-Za-z&' ]+?(?:Bank|Trust))", row.text)
            if bm:
                bank_name = bm.group(1).strip()
    f["remit_bank_name"] = bank_name or (bank.group(1).strip() if bank else None)
    f["remit_account"] = acct.group(1) if acct else None
    f["remit_routing"] = rout.group(1) if rout else None

    notices = []
    for row in doc.rows:
        t = row.text
        if re.search(r"bank(ing)?\s+details\s+have\s+changed|new\s+account|update\s+your\s+records|changed\s+bank", t, re.I):
            notices.append(t.strip())
        elif re.search(r"reminder|past\s+due|remains\s+unpaid|overdue|second\s+notice|copy", t, re.I):
            notices.append(t.strip())
    f["notices"] = notices

    if doc.method == "ocr" and doc.ocr_confidence is not None:
        conf = {k: round(v * min(1.0, doc.ocr_confidence + 0.03), 3) for k, v in conf.items()}
    f["confidence"] = {k: round(v, 3) for k, v in conf.items()}
    f["engine"] = "rules"
    return f


def parse_amount(s):
    return parse_money(s)
