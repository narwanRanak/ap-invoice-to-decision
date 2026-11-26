"""SQLite persistence: run history (for the dashboard) and the AP ledger (for duplicates / cumulative billing)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH
from .reference import ledger_seed

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT,
    filename TEXT,
    file_path TEXT,
    file_hash TEXT,
    status TEXT NOT NULL,            -- running | completed | error
    decision TEXT,                   -- APPROVED | NEEDS_REVIEW | REJECTED
    final_status TEXT,               -- decision after human review, if any
    vendor_name TEXT,
    invoice_number TEXT,
    po_number TEXT,
    total REAL,
    engine TEXT,
    duration_ms INTEGER,
    headline TEXT,
    result_json TEXT,
    review_json TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_key TEXT NOT NULL,
    invoice_date TEXT,
    po_number TEXT,
    subtotal REAL,
    tax REAL,
    total REAL,
    status TEXT NOT NULL,            -- paid | approved | on_hold | rejected
    source TEXT,                     -- 'ERP import' or the run id that created it
    lines_json TEXT,
    created_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init(reset: bool = False) -> None:
    from .pipeline.normalize import invoice_key

    with _lock, connect() as c:
        if reset:
            c.executescript("DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS ledger;")
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0:
            for r in ledger_seed():
                c.execute(
                    "INSERT INTO ledger (vendor_id, invoice_number, invoice_key, invoice_date, po_number, subtotal, tax, total, status, source, lines_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["vendor_id"], r["invoice_number"], invoice_key(r["invoice_number"]), r["invoice_date"],
                     r["po_number"], r["subtotal"], r["tax"], r["total"], r["status"], r["source"], "[]", now_iso()),
                )


# ----------------------------------------------------------------------- runs
def next_run_id() -> str:
    with _lock, connect() as c:
        n = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    return f"RUN-{n + 1:04d}"


def create_run(run_id: str, source: str, filename: str, file_path: str) -> None:
    with _lock, connect() as c:
        c.execute("INSERT INTO runs (id, created_at, source, filename, file_path, status) VALUES (?,?,?,?,?,?)",
                  (run_id, now_iso(), source, filename, file_path, "running"))


def update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = [json.dumps(v) if k.endswith("_json") and not isinstance(v, str) else v for k, v in fields.items()]
    with _lock, connect() as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*vals, run_id))


def get_run(run_id: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run_dict(r) if r else None


def list_runs() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC").fetchall()
    return [_run_dict(r, light=True) for r in rows]


def _run_dict(r: sqlite3.Row, light: bool = False) -> dict:
    d = dict(r)
    d["result"] = json.loads(d.pop("result_json") or "null")
    d["review"] = json.loads(d.pop("review_json") or "null")
    if light and d["result"]:
        res = d["result"]
        d["result"] = {
            "reason_codes": [f["code"] for f in res.get("findings", []) if f["severity"] in ("hold", "reject")],
            "routed_to": (res.get("decision") or {}).get("routed_to"),
        }
    return d


def file_hash_seen(file_hash: str, exclude_run: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT id, created_at, decision FROM runs WHERE file_hash = ? AND id != ? AND status = 'completed' "
                      "ORDER BY created_at LIMIT 1", (file_hash, exclude_run)).fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------------- ledger
def ledger_rows(vendor_id: str | None = None, po_number: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM ledger WHERE status != 'rejected'", []
    if vendor_id:
        q += " AND vendor_id = ?"
        args.append(vendor_id)
    if po_number:
        q += " AND po_number = ?"
        args.append(po_number)
    with connect() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY invoice_date", args).fetchall()]
    for r in rows:
        r["lines"] = json.loads(r.pop("lines_json") or "[]")
    return rows


def all_ledger() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ledger ORDER BY invoice_date DESC").fetchall()]


def add_ledger(entry: dict) -> None:
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO ledger (vendor_id, invoice_number, invoice_key, invoice_date, po_number, subtotal, tax, total, status, source, lines_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry["vendor_id"], entry["invoice_number"], entry["invoice_key"], entry.get("invoice_date"),
             entry.get("po_number"), entry.get("subtotal"), entry.get("tax"), entry.get("total"), entry["status"],
             entry["source"], json.dumps(entry.get("lines", [])), now_iso()),
        )


def set_ledger_status_for_run(run_id: str, status: str) -> None:
    with _lock, connect() as c:
        c.execute("UPDATE ledger SET status = ? WHERE source = ?", (status, run_id))
