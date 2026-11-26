"""Stage 3 (AI engine) - Claude reads the invoice PDF (text or scanned) and returns a strict JSON record.

Claude is used ONLY to read the document. It never decides approve/hold/reject: that is
deterministic code in checks.py / decide.py so every decision is auditable and reproducible.
"""
from __future__ import annotations

import base64
import json

import anthropic

from ..config import CLAUDE_MODEL

_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["vendor_name", "vendor_tax_id", "vendor_email", "invoice_number", "invoice_date", "invoice_date_ambiguous",
                 "due_date", "po_number", "payment_terms", "currency", "line_items", "subtotal", "tax_amount", "tax_rate",
                 "tax_inclusive", "total", "remit_bank_name", "remit_account", "remit_routing", "notices", "confidence"],
    "properties": {
        "vendor_name": _STR,
        "vendor_tax_id": _STR,
        "vendor_email": _STR,
        "invoice_number": _STR,
        "invoice_date": {**_STR, "description": "ISO yyyy-mm-dd"},
        "invoice_date_ambiguous": {"type": "boolean", "description": "true if day/month order cannot be determined"},
        "due_date": {**_STR, "description": "ISO yyyy-mm-dd"},
        "po_number": {**_STR, "description": "Purchase order reference exactly as printed, or null if absent. Never guess."},
        "payment_terms": _STR,
        "currency": _STR,
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sku", "description", "qty", "unit_price", "amount"],
                "properties": {"sku": _STR, "description": {"type": "string"}, "qty": _NUM, "unit_price": _NUM, "amount": _NUM},
            },
        },
        "subtotal": _NUM,
        "tax_amount": _NUM,
        "tax_rate": {**_NUM, "description": "as a fraction, e.g. 0.0825"},
        "tax_inclusive": {"type": "boolean"},
        "total": _NUM,
        "remit_bank_name": _STR,
        "remit_account": _STR,
        "remit_routing": _STR,
        "notices": {"type": "array", "items": {"type": "string"},
                    "description": "Verbatim notices that matter to AP: bank detail changes, reminders/past-due, credit notes, disputes"},
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["vendor_name", "invoice_number", "invoice_date", "po_number", "line_items", "subtotal", "total"],
            "properties": {k: {"type": "number"} for k in
                           ["vendor_name", "invoice_number", "invoice_date", "po_number", "line_items", "subtotal", "total"]},
        },
    },
}

SYSTEM = """You extract data from vendor invoices for an accounts-payable team.
Read the attached invoice exactly as printed. Rules:
- Copy identifiers (invoice number, PO number, account numbers) character-for-character. Do not normalise or correct them.
- If a field is not present on the document, return null. Never infer a PO number from context.
- vendor_* fields describe the SELLER issuing the invoice, never the bill-to customer (Northwind Manufacturing).
- Amounts are plain numbers without currency symbols or thousands separators.
- confidence is your 0-1 certainty that each value is read correctly (lower it for blurry scans or ambiguous layouts)."""


def extract(pdf_path: str, ocr_text: str | None) -> dict:
    client = anthropic.Anthropic()
    pdf_b64 = base64.standard_b64encode(open(pdf_path, "rb").read()).decode()
    content = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
        {"type": "text", "text": "Extract the invoice." + (
            f"\n\nLocal OCR text of the page, for reference (may contain OCR errors):\n{ocr_text}" if ocr_text else "")},
    ]
    response = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this document")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude response was truncated")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    data["engine"] = f"claude:{response.model}"
    data["usage"] = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return data
