"""End-to-end tests: every demo scenario and every extra input must produce the designed decision.

Run:  .venv/bin/python -m pytest -q
Uses the local engine (no API key needed) and a throwaway database.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["AP_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="ap-test-")
os.environ["AP_STAGE_MIN_MS"] = "0"
os.environ["AP_EXTRACTOR"] = "rules"

import pytest  # noqa: E402

from app import db  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.pipeline.normalize import invoice_key, parse_date  # noqa: E402


def run(path: Path) -> dict:
    rid = db.next_run_id()
    runner.start_run(rid, str(path), path.name, "test")
    for _ in range(600):
        if runner.stream(rid)["done"]:
            break
        time.sleep(0.05)
    return db.get_run(rid)


def blocking_codes(r: dict) -> set[str]:
    return {f["code"] for f in r["result"]["findings"] if f["severity"] in ("hold", "reject")}


# ------------------------------------------------------------------ unit tests
@pytest.mark.parametrize("a,b", [("NS-2025-0912", "NS-2025-912"), ("INV 00417", "inv-417"), ("RP/55102", "RP55102")])
def test_invoice_key_collides_on_reformatting(a, b):
    assert invoice_key(a) == invoice_key(b)


def test_invoice_key_distinguishes_real_differences():
    assert invoice_key("MOS-1188") != invoice_key("MOS-1142")


@pytest.mark.parametrize("raw,iso,amb", [
    ("2026-11-18", "2026-11-18", False), ("Nov 20, 2026", "2026-11-20", False), ("October 2, 2026", "2026-10-02", False),
    ("22/11/2026", "2026-11-22", False), ("03/04/2026", "2026-03-04", True),
])
def test_parse_date(raw, iso, amb):
    assert parse_date(raw) == (iso, amb)


# ------------------------------------------------------------- scenario tests
@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    db.init(reset=True)


SCENARIOS = json.loads((ROOT / "samples" / "manifest.json").read_text())


@pytest.mark.parametrize("s", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_demo_scenarios(s):
    r = run(ROOT / "samples" / s["file"])
    assert r["decision"] == s["expected"], r["headline"]
    codes = blocking_codes(r)
    expected_codes = {
        "happy-bluepeak": set(), "happy-cobalt": set(), "edge-scanned-nopo": set(),
        "edge-split-po": {"PO_OVERBILLED"},
        "edge-duplicate": {"DUPLICATE_INVOICE"},
        "edge-bank-change": {"BANK_DETAILS_CHANGED", "CONTACT_DOMAIN_MISMATCH"},
    }[s["id"]]
    assert codes == expected_codes
    if s["id"] == "edge-scanned-nopo":
        assert r["result"]["po"]["how"] == "inferred" and r["result"]["po"]["po_number"] == "PO-4500140"
    if s["id"] == "happy-cobalt":
        assert any(f["code"] == "PRICE_VARIANCE_WITHIN_TOLERANCE" for f in r["result"]["findings"])


def test_resubmitting_an_approved_invoice_is_rejected():
    r = run(ROOT / "samples" / "bluepeak_INV-24817.pdf")  # already approved in test_demo_scenarios
    assert r["decision"] == "REJECTED"
    assert "DUPLICATE_INVOICE" in blocking_codes(r)


@pytest.mark.parametrize("name,decision,must_have", [
    ("pinecrest_unknown_vendor.pdf", "REJECTED", {"UNKNOWN_VENDOR"}),
    ("summit_blocked_vendor.pdf", "REJECTED", {"VENDOR_BLOCKED"}),
    ("riverbend_price_increase.pdf", "NEEDS_REVIEW", {"PRICE_VARIANCE", "QTY_NOT_RECEIVED"}),
    ("bluepeak_missing_number.pdf", "NEEDS_REVIEW", {"MISSING_FIELD"}),
])
def test_unseen_inputs(name, decision, must_have):
    r = run(ROOT / "test_inputs" / name)
    assert r["decision"] == decision, r["headline"]
    assert must_have <= blocking_codes(r)


def test_garbage_pdf_goes_to_a_person_not_crash(tmp_path):
    import pymupdf
    p = tmp_path / "letter.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Dear customer, thank you for your continued partnership. Kind regards.")
    doc.save(p)
    r = run(p)
    assert r["status"] == "completed"
    assert r["decision"] == "NEEDS_REVIEW"
    assert "NOT_AN_INVOICE" in blocking_codes(r)
