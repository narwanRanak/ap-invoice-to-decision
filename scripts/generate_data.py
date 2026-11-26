"""Generate the synthetic procurement dataset and the demo invoice PDFs.

Run:  python scripts/generate_data.py

Writes:
  data/vendors.csv, data/purchase_orders.csv, data/po_lines.csv,
  data/receipts.csv, data/ledger_seed.csv, data/policy.json
  samples/*.pdf + samples/manifest.json
Each vendor gets its own invoice layout on purpose: real AP inboxes are messy.
"""
from __future__ import annotations

import csv
import io
import json
import random
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageFilter, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLES = ROOT / "samples"
DATA.mkdir(exist_ok=True)
SAMPLES.mkdir(exist_ok=True)

BUYER = ["Northwind Manufacturing Inc.", "Accounts Payable", "2200 Foundry Road",
         "Columbus, OH 43215", "ap@northwind-mfg.example"]

# ----------------------------------------------------------------- reference data
VENDORS = [
    dict(vendor_id="V001", name="Bluepeak Industrial Supply Inc.", aliases="Bluepeak Industrial|Bluepeak Supply",
         tax_id="36-4418207", status="approved", payment_terms="Net 30", contact_email="ar@bluepeakindustrial.com",
         phone="+1 (312) 555-0148", bank_name="Great Lakes Commercial Bank", bank_account="4402198871", routing="071000013"),
    dict(vendor_id="V002", name="Cobalt Logistics LLC", aliases="Cobalt Logistics|Cobalt Freight",
         tax_id="75-2290314", status="approved", payment_terms="Net 15", contact_email="billing@cobaltlogistics.com",
         phone="+1 (214) 555-0192", bank_name="Lone Star National Bank", bank_account="9917004532", routing="111000025"),
    dict(vendor_id="V003", name="Meridian Office Solutions", aliases="Meridian Office|Meridian Workplace",
         tax_id="31-5523870", status="approved", payment_terms="Net 30", contact_email="accounts@meridianoffice.com",
         phone="+1 (614) 555-0117", bank_name="Buckeye Savings & Trust", bank_account="2018833406", routing="044000037"),
    dict(vendor_id="V004", name="Riverbend Packaging Co.", aliases="Riverbend Packaging|Riverbend Pkg",
         tax_id="62-1187455", status="approved", payment_terms="Net 30", contact_email="invoices@riverbendpack.com",
         phone="+1 (615) 555-0163", bank_name="Cumberland Valley Bank", bank_account="5530127789", routing="064000017"),
    dict(vendor_id="V005", name="Northstar Software Corp.", aliases="Northstar Software|Northstar",
         tax_id="94-3376120", status="approved", payment_terms="Net 30", contact_email="ar@northstarsoftware.io",
         phone="+1 (415) 555-0136", bank_name="Pacific Crest Bank", bank_account="7781200459", routing="121000248"),
    dict(vendor_id="V006", name="Halden Electrical Services", aliases="Halden Electrical|Halden Electric",
         tax_id="31-6640921", status="approved", payment_terms="Net 30", contact_email="ap@haldenelectrical.com",
         phone="+1 (614) 555-0175", bank_name="First Lakeshore Bank", bank_account="3309184471", routing="041000124"),
    dict(vendor_id="V007", name="Summit Electrical Contractors", aliases="Summit Electrical",
         tax_id="31-7781023", status="blocked", payment_terms="Net 30", contact_email="office@summitelectric.com",
         phone="+1 (614) 555-0109", bank_name="Ohio Valley Bank", bank_account="1180032219", routing="042000314"),
]

