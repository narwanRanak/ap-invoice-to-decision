# Invoice → Decision: AP invoice processing (PS-1)

A working process that takes a vendor invoice PDF (born-digital or scanned) and produces a clear, reasoned **APPROVED / NEEDS REVIEW / REJECTED** decision. Every stage in between is visible live, and a dashboard tracks history, status and outputs across runs.

```
PDF ─► Intake ─► Read (text / OCR) ─► Extract ─► Validate ─► Verify vendor ─► Duplicate check ─► Match to PO ─► Decide
```

**Live:** https://ranak363-invoice-to-decision.hf.space

- **Live run view:** a progress bar plus an activity log stream each of the 8 stages as it executes, beside the invoice itself. Each stage shows its result, the findings it raised, and expandable detail (line match, PO consumption bar, checklists).
- **Evidence highlighting:** every extracted value is located on the page. Hover a field or a flag, such as `BANK_DETAILS_CHANGED`, and the exact spot on the invoice lights up. Flagged spots are boxed in red automatically.
- **Decision:** Ready to pay / On hold / Rejected, with a one-line reason, owner, next actions, the controls that passed, a ready-to-send vendor email when the vendor has to act, and reviewer Approve/Reject with a required audit note.
- **Audit packet:** a one-click JSON export of the full decision record (inputs, findings, policy applied, reviewer) for any run.
- **Invoices inbox (dashboard):** KPIs (straight-through rate, value on hold, ready to pay, time to decision), status tabs and search, reasons invoices were stopped, and a **value-delivered** panel (AP hours and cost saved, risky payments stopped, projection at 1,000 invoices/month).
- **Vendors & POs:** POs with billed / remaining updating live, vendor master, AP ledger, goods receipts, policy thresholds.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000        # open http://localhost:8000
```

That's all. The app runs fully offline by default (local OCR + a layout-aware parser). To use Claude for extraction, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. The sidebar shows which engine is active. If a Claude call ever fails, the run automatically falls back to the local engine and says so on the stage.

```bash
.venv/bin/python -m pytest -q          # 23 end-to-end + unit tests
.venv/bin/python scripts/generate_data.py          # regenerate reference data + 6 demo invoices
.venv/bin/python scripts/generate_extra_tests.py   # regenerate 4 extra "unseen" invoices in test_inputs/
```

## Demo scenarios (Process invoice → Prepared scenarios)

| # | Scenario | What's hard about it | Result |
|---|---|---|---|
| Happy A | Bluepeak: clean itemised invoice | 3-way match: PO + invoice + goods receipt | **Approved**, scheduled per Net 30 |
| Happy B | Cobalt: freight bill | Fuel surcharge not on PO (+1.1%), different labels/layout | **Approved**, variance noted as within tolerance |
| Edge 1 | Meridian: split PO | 3rd milestone invoice; each looks fine alone, cumulatively 110% of PO | **Needs review** → PO owner, vendor email asking for $3,600 revised invoice |
| Edge 2 | Riverbend: scanned, no PO # | Image-only skewed scan, so OCR, then PO inferred from 2 open POs by line items | **Approved**, PO inferred, vendor reminded to quote PO |
| Edge 3 | Northstar: "past due" re-send | Already-paid invoice, number reformatted `NS-2025-912` vs `NS-2025-0912` | **Rejected** as duplicate, vendor told it was paid |
| Edge 4 | Halden: bank change | Numbers match PO perfectly; remit-to bank differs from master; lookalike email domain | **Needs review (security)** → AP Manager + Treasury; *no* vendor reply by design |

**Suggested demo order:** Reset demo data → Happy A → Happy B → Edge 1 → 4 (the "Next ▶" button walks through them) → re-run Happy A to show it's caught as a duplicate → upload something from `test_inputs/` live → resolve the Edge 4 hold as a reviewer → Dashboard.

`test_inputs/` holds invoices the process has never been shown in the demo: unknown vendor, blocked vendor, price increase with no goods receipt, and an invoice with no number or date. Any PDF can be uploaded; a non-invoice document is routed to a person as `NOT_AN_INVOICE` rather than guessed at.

## How it's built

| Layer | Choice | Why |
|---|---|---|
| Reading | PyMuPDF text layer, RapidOCR (ONNX, local) for scans | Both produce the same row/cell layout, so downstream logic doesn't care if a PDF was scanned |
| Extraction | Claude (`claude-opus-5-5`, strict JSON schema, PDF input) **or** local layout-aware parser | AI where documents are messy; the offline engine makes the live demo un-breakable |
| Checks + decision | Plain Python, explicit policy (`data/policy.json`) | **AI reads, rules decide.** Approve/hold/reject must be auditable and reproducible |
| Data | CSVs stand in for the ERP (vendors, POs, PO lines, receipts); SQLite for AP ledger + run history | Realistic shape, zero setup |
| API / UI | FastAPI + server-sent events; vanilla JS single page, no build step | Live stage streaming with nothing to install |

Code map: `app/pipeline/reader.py` (stage 2), `extract_rules.py` / `extract_claude.py` (3), `checks.py` (4–7), `decide.py` (8), `runner.py` (orchestration + live events), `app/main.py` (API), `app/static/` (UI).

Every check emits **findings** (`code`, `severity` info/warn/hold/reject, message, evidence). The decision is a pure function of the findings: any reject → REJECTED, any hold → NEEDS REVIEW (routed by category: security → AP Manager + Treasury, PO → PO owner, data → AP specialist, high value → Finance Controller), otherwise APPROVED. The explanation is built from the same findings, so the decision and its explanation can't disagree.

See **[docs/PROCESS_MAP.md](docs/PROCESS_MAP.md)** for the full process map, rules per stage, and every assumption.

## Deploying (live link)

**Live:** https://ranak363-invoice-to-decision.hf.space (Hugging Face Space, free tier, no card).

The Space holds only the small launcher in [`deploy/hf_space/`](deploy/hf_space/): `app.py`, `requirements.txt` and `packages.txt`. On start, it downloads this GitHub repo and serves the app on port 7860, so **GitHub is the single source of truth**. To ship a change: push to `main`, then *Settings → Factory rebuild* on the Space. Free Spaces sleep when idle, so open the link a minute before a demo. Run history lives in `/tmp` and resets on restart; use **Reset demo data** before a demo either way. Optionally add `ANTHROPIC_API_KEY` as a Space secret to switch on the Claude engine.

`Dockerfile` / `render.yaml` are also included for container hosts such as Render (which needs a card on file).

## Known limitations / next steps

- Single-invoice PDFs only (no splitting of multi-invoice batches); English, USD.
- The local parser is tuned to common invoice label vocabularies; truly novel layouts are what the Claude engine is for. Low-confidence fields always route to a person rather than being guessed.
- Next: email-inbox intake, ERP connector (NetSuite / SAP) with bill write-back, reviewer corrections fed back as per-vendor extraction hints, SLA timers on held invoices.
