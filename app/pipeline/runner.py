"""Runs the 8 stages for one invoice and streams every stage start/finish as an event."""
from __future__ import annotations

import hashlib
import threading
import time
import traceback
from datetime import date
from pathlib import Path

import pymupdf

from .. import config, db
from . import checks, decide as decide_mod, evidence, extract_rules
from .normalize import fmt_money, invoice_key
from .reader import read_pdf

STAGES = [
    ("intake", "Intake", "Fingerprint the file and check if it was seen before"),
    ("read", "Read document", "Text layer, or OCR for scanned pages"),
    ("extract", "Extract fields", "Vendor, numbers, dates, lines, totals, bank details"),
    ("validate", "Validate invoice", "Required fields, arithmetic, dates, currency"),
    ("vendor", "Verify vendor", "Vendor master, status, tax ID, bank details"),
    ("duplicate", "Duplicate check", "Exact (normalised) and fuzzy matches in the AP ledger"),
    ("po_match", "Match to PO", "PO lookup or inference, line match, tolerance, split billing, receipts"),
    ("decide", "Decide", "Apply AP policy and explain the outcome"),
]

# in-memory event streams for live runs: run_id -> {"events": [...], "done": bool}
_streams: dict[str, dict] = {}
_streams_lock = threading.Lock()


def stream(run_id: str) -> dict | None:
    return _streams.get(run_id)


def _emit(run_id: str, event: dict) -> None:
    event["t"] = time.time()
    with _streams_lock:
        _streams[run_id]["events"].append(event)


def _status_from(findings: list[checks.Finding]) -> str:
    sev = {f.severity for f in findings}
    if "reject" in sev:
        return "reject"
    if "hold" in sev:
        return "hold"
    if "warn" in sev:
        return "warn"
    return "pass"


def start_run(run_id: str, pdf_path: str, filename: str, source: str) -> None:
    db.create_run(run_id, source, filename, pdf_path)
    with _streams_lock:
        _streams[run_id] = {"events": [], "done": False}
    threading.Thread(target=_run, args=(run_id, pdf_path, filename), daemon=True).start()