# PO header rows: amount is pre-tax; tax_rate is the expected sales-tax rate
POS = [
    dict(po_number="PO-4500118", vendor_id="V005", issue_date="2025-09-20", description="Northstar Analytics annual licence (120 seats)",
         amount=14400.00, tax_rate=0.0, match_type="2-way", status="open", owner="Priya Raman (IT)", cost_center="CC-410 IT"),
    dict(po_number="PO-4500123", vendor_id="V001", issue_date="2026-10-28", description="Hydraulic line maintenance parts - Plant 2",
         amount=8135.00, tax_rate=0.0825, match_type="3-way", status="open", owner="Marcus Webb (Maintenance)", cost_center="CC-220 Plant 2"),
    dict(po_number="PO-4500127", vendor_id="V002", issue_date="2026-11-02", description="FTL freight Chicago -> Dallas, November",
         amount=11100.00, tax_rate=0.0, match_type="2-way", status="open", owner="Dana Ortiz (Logistics)", cost_center="CC-310 Logistics"),
    dict(po_number="PO-4500131", vendor_id="V003", issue_date="2026-08-15", description="Office fit-out Floor 3 - fixed fee, 3 milestones",
         amount=18000.00, tax_rate=0.0, match_type="2-way", status="open", owner="Elena Fischer (Facilities)", cost_center="CC-120 Facilities"),
    dict(po_number="PO-4500135", vendor_id="V006", issue_date="2026-10-20", description="Main panel upgrade - Building B",
         amount=10330.00, tax_rate=0.06, match_type="2-way", status="open", owner="Elena Fischer (Facilities)", cost_center="CC-120 Facilities"),
    dict(po_number="PO-4500140", vendor_id="V004", issue_date="2026-10-30", description="Outbound packaging - Q4 restock",
         amount=3872.00, tax_rate=0.07, match_type="3-way", status="open", owner="Dana Ortiz (Logistics)", cost_center="CC-310 Logistics"),
    dict(po_number="PO-4500141", vendor_id="V004", issue_date="2026-11-05", description="Packing tape restock",
         amount=930.00, tax_rate=0.07, match_type="3-way", status="open", owner="Dana Ortiz (Logistics)", cost_center="CC-310 Logistics"),
    dict(po_number="PO-4500102", vendor_id="V001", issue_date="2026-06-11", description="Bearings and seals - Plant 1",
         amount=2410.00, tax_rate=0.0825, match_type="3-way", status="closed", owner="Marcus Webb (Maintenance)", cost_center="CC-210 Plant 1"),
]

PO_LINES = [
    # po, line, sku, description, qty, unit_price
    ("PO-4500118", 1, "NS-ANL-SEAT", "Northstar Analytics - annual seat licence", 120, 120.00),
    ("PO-4500123", 1, "BP-HX220", "Hydraulic hose assembly 3/4in x 10ft", 40, 86.50),
    ("PO-4500123", 2, "BP-VLV-115", "Stainless ball valve 1in", 25, 142.00),
    ("PO-4500123", 3, "BP-GSK-09", "Flange gasket kit", 60, 18.75),
    ("PO-4500127", 1, "FTL-CHI-DAL", "Full truckload freight Chicago to Dallas", 6, 1850.00),
    ("PO-4500131", 1, "FITOUT-F3", "Office fit-out Floor 3 (fixed fee)", 1, 18000.00),
    ("PO-4500135", 1, "LAB-ELEC", "Electrician labour (hours)", 64, 95.00),
    ("PO-4500135", 2, "MAT-PANEL", "Panel, breakers and materials (lot)", 1, 4250.00),
    ("PO-4500140", 1, "CB-181210", "Corrugated box 18x12x10 double wall", 2000, 1.42),
    ("PO-4500140", 2, "SW-18-80", "Stretch wrap 18in x 1500ft roll", 48, 21.50),
    ("PO-4500141", 1, "TP-48-110", "Packing tape 48mm x 110m", 300, 3.10),
    ("PO-4500102", 1, "BR-6204", "Deep groove bearing 6204", 100, 14.10),
    ("PO-4500102", 2, "SL-3550", "Shaft seal 35x50", 100, 10.00),
]

