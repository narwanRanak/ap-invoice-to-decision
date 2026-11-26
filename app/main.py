"""HTTP API + static UI.  Run:  uvicorn app.main:app --port 8000"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from collections import Counter

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, reference
from .pipeline import runner

app = FastAPI(title="AP Invoice-to-Decision", version="1.0")
db.init()


def _warm_ocr() -> None:
    """Load the OCR model and run one pass at startup so the first scanned invoice isn't slow."""
    try:
        from .pipeline.reader import read_pdf
        scan = next((s for s in json.loads((config.SAMPLES_DIR / "manifest.json").read_text()) if "scan" in s["file"]), None)
        if scan:
            read_pdf(str(config.SAMPLES_DIR / scan["file"]))
    except Exception as e:  # warm-up is best-effort only
        print(f"OCR warm-up skipped: {e}")


threading.Thread(target=_warm_ocr, daemon=True).start()

MAX_UPLOAD = 15 * 1024 * 1024


def _manifest() -> list[dict]:
    return json.loads((config.SAMPLES_DIR / "manifest.json").read_text())


@app.get("/api/health")
def health():
    return {"ok": True, "engine": "claude" if config.claude_enabled() else "rules",
            "model": config.CLAUDE_MODEL if config.claude_enabled() else None,
            "stages": [{"key": k, "label": l, "description": d} for k, l, d in runner.STAGES]}


@app.get("/api/samples")
def samples():
    return _manifest()


@app.get("/api/samples/{sample_id}/file")
def sample_file(sample_id: str):
    s = next((s for s in _manifest() if s["id"] == sample_id), None)
    if not s:
        raise HTTPException(404, "unknown sample")
    return FileResponse(config.SAMPLES_DIR / s["file"], media_type="application/pdf")


@app.post("/api/runs")
async def create_run(file: UploadFile | None = File(None), sample_id: str | None = Form(None)):
    run_id = db.next_run_id()
    if sample_id:
        s = next((s for s in _manifest() if s["id"] == sample_id), None)
        if not s:
            raise HTTPException(404, "unknown sample")
        path, name, source = str(config.SAMPLES_DIR / s["file"]), s["file"], f"sample:{sample_id}"
    elif file is not None:
        raw = await file.read()
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "File too large (max 15 MB)")
        if not raw.startswith(b"%PDF"):
            raise HTTPException(415, "Only PDF invoices are accepted")
        name = re.sub(r"[^\w.\- ]", "_", file.filename or "invoice.pdf")[:120]
        dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
        dest.write_bytes(raw)
        path, source = str(dest), "upload"
    else:
        raise HTTPException(400, "Provide a PDF file or a sample_id")
    runner.start_run(run_id, path, name, source)
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    r.pop("file_path", None)
    return r


@app.get("/api/runs/{run_id}/file")
def run_file(run_id: str):
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return FileResponse(r["file_path"], media_type="application/pdf")


_page_cache: dict[tuple, bytes] = {}


