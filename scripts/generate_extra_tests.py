"""Extra invoices NOT among the six demo scenarios - used to show the process generalises.

Run:  python scripts/generate_extra_tests.py   -> test_inputs/*.pdf
Upload any of them through the UI ("Upload invoice PDF").
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "test_inputs"
OUT.mkdir(exist_ok=True)


def simple_invoice(path, vendor, addr, meta, lines, tax_label, tax, footer):
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    c.setFont("Helvetica-Bold", 17)
    c.drawString(40, H - 55, vendor)
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 70, addr)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 40, H - 55, "INVOICE")
    c.setFont("Helvetica", 10)
    for i, (k, v) in enumerate(meta):
        c.drawString(380, H - 90 - i * 15, k)
        c.drawRightString(W - 40, H - 90 - i * 15, v)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, H - 100, "Bill To:")
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 112, "Northwind Manufacturing Inc., 2200 Foundry Road, Columbus, OH 43215")
    y = H - 200
    c.setFont("Helvetica-Bold", 10)
    for h, x, r in [("Description", 40, False), ("Qty", 390, True), ("Unit Price", 470, True), ("Amount", 560, True)]:
        (c.drawRightString if r else c.drawString)(x, y, h)
    c.setFont("Helvetica", 10)
    sub = 0.0
    for d, q, u in lines:
        y -= 16
        amt = round(q * u, 2)
        sub += amt
        c.drawString(40, y, d)
        c.drawRightString(390, y, f"{q:g}")
        c.drawRightString(470, y, f"{u:,.2f}")
        c.drawRightString(560, y, f"{amt:,.2f}")
    y -= 30
    for k, v in [("Subtotal", sub), (tax_label, tax), ("Total Due", sub + tax)]:
        c.drawString(380, y, k)
        c.drawRightString(560, y, f"{v:,.2f}")
        y -= 16
    c.setFont("Helvetica", 9)
    c.drawString(40, 96, footer)
    c.save()


simple_invoice(OUT / "summit_blocked_vendor.pdf", "Summit Electrical Contractors", "12 Main St, Columbus, OH - office@summitelectric.com",
               [("Invoice No:", "SEC-4410"), ("Date:", "2026-11-20"), ("PO Number:", "PO-4500135")],
               [("Emergency call-out electrician", 6, 120.0)], "Sales Tax 6%", 43.20,
               "Remit to Ohio Valley Bank, Account 1180032219, Routing 042000314")

simple_invoice(OUT / "pinecrest_unknown_vendor.pdf", "Pinecrest Janitorial Group", "88 Elm Ave, Columbus, OH - billing@pinecrestjan.com",
               [("Invoice No:", "PJ-0071"), ("Date:", "2026-11-17")],
               [("Office deep clean - November", 1, 1850.0)], "Tax", 0.0,
               "Remit to Midwest Federal Bank, Account 7712009931, Routing 044115090")

simple_invoice(OUT / "riverbend_price_increase.pdf", "Riverbend Packaging Co.", "1201 River Rd, Nashville TN 37209 - invoices@riverbendpack.com",
               [("Invoice No:", "RP-55140"), ("Date:", "2026-11-24"), ("PO Number:", "PO-4500141"), ("Terms:", "Net 30")],
               [("Packing tape 48mm x 110m", 300, 3.45)], "Sales Tax 7%", round(300 * 3.45 * 0.07, 2),
               "Remit: Cumberland Valley Bank Acct 5530127789 Routing 064000017")

simple_invoice(OUT / "bluepeak_missing_number.pdf", "Bluepeak Industrial Supply Inc.", "880 W Fulton Market, Chicago, IL 60607 - ar@bluepeakindustrial.com",
               [("PO Number:", "PO-4500123")],
               [("Flange gasket kit", 20, 18.75)], "Sales Tax (8.25%)", round(375 * 0.0825, 2),
               "Remit to: Great Lakes Commercial Bank | Account 4402198871 | Routing 071000013")

print("wrote", sorted(p.name for p in OUT.glob("*.pdf")))