RECEIPTS = [
    # grn, po, line, qty_received, date
    ("GRN-77810", "PO-4500123", 1, 40, "2026-11-12"),
    ("GRN-77810", "PO-4500123", 2, 25, "2026-11-12"),
    ("GRN-77810", "PO-4500123", 3, 60, "2026-11-12"),
    ("GRN-77842", "PO-4500140", 1, 2000, "2026-11-14"),
    ("GRN-77842", "PO-4500140", 2, 48, "2026-11-14"),
    ("GRN-77601", "PO-4500102", 1, 100, "2026-06-25"),
    ("GRN-77601", "PO-4500102", 2, 100, "2026-06-25"),
]

# Invoices already processed by AP before the demo (history the process must respect)
LEDGER_SEED = [
    dict(vendor_id="V005", invoice_number="NS-2025-0912", invoice_date="2026-10-02", po_number="PO-4500118",
         subtotal=14400.00, tax=0.0, total=14400.00, status="paid", source="ERP import"),
    dict(vendor_id="V003", invoice_number="MOS-1107", invoice_date="2026-09-30", po_number="PO-4500131",
         subtotal=7200.00, tax=0.0, total=7200.00, status="paid", source="ERP import"),
    dict(vendor_id="V003", invoice_number="MOS-1142", invoice_date="2026-10-31", po_number="PO-4500131",
         subtotal=7200.00, tax=0.0, total=7200.00, status="approved", source="ERP import"),
    dict(vendor_id="V001", invoice_number="INV-23950", invoice_date="2026-07-01", po_number="PO-4500102",
         subtotal=2410.00, tax=198.83, total=2608.83, status="paid", source="ERP import"),
]

POLICY = {
    "currency": "USD",
    "header_tolerance_pct": 2.0,
    "header_tolerance_abs": 250.0,
    "line_price_tolerance_pct": 2.0,
    "tax_rate_tolerance_pp": 0.25,
    "auto_approve_limit": 25000.0,
    "max_invoice_age_days": 180,
    "duplicate_fuzzy_window_days": 45,
    "min_field_confidence": 0.70,
    "min_ocr_confidence": 0.80,
    "inferred_po_min_score": 0.85,
}


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_reference_data():
    write_csv(DATA / "vendors.csv", VENDORS)
    write_csv(DATA / "purchase_orders.csv", POS)
    write_csv(DATA / "po_lines.csv", [dict(po_number=p, line=l, sku=s, description=d, qty=q, unit_price=u)
                                       for p, l, s, d, q, u in PO_LINES])
    write_csv(DATA / "receipts.csv", [dict(grn=g, po_number=p, line=l, qty_received=q, receipt_date=d)
                                       for g, p, l, q, d in RECEIPTS])
    write_csv(DATA / "ledger_seed.csv", LEDGER_SEED)
    (DATA / "policy.json").write_text(json.dumps(POLICY, indent=2))


# ----------------------------------------------------------------- PDF helpers
def money(x: float) -> str:
    return f"{x:,.2f}"


def bill_to(c, x, y, size=9):
    c.setFont("Helvetica-Bold", size)
    c.drawString(x, y, "Bill To:")
    c.setFont("Helvetica", size)
    for i, line in enumerate(BUYER):
        c.drawString(x, y - (i + 1) * (size + 3), line)


def table(c, x0, y, headers, col_x, rows, size=9, aligns=None, header_fill=None):
    aligns = aligns or ["l"] * len(headers)
    if header_fill:
        c.setFillColor(header_fill)
        c.rect(x0 - 4, y - 4, col_x[-1] - x0 + 70, 16, fill=1, stroke=0)
        c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", size)
    for h, cx, a in zip(headers, col_x, aligns):
        (c.drawRightString if a == "r" else c.drawString)(cx, y, h)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", size)
    y -= 18
    for r in rows:
        for v, cx, a in zip(r, col_x, aligns):
            (c.drawRightString if a == "r" else c.drawString)(cx, y, str(v))
        y -= 15
    return y


