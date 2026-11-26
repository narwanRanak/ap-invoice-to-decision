"""Read-only procurement reference data (stands in for the ERP / procurement system)."""
from __future__ import annotations

import csv
from functools import lru_cache

from .config import DATA_DIR


def _read(name: str) -> list[dict]:
    with (DATA_DIR / name).open() as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def vendors() -> list[dict]:
    rows = _read("vendors.csv")
    for r in rows:
        r["aliases"] = [a for a in r["aliases"].split("|") if a]
    return rows


@lru_cache(maxsize=1)
def purchase_orders() -> dict[str, dict]:
    out = {}
    for r in _read("purchase_orders.csv"):
        r["amount"] = float(r["amount"])
        r["tax_rate"] = float(r["tax_rate"])
        r["lines"] = []
        out[r["po_number"]] = r
    for l in _read("po_lines.csv"):
        l["line"] = int(l["line"])
        l["qty"] = float(l["qty"])
        l["unit_price"] = float(l["unit_price"])
        l["amount"] = round(l["qty"] * l["unit_price"], 2)
        out[l["po_number"]]["lines"].append(l)
    return out


@lru_cache(maxsize=1)
def receipts() -> list[dict]:
    rows = _read("receipts.csv")
    for r in rows:
        r["line"] = int(r["line"])
        r["qty_received"] = float(r["qty_received"])
    return rows


def received_qty(po_number: str, line: int) -> float:
    return sum(r["qty_received"] for r in receipts() if r["po_number"] == po_number and r["line"] == line)


def vendor_by_id(vid: str) -> dict | None:
    return next((v for v in vendors() if v["vendor_id"] == vid), None)


def ledger_seed() -> list[dict]:
    rows = _read("ledger_seed.csv")
    for r in rows:
        for k in ("subtotal", "tax", "total"):
            r[k] = float(r[k])
    return rows
