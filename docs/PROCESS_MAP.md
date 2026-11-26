# Process map: invoice PDF to AP decision

PS-1 (Finance / AP). I wrote this before building anything, and the code follows it one stage per module.

```
 Invoice PDF ──► 1 Intake ──► 2 Read document ──► 3 Extract fields ──► 4 Validate invoice
                                                                              │
     Decision ◄── 8 Decide ◄── 7 Match to PO ◄── 6 Duplicate check ◄── 5 Verify vendor
```

| # | Stage | Input | What it does | Can produce |
|---|-------|-------|--------------|-------------|
| 1 | **Intake** | PDF bytes | SHA-256 fingerprint, page count, size, and a check for the *exact same file* already seen | `FILE_RESUBMITTED` (warn) |
| 2 | **Read document** | PDF | Uses the text layer when there is one. Scanned/image-only pages go to OCR (Claude vision, or local RapidOCR when there's no API key). Reports the method used and OCR confidence | `SCANNED_DOCUMENT` (info), `LOW_OCR_CONFIDENCE` (hold), `UNREADABLE` (hold) |
| 3 | **Extract fields** | text / page images | Structured extraction: vendor, invoice #, dates, PO ref, line items, subtotal, tax, total, remit-to bank details, notes, each with a confidence score. Claude with a strict JSON schema, or a layout-aware rules parser as the offline fallback | `MISSING_FIELD` (hold), `LOW_CONFIDENCE_FIELD` (hold) |
| 4 | **Validate invoice** | fields | Arithmetic (lines → subtotal, subtotal + tax → total, tax-inclusive handling), date sanity (future / stale), currency | `MATH_MISMATCH` (hold), `FUTURE_DATED` / `STALE_INVOICE` (hold) |
| 5 | **Verify vendor** | fields + vendor master | Fuzzy name / alias / tax-ID match. Checks vendor status. **Compares remit-to bank details and contact domain against the vendor master** | `UNKNOWN_VENDOR` (reject), `VENDOR_BLOCKED` (reject), `BANK_DETAILS_CHANGED` (security hold), `CONTACT_DOMAIN_MISMATCH` (warn) |
| 6 | **Duplicate check** | fields + AP ledger | Exact match on a *normalised* key (vendor + invoice # with punctuation and leading zeros stripped). Fuzzy match on same vendor + same amount + invoice date within 45 days + same PO | `DUPLICATE_INVOICE` (reject), `POSSIBLE_DUPLICATE` (hold) |
| 7 | **Match to PO** | fields + POs, PO lines, receipts, ledger | Uses the explicit PO ref, or **infers** the PO from the vendor's open POs by line-level similarity. Line matching (description, qty, unit price), header tolerance, **cumulative billing across split invoices**, and a 3-way match against goods receipts for goods POs | `PO_INFERRED` (info), `PO_NOT_FOUND` (hold), `PO_VENDOR_MISMATCH` (hold), `PRICE_VARIANCE_WITHIN_TOLERANCE` (info), `PRICE_VARIANCE` (hold), `PO_OVERBILLED` (hold), `QTY_NOT_RECEIVED` (hold), `PO_CLOSED` (hold) |
| 8 | **Decide** | all findings + policy | Deterministic policy: any *reject* finding → **REJECTED**; any *hold* → **NEEDS REVIEW** (routed to the right owner); otherwise **APPROVED** (scheduled per payment terms, subject to the auto-approval limit). Writes a plain-English explanation, next actions, and a vendor-facing message when the vendor has to do something | Decision record |

## Design principles (what I'd defend in the interview)

1. **AI reads, rules decide.** The LLM handles the messy, unstructured part: reading the invoice. The approve/hold/reject decision is deterministic code against an explicit policy, so every outcome can be audited and reproduced. Nothing that moves money depends on a model's mood.
2. **Every decision explains itself.** Each check emits *findings* (code, severity, message, evidence). The decision is a pure function of the findings, so the explanation and the decision can't drift apart.
3. **Fail safe, not silent.** Low confidence, missing data or anything ambiguous becomes **NEEDS REVIEW** with a specific reason, never a guess. Rejection is reserved for cases where paying would clearly be wrong (duplicate, unknown or blocked vendor).
4. **Runs with or without an API key.** With `ANTHROPIC_API_KEY` set, extraction and OCR use Claude. Without it, a local OCR + layout parser runs the same pipeline, so the live demo can't be broken by a network or quota issue. The UI always shows which engine was used.

## Scenarios prepared for the demo

| Scenario | Vendor | What makes it interesting | Expected |
|---|---|---|---|
| Happy path A | Bluepeak Industrial Supply | Clean text PDF, itemised, tax separate, explicit PO, goods fully received (3-way match) | **APPROVED** |
| Happy path B | Cobalt Logistics | Bill with a fuel surcharge that isn't on the PO: +1.1% variance, inside tolerance | **APPROVED** (variance noted) |
| Edge 1: split PO overbilling | Meridian Office Solutions | Third milestone invoice on one PO. Each invoice looks fine alone, but cumulative billing reaches 110% of the PO | **NEEDS REVIEW**: $1,800 over PO; vendor asked for a revised invoice of $3,600 |
| Edge 2: scanned, no PO reference | Riverbend Packaging | Image-only, slightly skewed scan with no PO number. OCR, then the PO is inferred from 2 open POs by line items | **APPROVED**, PO inferred, vendor reminded to quote the PO |
| Edge 3: disguised duplicate | Northstar Software | "Past due reminder" re-send of an invoice already paid, with the number reformatted (`NS-2025-912` vs `NS-2025-0912`) | **REJECTED**: duplicate of the paid invoice |
| Edge 4: bank detail change | Halden Electrical | Numbers match the PO perfectly, but remit-to bank account differs from the vendor master and the email domain is a lookalike (classic BEC fraud) | **NEEDS REVIEW (security hold)**: verify by phone on file before paying |

## Assumptions (noted as the FAQ asks)

- Buyer is a fictional US mid-size manufacturer ("Northwind Manufacturing"); currency is USD. All vendors, POs and invoices are synthetic.
- The PO dataset, PO lines, goods receipts and vendor master are CSVs standing in for the procurement system / ERP; the AP ledger is a SQLite table.
- Tolerance: header variance is auto-accepted if it is **≤ 2% *and* ≤ $250**; per-line unit price ±2%. Tax rate must match the PO's expected rate within 0.25 percentage points.
- Goods POs use a **3-way match** (PO, invoice, goods receipt). Service POs use a **2-way match** (service acceptance is assumed at PO-owner level).
- Invoices over **$25,000** are never auto-approved, even when they match perfectly; they're routed to the Finance Controller.
- An inferred PO is auto-accepted only if there's **exactly one** candidate scoring ≥ 0.85 on line-level similarity; otherwise a human picks.
- A changed bank account is **never** accepted from the invoice itself. It needs out-of-band verification using the contact details on file.
- "Approved" means approved for payment and scheduled per vendor terms. Actual payment execution is out of scope.