# ----------------------------------------------------------------- invoices
def inv_bluepeak(path):
    """Happy path A: clean, itemised, tax separate, explicit PO."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    c.setFillColor(colors.HexColor("#0B3D91"))
    c.rect(0, H - 90, W, 90, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, H - 50, "Bluepeak Industrial Supply Inc.")
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 68, "880 W Fulton Market, Chicago, IL 60607  |  ar@bluepeakindustrial.com  |  Tax ID 36-4418207")
    c.setFont("Helvetica-Bold", 24)
    c.drawRightString(W - 40, H - 52, "INVOICE")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    meta = [("Invoice No:", "INV-24817"), ("Invoice Date:", "2026-11-18"), ("Due Date:", "2026-12-18"),
            ("PO Number:", "PO-4500123"), ("Terms:", "Net 30")]
    for i, (k, v) in enumerate(meta):
        c.drawString(380, H - 120 - i * 15, k)
        c.drawString(470, H - 120 - i * 15, v)
    bill_to(c, 40, H - 120)
    rows = [("BP-HX220", "Hydraulic hose assembly 3/4in x 10ft", 40, money(86.50), money(3460.00)),
            ("BP-VLV-115", "Stainless ball valve 1in", 25, money(142.00), money(3550.00)),
            ("BP-GSK-09", "Flange gasket kit", 60, money(18.75), money(1125.00))]
    y = table(c, 40, H - 230, ["Item", "Description", "Qty", "Unit Price", "Amount"],
              [40, 120, 390, 470, 560], rows, aligns=["l", "l", "r", "r", "r"],
              header_fill=colors.HexColor("#0B3D91"))
    y -= 10
    c.line(360, y + 5, 570, y + 5)
    for k, v in [("Subtotal", money(8135.00)), ("Sales Tax (8.25%)", money(671.14)), ("Total Due (USD)", money(8806.14))]:
        c.setFont("Helvetica-Bold" if "Total" in k else "Helvetica", 10)
        c.drawString(380, y - 10, k)
        c.drawRightString(560, y - 10, v)
        y -= 16
    c.setFont("Helvetica", 9)
    c.drawString(40, 110, "Remit to: Great Lakes Commercial Bank  |  Account 4402198871  |  Routing 071000013")
    c.drawString(40, 96, "Please reference the invoice number with your payment. Thank you for your business.")
    c.save()


def inv_cobalt(path):
    """Happy path B: freight bill, fuel surcharge not on PO (+1.1%), no sales tax, different labels."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, H - 50, "COBALT LOGISTICS LLC")
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 64, "4100 Commerce St, Dallas, TX 75226 - billing@cobaltlogistics.com - EIN 75-2290314")
    c.setStrokeColor(colors.HexColor("#1F6F8B"))
    c.setLineWidth(2)
    c.line(40, H - 74, W - 40, H - 74)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, H - 100, "FREIGHT BILL")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 120, "Bill No.: CL-88213")
    c.drawString(40, H - 135, "Bill Date: Nov 20, 2026")
    c.drawString(40, H - 150, "Customer PO: PO-4500127")
    c.drawString(40, H - 165, "Payment Terms: Net 15")
    bill_to(c, 360, H - 100)
    rows = [("Full truckload freight Chicago to Dallas", "6", money(1850.00), money(11100.00)),
            ("Fuel surcharge (diesel index adj.)", "1", money(122.10), money(122.10))]
    y = table(c, 40, H - 220, ["Service", "Loads", "Rate", "Charge"], [40, 360, 450, 560], rows,
              aligns=["l", "r", "r", "r"])
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(360, y + 2, 570, y + 2)
    c.setFont("Helvetica", 10)
    c.drawString(380, y - 12, "Subtotal")
    c.drawRightString(560, y - 12, money(11222.10))
    c.drawString(380, y - 28, "Tax")
    c.drawRightString(560, y - 28, "0.00")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(380, y - 46, "AMOUNT DUE USD")
    c.drawRightString(560, y - 46, money(11222.10))
    c.setFont("Helvetica", 9)
    c.drawString(40, 110, "Interstate freight transportation - not subject to sales tax.")
    c.drawString(40, 96, "ACH: Lone Star National Bank, Acct 9917004532, ABA 111000025")
    c.save()


