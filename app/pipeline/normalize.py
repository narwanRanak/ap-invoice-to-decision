"""Small, well-tested normalisation helpers shared by every stage."""
from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher

MONEY_RE = re.compile(r"(?<![\d.])\$?\s?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

LEGAL_SUFFIXES = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "incorporated", "limited", "plc", "the"}


def invoice_key(invoice_number: str | None) -> str:
    """Normalise an invoice number so trivially reformatted re-sends collide.

    'NS-2025-0912', 'ns 2025 912', 'NS2025-912' -> 'NS2025912'
    Leading zeros are dropped inside each numeric group; punctuation and case are ignored.
    """
    if not invoice_number:
        return ""
    groups = re.findall(r"[A-Za-z]+|\d+", invoice_number.upper())
    return "".join(g.lstrip("0") or "0" if g.isdigit() else g for g in groups)


def parse_money(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    m = MONEY_RE.search(str(s))
    if not m:
        return None
    return round(float(m.group(1).replace(",", "") + "." + m.group(2)), 2)


def all_money(s: str) -> list[float]:
    return [round(float(a.replace(",", "") + "." + b), 2) for a, b in MONEY_RE.findall(s)]


def parse_number(s: str) -> float | None:
    s = s.strip().replace(",", "").lstrip("$")
    try:
        return float(s)
    except ValueError:
        return None


_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def parse_date(s: str | None) -> tuple[str | None, bool]:
    """Return (ISO date, ambiguous?). Handles ISO, 'Nov 20, 2026', '2 October 2026', dd/mm and mm/dd."""
    if not s:
        return None, False
    s = s.strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))), False
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS.index(m.group(1)[:3].lower()) + 1, int(m.group(2))), False
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS.index(m.group(2)[:3].lower()) + 1, int(m.group(1))), False
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12:  # unambiguous dd/mm
            return _iso(y, b, a), False
        if b > 12:  # unambiguous mm/dd
            return _iso(y, a, b), False
        return _iso(y, a, b), True  # assume US mm/dd, flag ambiguity
    return None, False


def _iso(y, m, d) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def to_date(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def norm_name(s: str) -> str:
    words = re.findall(r"[a-z0-9]+", (s or "").lower())
    return " ".join(w for w in words if w not in LEGAL_SUFFIXES)


def name_similarity(a: str, b: str) -> float:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    contain = len(ta & tb) / min(len(ta), len(tb))
    return round(max(ratio, 0.92 * contain if contain == 1 else 0.8 * contain), 3)


def text_similarity(a: str, b: str) -> float:
    """Description similarity robust to OCR glitches: blend of char ratio and token overlap."""
    na = re.sub(r"[^a-z0-9 ]", " ", (a or "").lower())
    nb = re.sub(r"[^a-z0-9 ]", " ", (b or "").lower())
    na, nb = " ".join(na.split()), " ".join(nb.split())
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na.replace(" ", ""), nb.replace(" ", "")).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb)
    return round(0.65 * ratio + 0.35 * jacc, 3)


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[1].lower().strip(".")


def fmt_money(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"