def _run(run_id: str, pdf_path: str, filename: str) -> None:
    policy = config.load_policy()
    t_start = time.time()
    stages_out: dict[str, dict] = {}
    all_findings: list[dict] = []
    ctx: dict = {}

    def stage(key: str, fn):
        label = next(s[1] for s in STAGES if s[0] == key)
        _emit(run_id, {"type": "stage_start", "stage": key, "label": label})
        t0 = time.time()
        summary, details, findings = fn()
        elapsed = int((time.time() - t0) * 1000)
        pad = config.STAGE_MIN_MS / 1000 - (time.time() - t0)
        if pad > 0:
            time.sleep(pad)  # visual pacing only, so a human can follow the live view
        fds = [f.dict() if isinstance(f, checks.Finding) else f for f in findings]
        all_findings.extend(fds)
        status = _status_from([checks.Finding(**f) for f in fds]) if fds else "pass"
        if details.get("_status"):
            status = details.pop("_status")
        rec = {"stage": key, "label": label, "status": status, "summary": summary, "details": details,
               "findings": fds, "duration_ms": elapsed}
        stages_out[key] = rec
        _emit(run_id, {"type": "stage_end", **rec})
        return details

    try:
        # 1 ----------------------------------------------------------- intake
        def s_intake():
            raw = Path(pdf_path).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            try:
                pages = len(pymupdf.open(pdf_path))
            except Exception as e:  # not a PDF / corrupt
                raise ValueError(f"File is not a readable PDF: {e}")
            db.update_run(run_id, file_hash=sha)
            seen = db.file_hash_seen(sha, run_id)
            f = []
            if seen:
                f.append(checks.Finding("FILE_RESUBMITTED", "warn", "intake",
                                        f"This exact file was already processed in {seen['id']} (decision: {seen['decision']}).",
                                        "duplicate", {"previous_run": seen["id"]}))
            d = {"filename": filename, "size_kb": round(len(raw) / 1024, 1), "pages": pages, "sha256": sha[:16],
                 "previously_seen": seen["id"] if seen else None}
            return f"{pages} page PDF, {d['size_kb']} KB · fingerprint {sha[:10]}…", d, f
        stage("intake", s_intake)

        # 2 ------------------------------------------------------------- read
        def s_read():
            doc = read_pdf(pdf_path)
            ctx["doc"] = doc
            f = []
            if doc.method == "ocr":
                f.append(checks.Finding("SCANNED_DOCUMENT", "info", "read",
                                        "No text layer - image-only scan. Text recovered with OCR.", "data",
                                        {"ocr_confidence": doc.ocr_confidence}))
                if (doc.ocr_confidence or 0) < policy["min_ocr_confidence"]:
                    f.append(checks.Finding("LOW_OCR_CONFIDENCE", "hold", "read",
                                            f"OCR confidence {doc.ocr_confidence:.0%} is below {policy['min_ocr_confidence']:.0%}.", "data", {}))
            if len(doc.text.strip()) < 20:
                f.append(checks.Finding("UNREADABLE", "hold", "read", "Could not read any text from the document.", "data", {}))
            summary = (f"Scanned image - OCR recovered {len(doc.rows)} lines at {doc.ocr_confidence:.1%} confidence"
                       if doc.method == "ocr" else f"Born-digital PDF - read {len(doc.rows)} lines from the text layer")
            d = {"method": doc.method, "lines": len(doc.rows), "ocr_confidence": doc.ocr_confidence, "text": doc.text,
                 "pages": doc.pages}
            return summary, d, f
        stage("read", s_read)

        # 3 ---------------------------------------------------------- extract
        def s_extract():
            fx, engine_note = None, None
            if config.claude_enabled():
                try:
                    from . import extract_claude
                    fx = extract_claude.extract(pdf_path, ctx["doc"].text if ctx["doc"].method == "ocr" else None)
                except Exception as e:  # never let an API hiccup break the run
                    engine_note = f"Claude unavailable ({type(e).__name__}); fell back to the local rules parser."
            if fx is None:
                fx = extract_rules.extract(ctx["doc"])
            fx.setdefault("notices", [])
            fx.setdefault("confidence", {})
            try:
                fx["evidence"] = evidence.locate(ctx["doc"], fx)
            except Exception:  # highlighting is a nice-to-have; never fail a run over it
                fx["evidence"] = {}
            ctx["fx"] = fx
            db.update_run(run_id, vendor_name=fx.get("vendor_name"), invoice_number=fx.get("invoice_number"),
                          po_number=fx.get("po_number"), total=fx.get("total"), engine=fx.get("engine"))
            f = []
            if engine_note:
                f.append(checks.Finding("EXTRACTOR_FALLBACK", "warn", "extract", engine_note, "data", {}))
            n_found = sum(1 for k in ("vendor_name", "invoice_number", "invoice_date", "po_number", "subtotal", "total")
                          if fx.get(k) not in (None, ""))
            eng = "Claude" if str(fx.get("engine", "")).startswith("claude") else "Local rules parser"
            summary = (f"{eng}: {fx.get('vendor_name') or '?'} · #{fx.get('invoice_number') or '?'} · "
                       f"{fmt_money(fx.get('total'))} · {len(fx.get('line_items') or [])} line(s) · {n_found}/6 key fields")
            return summary, {"fields": fx, "engine_note": engine_note}, f
        stage("extract", s_extract)
        fx = ctx["fx"]
        today = date.today()

        # 4 --------------------------------------------------------- validate
        def s_validate():
            summary, f = checks.validate(fx, policy, today)
            ctx["val"] = summary
            fails = [c for c in summary["checks"] if c[1] == "fail"]
            txt = (f"{len(summary['checks']) - len(fails)}/{len(summary['checks'])} checks passed"
                   + (f" · net {fmt_money(summary['net_amount'])} + tax {fmt_money(summary['tax'])}" if summary.get("net_amount") is not None else ""))
            return txt, summary, f
        stage("validate", s_validate)

        not_invoice = any(f["code"] == "NOT_AN_INVOICE" for f in all_findings)

        # 5 ----------------------------------------------------------- vendor
        def s_vendor():
            if not_invoice:
                ctx["vendor"] = {"matched": False, "skipped": "document not recognised as an invoice"}
                return "Skipped - document not recognised as an invoice", {**ctx["vendor"], "_status": "skip"}, []
            summary, f = checks.verify_vendor(fx)
            ctx["vendor"] = summary
            txt = (f"{summary['vendor_id']} {summary['vendor_name']} (match {summary['score']:.0%})" if summary.get("matched")
                   else f"Not in vendor master (closest: {summary.get('best_guess')})")
            return txt, summary, f
        stage("vendor", s_vendor)

        # 6 -------------------------------------------------------- duplicate
        def s_dup():
            summary, f = checks.check_duplicates(fx, ctx["vendor"], policy, run_id)
            ctx["dup"] = summary
            if summary.get("skipped"):
                return f"Skipped - {summary['skipped']}", {**summary, "_status": "skip"}, f
            hit = next((x for x in f if x.code in ("DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE")), None)
            txt = hit.message if hit else f"No duplicates across {summary['compared_against']} prior invoices from this vendor"
            return txt, summary, f
        stage("duplicate", s_dup)

        # 7 --------------------------------------------------------- PO match
        def s_po():
            dup_hit = next((f for f in all_findings if f["code"] == "DUPLICATE_INVOICE"), None)
            if dup_hit:
                ctx["po"] = {"skipped": f"exact duplicate of {dup_hit['evidence']['original']}; not matched again, so the PO isn't double-counted"}
                return f"Skipped - {ctx['po']['skipped']}", {**ctx["po"], "_status": "skip"}, []
            summary, f = checks.match_po(fx, ctx["vendor"], ctx["val"], policy, run_id)
            ctx["po"] = summary
            if summary.get("skipped"):
                return f"Skipped - {summary['skipped']}", {**summary, "_status": "skip"}, f
            if not summary.get("po_number"):
                return "No PO could be matched", summary, f
            h = summary["header"]
            txt = (f"{summary['po_number']} ({summary['how']}, {summary['match_type']}) · "
                   f"{h['cumulative_pct']:.0f}% of PO billed incl. this invoice")
            return txt, summary, f
        stage("po_match", s_po)

        # 8 ----------------------------------------------------------- decide
        def s_decide():
            dec = decide_mod.decide(fx, ctx["val"], ctx["vendor"], ctx["dup"], ctx["po"], all_findings, policy)
            ctx["decision"] = dec
            status = {"APPROVED": "pass", "NEEDS_REVIEW": "hold", "REJECTED": "reject"}[dec["decision"]]
            return f"{dec['decision'].replace('_', ' ')} → {dec['routed_to']}", {**dec, "_status": status}, []
        stage("decide", s_decide)

        dec = ctx["decision"]
        _record_ledger(run_id, fx, ctx, dec["decision"])
        result = {"stages": [stages_out[k] for k, *_ in STAGES], "fields": fx, "findings": all_findings, "decision": dec,
                  "validation": ctx["val"], "vendor": ctx["vendor"], "po": ctx["po"]}
        db.update_run(run_id, status="completed", decision=dec["decision"], final_status=dec["decision"],
                      headline=dec["headline"], duration_ms=int((time.time() - t_start) * 1000),
                      total=fx.get("total"), po_number=ctx["po"].get("po_number") or fx.get("po_number"),
                      vendor_name=ctx["vendor"].get("vendor_name") or fx.get("vendor_name"), result_json=result)
        _emit(run_id, {"type": "done", "decision": dec["decision"]})
    except Exception as e:  # graceful failure: the document goes to a human, never silently dropped
        traceback.print_exc()
        current = next((k for k, *_ in STAGES if k not in stages_out), "intake")
        err = {"stage": current, "label": next(s[1] for s in STAGES if s[0] == current), "status": "error",
               "summary": f"{type(e).__name__}: {e}", "details": {}, "findings": [], "duration_ms": 0}
        stages_out[current] = err
        _emit(run_id, {"type": "stage_end", **err})
        dec = {"decision": "NEEDS_REVIEW", "headline": f"Automatic processing failed at '{err['label']}' - routed to a person. ({e})",
               "routed_to": "AP Specialist", "next_actions": ["Process this invoice manually."], "vendor_message": None,
               "reasons": [{"code": "PROCESSING_ERROR", "severity": "hold", "stage": current, "category": "data",
                            "message": str(e), "evidence": {}}], "notes": []}
        result = {"stages": [stages_out.get(k) for k, *_ in STAGES if k in stages_out], "fields": ctx.get("fx"),
                  "findings": all_findings, "decision": dec}
        db.update_run(run_id, status="completed", decision="NEEDS_REVIEW", final_status="NEEDS_REVIEW",
                      headline=dec["headline"], duration_ms=int((time.time() - t_start) * 1000), result_json=result)
        _emit(run_id, {"type": "done", "decision": "NEEDS_REVIEW", "error": str(e)})
    finally:
        with _streams_lock:
            if run_id in _streams:
                _streams[run_id]["done"] = True


def _record_ledger(run_id: str, fx: dict, ctx: dict, decision: str) -> None:
    """Approved and on-hold invoices enter the AP ledger so later duplicates / split billing see them."""
    vendor = ctx.get("vendor") or {}
    if decision == "REJECTED" or not vendor.get("matched") or not fx.get("invoice_number"):
        return
    lines = [{"po_line": r.get("po_line"), "qty": r.get("qty"), "amount": r.get("amount")}
             for r in (ctx.get("po") or {}).get("lines", [])]
    db.add_ledger({
        "vendor_id": vendor["vendor_id"], "invoice_number": fx["invoice_number"], "invoice_key": invoice_key(fx["invoice_number"]),
        "invoice_date": fx.get("invoice_date"), "po_number": (ctx.get("po") or {}).get("po_number"),
        "subtotal": ctx["val"].get("net_amount"), "tax": ctx["val"].get("tax"), "total": fx.get("total"),
        "status": "approved" if decision == "APPROVED" else "on_hold", "source": run_id, "lines": lines,
    })