@app.get("/api/runs/{run_id}/page/{page}.png")
def run_page(run_id: str, page: int):
    """The invoice page as an image, so the UI can draw evidence highlights on top of it."""
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    key = (r["file_path"], page)
    if key not in _page_cache:
        from .pipeline.reader import render_page_png
        try:
            _page_cache[key] = render_page_png(r["file_path"], page)
        except Exception:
            raise HTTPException(404, "page not found")
    return Response(_page_cache[key], media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/runs/{run_id}/audit")
def audit_packet(run_id: str):
    """Everything an auditor needs to reproduce the decision, as one JSON document."""
    r = db.get_run(run_id)
    if not r or not r["result"]:
        raise HTTPException(404, "run not found or not finished")
    res = r["result"]
    fields = {k: v for k, v in (res.get("fields") or {}).items() if k != "evidence"}
    packet = {
        "audit_packet_version": 1,
        "run": {k: r[k] for k in ("id", "created_at", "source", "filename", "file_hash", "engine", "duration_ms")},
        "decision": {"automated": r["decision"], "final": r["final_status"], "headline": r["headline"],
                     "routed_to": res["decision"].get("routed_to"), "next_actions": res["decision"].get("next_actions"),
                     "controls_passed": res["decision"].get("controls_passed", [])},
        "human_review": r["review"],
        "findings": res.get("findings", []),
        "extracted_fields": fields,
        "stages": [{k: st.get(k) for k in ("stage", "label", "status", "summary", "duration_ms")} for st in res.get("stages", [])],
        "po_match": res.get("po"),
        "vendor_check": res.get("vendor"),
        "policy_applied": config.load_policy(),
        "generated_at": db.now_iso(),
    }
    return JSONResponse(packet, headers={"Content-Disposition": f'attachment; filename="audit-{run_id}.json"'})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """Server-sent events: one event per stage start / finish, then 'done'."""
    async def gen():
        i = 0
        for _ in range(1200):  # hard stop after ~2 min
            s = runner.stream(run_id)
            if s is None:
                r = db.get_run(run_id)
                if r and r["result"]:  # finished in a previous server process: replay from storage
                    for st in r["result"]["stages"]:
                        yield f"data: {json.dumps({'type': 'stage_end', **st})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'decision': r['decision']})}\n\n"
                return
            events = s["events"]
            while i < len(events):
                yield f"data: {json.dumps(events[i])}\n\n"
                i += 1
            if s["done"] and i >= len(events):
                return
            await asyncio.sleep(0.1)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Review(BaseModel):
    action: str        # approve | reject
    reviewer: str
    note: str


@app.post("/api/runs/{run_id}/review")
def review(run_id: str, body: Review):
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r["decision"] != "NEEDS_REVIEW" or r["review"]:
        raise HTTPException(409, "Only invoices awaiting review can be reviewed, once.")
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if len(body.note.strip()) < 5 or not body.reviewer.strip():
        raise HTTPException(400, "A reviewer name and a note explaining the decision are required for the audit trail.")
    final = "APPROVED" if body.action == "approve" else "REJECTED"
    db.update_run(run_id, final_status=final,
                  review_json={"action": body.action, "reviewer": body.reviewer.strip(), "note": body.note.strip(), "at": db.now_iso()})
    db.set_ledger_status_for_run(run_id, "approved" if final == "APPROVED" else "rejected")
    return {"ok": True, "final_status": final}


@app.get("/api/stats")
def stats():
    runs = [r for r in db.list_runs() if r["status"] == "completed"]
    by = Counter(r["final_status"] for r in runs)
    auto = Counter(r["decision"] for r in runs)
    reasons = Counter(c for r in runs for c in (r["result"] or {}).get("reason_codes", []))
    val = lambda rs: round(sum(r["total"] or 0 for r in rs), 2)
    durations = [r["duration_ms"] for r in runs if r["duration_ms"]]
    return {
        "runs": len(runs),
        "auto_decision": dict(auto),
        "final_status": dict(by),
        "pending_review": sum(1 for r in runs if r["final_status"] == "NEEDS_REVIEW"),
        "straight_through_rate": round(auto.get("APPROVED", 0) / len(runs), 3) if runs else None,
        "value_processed": val(runs),
        "value_approved": val([r for r in runs if r["final_status"] == "APPROVED"]),
        "value_on_hold": val([r for r in runs if r["final_status"] == "NEEDS_REVIEW"]),
        "value_blocked": val([r for r in runs if r["final_status"] == "REJECTED"]),
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        "top_reasons": reasons.most_common(8),
        "roi": _roi(runs, auto),
    }


# Buyer-facing ROI assumptions (shown in the UI next to the numbers)
MANUAL_MIN_PER_INVOICE = 12      # open, key in, find PO, match, decide - typical manual AP handling time
REVIEW_MIN_PER_EXCEPTION = 4     # an exception still needs a person, but arrives pre-investigated
LOADED_COST_PER_HOUR = 45.0      # fully loaded AP specialist cost, USD


def _roi(runs: list[dict], auto: Counter) -> dict:
    n = len(runs)
    exceptions = n - auto.get("APPROVED", 0)
    manual_min = n * MANUAL_MIN_PER_INVOICE
    actual_min = exceptions * REVIEW_MIN_PER_EXCEPTION
    saved_min = max(0, manual_min - actual_min)
    prevented = round(sum(r["total"] or 0 for r in runs if r["decision"] == "REJECTED"
                          or "BANK_DETAILS_CHANGED" in ((r["result"] or {}).get("reason_codes") or [])), 2)
    return {"minutes_saved": saved_min, "cost_saved": round(saved_min / 60 * LOADED_COST_PER_HOUR, 2),
            "risk_value_stopped": prevented,
            "assumptions": f"{MANUAL_MIN_PER_INVOICE} min manual handling per invoice, {REVIEW_MIN_PER_EXCEPTION} min per "
                           f"pre-investigated exception, ${LOADED_COST_PER_HOUR:.0f}/h loaded AP cost"}


@app.get("/api/reference")
def reference_data():
    ledger = db.all_ledger()
    pos = []
    for p in reference.purchase_orders().values():
        billed = round(sum(l["subtotal"] or 0 for l in ledger if l["po_number"] == p["po_number"] and l["status"] in ("paid", "approved")), 2)
        on_hold = round(sum(l["subtotal"] or 0 for l in ledger if l["po_number"] == p["po_number"] and l["status"] == "on_hold"), 2)
        v = reference.vendor_by_id(p["vendor_id"])
        pos.append({**{k: p[k] for k in ("po_number", "vendor_id", "issue_date", "description", "amount", "tax_rate", "match_type",
                                         "status", "owner", "cost_center")},
                    "vendor_name": v["name"] if v else None, "billed": billed, "on_hold": on_hold,
                    "remaining": round(p["amount"] - billed, 2), "lines": p["lines"]})
    vendors = [{**{k: v[k] for k in ("vendor_id", "name", "tax_id", "status", "payment_terms", "contact_email", "phone", "bank_name")},
                "bank_account": "••••" + v["bank_account"][-4:]} for v in reference.vendors()]
    return {"vendors": vendors, "purchase_orders": sorted(pos, key=lambda p: p["po_number"]),
            "receipts": reference.receipts(), "ledger": ledger, "policy": config.load_policy()}


@app.post("/api/reset")
def reset():
    db.init(reset=True)
    return {"ok": True}


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