def inv_meridian(path):
    """Edge 1: third milestone on a split PO; cumulative billing exceeds PO value."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    c.setFillColor(colors.HexColor("#F2EFE8"))
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#3A3A3A"))
    c.setFont("Times-Bold", 22)
    c.drawString(40, H - 60, "Meridian Office Solutions")
    c.setFont("Times-Roman", 10)
    c.drawString(40, H - 76, "Workplace design & fit-out  -  95 N High St, Columbus, OH 43215  -  accounts@meridianoffice.com")
    c.setFont("Times-Bold", 14)
    c.drawRightString(W - 40, H - 110, "Tax Invoice")
    c.setFont("Times-Roman", 11)
    lines = ["Invoice #: MOS-1188", "Date of issue: 22/11/2026", "Your reference: PO-4500131",
             "Project: Office fit-out Floor 3"]
    for i, l in enumerate(lines):
        c.drawRightString(W - 40, H - 130 - i * 15, l)
    bill_to(c, 40, H - 110, size=10)
    rows = [("Office fit-out Floor 3 (fixed fee) - Milestone 3: final handover (30%)", "1", money(5400.00), money(5400.00))]
    y = table(c, 40, H - 250, ["Description", "Qty", "Unit Price", "Line Total"], [40, 400, 480, 560], rows,
              aligns=["l", "r", "r", "r"])
    c.setFont("Times-Roman", 10)
    c.drawString(40, y - 10, "Billing schedule: M1 design sign-off 40% (MOS-1107)  |  M2 construction 40% (MOS-1142)  |  M3 handover 30% (this invoice)")
    c.setFont("Times-Roman", 11)
    c.drawString(380, y - 40, "Subtotal")
    c.drawRightString(560, y - 40, money(5400.00))
    c.drawString(380, y - 56, "Tax (services exempt)")
    c.drawRightString(560, y - 56, "0.00")
    c.setFont("Times-Bold", 12)
    c.drawString(380, y - 76, "Balance due")
    c.drawRightString(560, y - 76, "$" + money(5400.00))
    c.setFont("Times-Roman", 9)
    c.drawString(40, 96, "Payment within 30 days to Buckeye Savings & Trust, account 2018833406, routing 044000037.")
    c.save()


def inv_riverbend_scanned(path):
    """Edge 2: scanned image-only invoice, no PO reference; PO must be inferred."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    W, H = LETTER
    c.setFont("Courier-Bold", 18)
    c.drawString(50, H - 60, "RIVERBEND PACKAGING CO.")
    c.setFont("Courier", 10)
    c.drawString(50, H - 76, "1201 River Rd, Nashville TN 37209")
    c.drawString(50, H - 90, "invoices@riverbendpack.com")
    c.setFont("Courier-Bold", 14)
    c.drawString(400, H - 60, "INVOICE")
    c.setFont("Courier", 11)
    c.drawString(400, H - 80, "Invoice: RP-55102")
    c.drawString(400, H - 96, "Date: 2026-11-19")
    c.drawString(400, H - 112, "Terms: Net 30")
    c.setFont("Courier", 11)
    c.drawString(50, H - 130, "Sold To: Northwind Manufacturing Inc.")
    c.drawString(50, H - 146, "2200 Foundry Road, Columbus OH 43215")
    y = H - 200
    c.setFont("Courier-Bold", 11)
    c.drawString(50, y, "Description")
    c.drawRightString(390, y, "Qty")
    c.drawRightString(470, y, "Price")
    c.drawRightString(560, y, "Amount")
    c.line(50, y - 5, 560, y - 5)
    c.setFont("Courier", 11)
    items = [("Corrugated box 18x12x10 double wall", "2000", "1.42", "2,840.00"),
             ("Stretch wrap 18in x 1500ft roll", "48", "21.50", "1,032.00")]
    for d, q, p, a in items:
        y -= 22
        c.drawString(50, y, d)
        c.drawRightString(390, y, q)
        c.drawRightString(470, y, p)
        c.drawRightString(560, y, a)
    y -= 40
    for k, v in [("Subtotal", "3,872.00"), ("Sales Tax 7%", "271.04"), ("TOTAL DUE", "4,143.04")]:
        c.setFont("Courier-Bold" if "TOTAL" in k else "Courier", 12)
        c.drawString(360, y, k)
        c.drawRightString(560, y, v)
        y -= 20
    c.setFont("Courier", 9)
    c.drawString(50, 110, "Remit: Cumberland Valley Bank  Acct 5530127789  Routing 064000017")
    c.save()
    # rasterise -> make it look scanned -> image-only PDF
    doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    pix = doc[0].get_pixmap(dpi=170)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    img = img.rotate(-0.9, expand=True, fillcolor=255, resample=Image.BICUBIC)
    rnd = random.Random(7)
    px = img.load()
    for _ in range(int(img.width * img.height * 0.004)):
        x, yy = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, yy] = rnd.choice((90, 140, 200))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img = ImageOps.autocontrast(img, cutoff=1)
    img = Image.eval(img, lambda v: int(v * 0.93 + 12))  # paper tone
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PDF", resolution=170)
    path.write_bytes(out.getvalue())


def inv_northstar_dup(path):
    """Edge 3: 'past due' resend of an already-paid invoice, number reformatted."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(colors.HexColor("#4B2E83"))
    c.drawString(40, H - 55, "northstar")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 70, "Northstar Software Corp.  |  500 Howard St, San Francisco, CA 94105  |  ar@northstarsoftware.io")
    c.setFillColor(colors.HexColor("#C62828"))
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(W - 40, H - 55, "PAYMENT REMINDER - PAST DUE")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    info = [("Invoice Number", "NS-2025-912"), ("Invoice Date", "October 2, 2026"),
            ("Purchase Order", "PO-4500118"), ("Subscription period", "Oct 1, 2026 - Sep 30, 2027")]
    for i, (k, v) in enumerate(info):
        c.drawString(40, H - 110 - i * 15, k + ":")
        c.drawString(170, H - 110 - i * 15, v)
    bill_to(c, 380, H - 110)
    rows = [("Northstar Analytics - annual seat licence", "120", money(120.00), money(14400.00))]
    y = table(c, 40, H - 220, ["Product", "Seats", "Price / seat", "Amount"], [40, 360, 460, 560], rows,
              aligns=["l", "r", "r", "r"])
    c.setFont("Helvetica-Bold", 11)
    c.drawString(380, y - 15, "Total USD")
    c.drawRightString(560, y - 15, money(14400.00))
    c.setFont("Helvetica", 10)
    c.drawString(40, y - 60, "Our records show this invoice remains unpaid. Please arrange payment immediately")
    c.drawString(40, y - 74, "to avoid service interruption.")
    c.setFont("Helvetica", 9)
    c.drawString(40, 96, "Wire: Pacific Crest Bank, Account 7781200459, Routing 121000248")
    c.save()


def inv_halden_bank(path):
    """Edge 4: numbers match the PO exactly, but remit-to bank account changed + lookalike email domain."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, H - 55, "Halden Electrical Services")
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 70, "Licensed commercial electrical contractor - 740 Parsons Ave, Columbus, OH 43206")
    c.drawString(40, H - 82, "billing@halden-elec.co  |  (614) 555-0199")
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 40, H - 55, "INVOICE")
    c.setFont("Helvetica", 10)
    for i, (k, v) in enumerate([("Invoice No", "HES-2291"), ("Date", "2026-11-21"), ("PO #", "PO-4500135"),
                                ("Terms", "Due on receipt")]):
        c.drawString(390, H - 80 - i * 15, k)
        c.drawRightString(W - 40, H - 80 - i * 15, v)
    bill_to(c, 40, H - 120)
    rows = [("Electrician labour (hours)", "64", money(95.00), money(6080.00)),
            ("Panel, breakers and materials (lot)", "1", money(4250.00), money(4250.00))]
    y = table(c, 40, H - 230, ["Description", "Qty", "Rate", "Amount"], [40, 380, 470, 560], rows,
              aligns=["l", "r", "r", "r"])
    c.line(360, y + 2, 570, y + 2)
    for k, v in [("Subtotal", money(10330.00)), ("Sales Tax 6%", money(619.80)), ("Total", money(10949.80))]:
        c.setFont("Helvetica-Bold" if k == "Total" else "Helvetica", 10)
        c.drawString(380, y - 12, k)
        c.drawRightString(560, y - 12, v)
        y -= 16
    c.setFillColor(colors.HexColor("#FFF4D6"))
    c.rect(36, 90, W - 72, 74, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, 148, "IMPORTANT - OUR BANKING DETAILS HAVE CHANGED")
    c.setFont("Helvetica", 9)
    c.drawString(46, 133, "Please update your records and remit all payments to our new account:")
    c.drawString(46, 119, "Bank: Crestline Digital Bank   Account: 88213390   Routing: 121000358")
    c.drawString(46, 105, "Payments to the old account will be returned. Questions: billing@halden-elec.co")
    c.save()


SCENARIOS = [
    dict(id="happy-bluepeak", file="bluepeak_INV-24817.pdf", builder=inv_bluepeak, kind="Happy path",
         title="Clean itemised invoice, explicit PO",
         blurb="Text PDF, tax separate, goods fully received. Should sail through a 3-way match.",
         expected="APPROVED"),
    dict(id="happy-cobalt", file="cobalt_CL-88213.pdf", builder=inv_cobalt, kind="Happy path",
         title="Freight bill with surcharge inside tolerance",
         blurb="Fuel surcharge not on the PO (+1.1%). Different labels and layout. Within the 2% / $250 policy.",
         expected="APPROVED"),
    dict(id="edge-split-po", file="meridian_MOS-1188.pdf", builder=inv_meridian, kind="Edge case 1",
         title="Split PO: third invoice overbills",
         blurb="Milestone 3 of a PO already billed 80%. Looks fine alone; cumulatively 110% of the PO.",
         expected="NEEDS_REVIEW"),
    dict(id="edge-scanned-nopo", file="riverbend_RP-55102_scan.pdf", builder=inv_riverbend_scanned, kind="Edge case 2",
         title="Scanned invoice, no PO reference",
         blurb="Image-only skewed scan. Needs OCR, then the PO must be inferred from two open POs.",
         expected="APPROVED"),
    dict(id="edge-duplicate", file="northstar_NS-2025-912_reminder.pdf", builder=inv_northstar_dup, kind="Edge case 3",
         title="Disguised duplicate (past-due reminder)",
         blurb="Re-send of an invoice already paid, with the number reformatted (912 vs 0912).",
         expected="REJECTED"),
    dict(id="edge-bank-change", file="halden_HES-2291.pdf", builder=inv_halden_bank, kind="Edge case 4",
         title="Bank details changed (payment fraud signal)",
         blurb="Matches the PO perfectly, but asks to pay a new bank account from a lookalike email domain.",
         expected="NEEDS_REVIEW"),
]


def main():
    write_reference_data()
    manifest = []
    for s in SCENARIOS:
        s["builder"](SAMPLES / s["file"])
        manifest.append({k: v for k, v in s.items() if k != "builder"})
    (SAMPLES / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(manifest)} invoices and reference data")


if __name__ == "__main__":
    main()
