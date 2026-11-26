"""Stages 4-7 - deterministic checks. Each returns (summary dict, list[Finding]).

Severity ladder:  info < warn < hold < reject
  hold   -> invoice cannot be paid automatically; a named owner must act
  reject -> paying would clearly be wrong (duplicate, unknown/blocked vendor)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .. import db, reference
from .normalize import (email_domain, fmt_money, invoice_key, name_similarity, text_similarity, to_date)


@dataclass
class Finding:
    code: str
    severity: str            # info | warn | hold | reject
    stage: str
    message: str
    category: str = "data"   # data | vendor | duplicate | po | security | policy
    evidence: dict = field(default_factory=dict)

    def dict(self):
        return asdict(self)


def _f(code, severity, stage, message, category="data", **evidence):
    return Finding(code, severity, stage, message, category, evidence)


def _pct(a: float, b: float) -> float:
    return 0.0 if not b else round(100.0 * a / b, 2)


# ------------------------------------------------------------------ 4. validate
def validate(fx: dict, policy: dict, today: date) -> tuple[dict, list[Finding]]:
    S = "validate"
    out: list[Finding] = []
    conf = fx.get("confidence", {})
    checks = []

    for key, label in [("vendor_name", "Vendor name"), ("invoice_number", "Invoice number"),
                       ("invoice_date", "Invoice date"), ("total", "Invoice total")]:
        if fx.get(key) in (None, ""):
            out.append(_f("MISSING_FIELD", "hold", S, f"{label} could not be found on the invoice.", field=key))
            checks.append((f"{label} present", "fail"))
        elif conf.get(key, 1.0) < policy["min_field_confidence"]:
            out.append(_f("LOW_CONFIDENCE_FIELD", "hold", S,
                          f"{label} was read with low confidence ({conf.get(key):.0%}); a person should confirm it.",
                          field=key, value=fx.get(key), confidence=conf.get(key)))
            checks.append((f"{label} confident", "warn"))
        else:
            checks.append((f"{label} present", "pass"))

    missing_core = [k for k in ("invoice_number", "invoice_date", "total") if fx.get(k) in (None, "")]
    if len(missing_core) == 3:
        out.append(_f("NOT_AN_INVOICE", "hold", S,
                      "No invoice number, invoice date or total found. This may not be an invoice (statement, quote, letter) "
                      "or it may be unreadable. Sent to a person.", missing=missing_core))

    if fx.get("invoice_date_ambiguous"):
        out.append(_f("AMBIGUOUS_DATE", "warn", S, "Invoice date is ambiguous (dd/mm vs mm/dd); US format assumed.",
                      value=fx.get("invoice_date")))

    lines = fx.get("line_items") or []
    bad_lines = []
    for i, li in enumerate(lines, 1):
        if li.get("qty") is not None and li.get("unit_price") is not None and li.get("amount") is not None:
            if abs(li["qty"] * li["unit_price"] - li["amount"]) > 0.02 + 0.0005 * li["amount"]:
                bad_lines.append(i)
    if bad_lines:
        out.append(_f("MATH_MISMATCH", "hold", S, f"Line(s) {bad_lines}: quantity x unit price does not equal the line amount.",
                      lines=bad_lines))
    checks.append(("Line arithmetic (qty x price)", "fail" if bad_lines else ("pass" if lines else "skip")))

    line_sum = round(sum(li.get("amount") or 0 for li in lines), 2)
    subtotal = fx.get("subtotal")
    total = fx.get("total")
    tax = fx.get("tax_amount")
    derived = []
    if subtotal is None and lines:
        subtotal = line_sum
        derived.append("subtotal")
    if tax is None and subtotal is not None and total is not None:
        tax = round(total - subtotal, 2)
        derived.append("tax")
    if lines and subtotal is not None and "subtotal" not in derived:
        ok = abs(line_sum - subtotal) <= 0.02
        if not ok:
            out.append(_f("MATH_MISMATCH", "hold", S,
                          f"Line items add up to {fmt_money(line_sum)} but the invoice subtotal says {fmt_money(subtotal)}.",
                          line_sum=line_sum, subtotal=subtotal))
        checks.append(("Lines sum to subtotal", "pass" if ok else "fail"))
    if subtotal is not None and total is not None and tax is not None:
        if fx.get("tax_inclusive"):
            ok = abs(subtotal - total) <= 0.02
            net = round(total - tax, 2)
        else:
            ok = abs(subtotal + tax - total) <= 0.02
            net = subtotal
        if not ok:
            out.append(_f("MATH_MISMATCH", "hold", S,
                          f"Subtotal {fmt_money(subtotal)} + tax {fmt_money(tax)} does not equal total {fmt_money(total)}.",
                          subtotal=subtotal, tax=tax, total=total))
        checks.append(("Subtotal + tax = total", "pass" if ok else "fail"))
    else:
        net = subtotal

    inv_date = to_date(fx.get("invoice_date"))
    if inv_date:
        if inv_date > today + timedelta(days=1):
            out.append(_f("FUTURE_DATED", "hold", S, f"Invoice is dated in the future ({inv_date}).", invoice_date=str(inv_date)))
            checks.append(("Date not in future", "fail"))
        elif (today - inv_date).days > policy["max_invoice_age_days"]:
            out.append(_f("STALE_INVOICE", "hold", S,
                          f"Invoice is {(today - inv_date).days} days old (policy limit {policy['max_invoice_age_days']}).",
                          invoice_date=str(inv_date)))
            checks.append(("Invoice age within policy", "fail"))
        else:
            checks.append(("Invoice date plausible", "pass"))

    cur = (fx.get("currency") or "USD").upper()
    if cur != policy["currency"]:
        out.append(_f("CURRENCY_MISMATCH", "hold", S, f"Invoice currency {cur} differs from company currency {policy['currency']}."))
    checks.append(("Currency " + cur, "pass" if cur == policy["currency"] else "fail"))

    summary = {"checks": checks, "line_sum": line_sum, "subtotal": subtotal, "tax": tax, "total": total,
               "net_amount": net, "derived": derived}
    return summary, out


# ------------------------------------------------------------- 5. vendor check
def verify_vendor(fx: dict) -> tuple[dict, list[Finding]]:
    S = "vendor"
    out: list[Finding] = []
    inv_domain = email_domain(fx.get("vendor_email"))
    scored = []
    for v in reference.vendors():
        name_s = max([name_similarity(fx.get("vendor_name") or "", v["name"])] +
                     [name_similarity(fx.get("vendor_name") or "", a) for a in v["aliases"]])
        signals = {"name": name_s}
        score = name_s
        if fx.get("vendor_tax_id") and fx["vendor_tax_id"] == v["tax_id"]:
            signals["tax_id"] = 1.0
            score = max(score, 0.99)
        if fx.get("remit_account") and fx["remit_account"] == v["bank_account"]:
            signals["bank_account"] = 1.0
            score = max(score, 0.9)
        if inv_domain and inv_domain == email_domain(v["contact_email"]):
            signals["email_domain"] = 1.0
            score = min(1.0, score + 0.05)
        scored.append((score, v, signals))
    scored.sort(key=lambda t: -t[0])
    best_score, v, signals = scored[0]

    if best_score < 0.75:
        out.append(_f("UNKNOWN_VENDOR", "reject", S,
                      f"'{fx.get('vendor_name')}' is not in the approved vendor master. Vendors must be onboarded before they can be paid.",
                      "vendor", closest=v["name"], score=best_score))
        return {"matched": False, "best_guess": v["name"], "score": best_score}, out

    checks = [(f"Matched to {v['vendor_id']} {v['name']}", "pass")]
    if v["status"] != "approved":
        out.append(_f("VENDOR_BLOCKED", "reject", S, f"{v['name']} is on payment block in the vendor master (status: {v['status']}).",
                      "vendor", vendor_id=v["vendor_id"]))
        checks.append(("Vendor status approved", "fail"))
    else:
        checks.append(("Vendor status approved", "pass"))

    if fx.get("vendor_tax_id") and fx["vendor_tax_id"] != v["tax_id"]:
        out.append(_f("TAX_ID_MISMATCH", "hold", S,
                      f"Tax ID on invoice ({fx['vendor_tax_id']}) differs from vendor master ({v['tax_id']}).", "vendor"))
        checks.append(("Tax ID matches master", "fail"))
    elif fx.get("vendor_tax_id"):
        checks.append(("Tax ID matches master", "pass"))

    bank_changed = False
    acct = fx.get("remit_account")
    if acct and acct != v["bank_account"]:
        bank_changed = True
        out.append(_f("BANK_DETAILS_CHANGED", "hold", S,
                      f"Invoice asks for payment to account ••••{acct[-4:]} at {fx.get('remit_bank_name') or 'an unknown bank'}, "
                      f"but the vendor master has ••••{v['bank_account'][-4:]} at {v['bank_name']}. "
                      "Bank changes are never accepted from an invoice; verify by phone using the number on file.",
                      "security", invoice_account=acct, invoice_routing=fx.get("remit_routing"),
                      invoice_bank=fx.get("remit_bank_name"), master_account_last4=v["bank_account"][-4:],
                      master_bank=v["bank_name"], phone_on_file=v["phone"],
                      notices=[n for n in fx.get("notices", []) if "bank" in n.lower() or "account" in n.lower()]))
        checks.append(("Remit-to bank matches master", "fail"))
    elif acct:
        checks.append(("Remit-to bank matches master", "pass"))
    else:
        checks.append(("Remit-to bank on invoice", "skip"))

    master_domain = email_domain(v["contact_email"])
    if inv_domain and inv_domain != master_domain:
        sev = "hold" if bank_changed else "warn"
        out.append(_f("CONTACT_DOMAIN_MISMATCH", sev, S,
                      f"Invoice contact domain '{inv_domain}' does not match the vendor's domain on file '{master_domain}'"
                      + (" (lookalike domain combined with a bank change is a common payment-fraud pattern)." if bank_changed else "."),
                      "security", invoice_domain=inv_domain, master_domain=master_domain))
        checks.append(("Email domain matches master", "fail" if bank_changed else "warn"))
    elif inv_domain:
        checks.append(("Email domain matches master", "pass"))

    summary = {"matched": True, "vendor_id": v["vendor_id"], "vendor_name": v["name"], "score": round(best_score, 3),
               "signals": signals, "status": v["status"], "payment_terms": v["payment_terms"], "phone": v["phone"],
               "contact_email": v["contact_email"], "checks": checks}
    return summary, out


# ----------------------------------------------------------- 6. duplicate check
def check_duplicates(fx: dict, vendor: dict, policy: dict, run_id: str) -> tuple[dict, list[Finding]]:
    S = "duplicate"
    out: list[Finding] = []
    if not vendor.get("matched"):
        return {"skipped": "vendor not identified"}, out
    key = invoice_key(fx.get("invoice_number"))
    rows = [r for r in db.ledger_rows(vendor_id=vendor["vendor_id"]) if r["source"] != run_id]
    exact = [r for r in rows if key and r["invoice_key"] == key]
    checks = [(f"Normalised key '{key}' vs {len(rows)} prior invoices from this vendor", "fail" if exact else "pass")]
    if exact:
        r = exact[0]
        same_text = r["invoice_number"] == fx.get("invoice_number")
        out.append(_f("DUPLICATE_INVOICE", "reject", S,
                      f"Invoice {fx.get('invoice_number')} is a duplicate of {r['invoice_number']} "
                      f"({fmt_money(r['total'])}, dated {r['invoice_date']}, status: {r['status']})"
                      + ("" if same_text else " - the number was reformatted on the re-send") + ".",
                      "duplicate", original=r["invoice_number"], original_status=r["status"], original_date=r["invoice_date"],
                      original_total=r["total"], original_source=r["source"], normalised_key=key, reformatted=not same_text))
    else:
        d = to_date(fx.get("invoice_date"))
        fuzzy = []
        for r in rows:
            rd = to_date(r["invoice_date"])
            if (fx.get("total") is not None and r["total"] is not None and abs(r["total"] - fx["total"]) < 0.01 and d and rd
                    and abs((d - rd).days) <= policy["duplicate_fuzzy_window_days"]
                    and (not fx.get("po_number") or not r["po_number"] or r["po_number"] == fx.get("po_number"))):
                fuzzy.append(r)
        checks.append(("Same amount + date window + PO", "warn" if fuzzy else "pass"))
        if fuzzy:
            r = fuzzy[0]
            out.append(_f("POSSIBLE_DUPLICATE", "hold", S,
                          f"Same vendor, same amount ({fmt_money(r['total'])}) and a date within {policy['duplicate_fuzzy_window_days']} days "
                          f"as invoice {r['invoice_number']} ({r['status']}). Different number, so a person should confirm.",
                          "duplicate", original=r["invoice_number"], original_status=r["status"]))
    reminders = [n for n in fx.get("notices", []) if any(w in n.lower() for w in ("reminder", "past due", "unpaid", "overdue"))]
    if reminders:
        out.append(_f("REMINDER_NOTICE", "info", S, "Document is marked as a payment reminder / past-due notice.",
                      "duplicate", notices=reminders))
    return {"normalised_key": key, "compared_against": len(rows), "checks": checks}, out


# --------------------------------------------------------------- 7. PO matching
def _line_match(inv_lines: list[dict], po: dict) -> tuple[list[dict], float]:
    """Greedy best match of invoice lines to PO lines. Returns (matches, score 0-1)."""
    used = set()
    matches = []
    scores = []
    for li in inv_lines:
        best, best_s = None, 0.0
        for pl in po["lines"]:
            if pl["line"] in used:
                continue
            if li.get("sku") and li["sku"] == pl["sku"]:
                s = 1.0
            else:
                s = text_similarity(li.get("description", ""), pl["description"])
                if li.get("unit_price") and abs(li["unit_price"] - pl["unit_price"]) <= 0.02 * pl["unit_price"]:
                    s = min(1.0, s + 0.25)
            if s > best_s:
                best, best_s = pl, s
        if best is not None and best_s >= 0.55:
            used.add(best["line"])
            matches.append({"invoice_line": li, "po_line": best, "score": round(best_s, 3)})
            scores.append(best_s)
        else:
            matches.append({"invoice_line": li, "po_line": None, "score": round(best_s, 3)})
            scores.append(0.0)
    unmatched_po = [pl for pl in po["lines"] if pl["line"] not in used]
    # score penalises both unmatched invoice lines and PO lines not billed
    denom = len(inv_lines) + 0.5 * len(unmatched_po)
    return matches, (sum(scores) / denom if denom else 0.0)


def match_po(fx: dict, vendor: dict, val: dict, policy: dict, run_id: str) -> tuple[dict, list[Finding]]:
    S = "po_match"
    out: list[Finding] = []
    if not vendor.get("matched"):
        return {"skipped": "vendor not identified"}, out
    pos = reference.purchase_orders()
    inv_lines = fx.get("line_items") or []
    po = None
    how = "explicit"
    candidates = []

    ref = (fx.get("po_number") or "").upper().replace(" ", "")
    if ref:
        digits = "".join(ch for ch in ref if ch.isdigit())
        po = pos.get("PO-" + digits)
        if po is None:
            out.append(_f("PO_NOT_FOUND", "hold", S, f"PO {fx['po_number']} referenced on the invoice does not exist.", "po"))
        elif po["vendor_id"] != vendor["vendor_id"]:
            out.append(_f("PO_VENDOR_MISMATCH", "hold", S,
                          f"PO {po['po_number']} belongs to a different vendor ({po['vendor_id']}), not {vendor['vendor_name']}.", "po"))
            po = None
    if po is None:
        how = "inferred"
        open_pos = [p for p in pos.values() if p["vendor_id"] == vendor["vendor_id"] and p["status"] == "open"]
        for p in open_pos:
            _, s = _line_match(inv_lines, p)
            net = val.get("net_amount")
            if net:
                s = 0.8 * s + 0.2 * max(0.0, 1 - abs(p["amount"] - net) / p["amount"])
            candidates.append({"po_number": p["po_number"], "description": p["description"], "amount": p["amount"],
                               "score": round(s, 3)})
        candidates.sort(key=lambda c: -c["score"])
        top = candidates[0] if candidates else None
        runner_up = candidates[1]["score"] if len(candidates) > 1 else 0.0
        if top and top["score"] >= policy["inferred_po_min_score"] and top["score"] - runner_up >= 0.25:
            po = pos[top["po_number"]]
            if not ref:
                out.append(_f("PO_INFERRED", "info", S,
                              f"No PO number on the invoice. Matched to {po['po_number']} by line items "
                              f"(score {top['score']:.2f} vs next best {runner_up:.2f} across {len(candidates)} open POs).",
                              "po", candidates=candidates))
        else:
            if not ref:
                out.append(_f("PO_MISSING", "hold", S,
                              "No PO number on the invoice and no open PO is a confident, unique match. A buyer must assign the PO.",
                              "po", candidates=candidates))
            return {"po_number": None, "how": how, "candidates": candidates}, out

    if po["status"] != "open":
        out.append(_f("PO_CLOSED", "hold", S, f"PO {po['po_number']} is {po['status']}; no further invoices should be billed to it.", "po"))

    matches, mscore = _line_match(inv_lines, po)
    tol_line = policy["line_price_tolerance_pct"]
    line_rows = []
    prior_runs_qty = _prior_billed_qty(po["po_number"], run_id)
    for m in matches:
        li, pl = m["invoice_line"], m["po_line"]
        row = {"description": li.get("description"), "qty": li.get("qty"), "unit_price": li.get("unit_price"),
               "amount": li.get("amount"), "match_score": m["score"]}
        if pl is None:
            row["status"] = "not on PO"
            line_rows.append(row)
            continue
        row.update(po_line=pl["line"], po_description=pl["description"], po_qty=pl["qty"], po_unit_price=pl["unit_price"])
        status = "ok"
        lump_sum = pl["qty"] == 1 and (li.get("qty") or 1) == 1 and (li.get("amount") or 0) <= pl["amount"] + 0.01
        if lump_sum:
            status = "milestone / partial"  # fixed-fee line billed in parts - judged at header level below
        elif li.get("unit_price") is not None:
            var = _pct(li["unit_price"] - pl["unit_price"], pl["unit_price"])
            row["price_var_pct"] = var
            if abs(var) > tol_line:
                status = "price variance"
                out.append(_f("PRICE_VARIANCE", "hold", S,
                              f"'{pl['description']}' billed at {fmt_money(li['unit_price'])} vs PO price {fmt_money(pl['unit_price'])} "
                              f"({var:+.1f}%, tolerance ±{tol_line}%).", "po", po_line=pl["line"]))
        qty = li.get("qty") or 0
        billed_before = prior_runs_qty.get(pl["line"], 0.0)
        if po["lines"] and len(po["lines"]) > 0 and pl["qty"] > 1 and billed_before + qty > pl["qty"] + 1e-6:
            status = "qty over PO"
            out.append(_f("QTY_EXCEEDS_PO", "hold", S,
                          f"'{pl['description']}': {billed_before + qty:g} billed in total vs {pl['qty']:g} ordered.", "po", po_line=pl["line"]))
        if po["match_type"] == "3-way":
            rec = reference.received_qty(po["po_number"], pl["line"])
            row["qty_received"] = rec
            if billed_before + qty > rec + 1e-6:
                status = "not received"
                out.append(_f("QTY_NOT_RECEIVED", "hold", S,
                              f"'{pl['description']}': billing for {billed_before + qty:g} but only {rec:g} received (3-way match).",
                              "po", po_line=pl["line"]))
        row["status"] = status
        line_rows.append(row)

    # header / cumulative check - covers split invoicing on one PO
    net = val.get("net_amount") or 0.0
    prior = [r for r in db.ledger_rows(po_number=po["po_number"]) if r["source"] != run_id and r["status"] in ("paid", "approved")]
    prior_net = round(sum(r["subtotal"] or 0 for r in prior), 2)
    remaining = round(po["amount"] - prior_net, 2)
    over = round(net - remaining, 2)
    tol_abs, tol_pct = policy["header_tolerance_abs"], policy["header_tolerance_pct"]
    within = over <= min(tol_abs, po["amount"] * tol_pct / 100)
    header = {"po_amount": po["amount"], "previously_billed": prior_net, "prior_invoices": [r["invoice_number"] for r in prior],
              "this_invoice_net": net, "remaining_before": remaining, "remaining_after": round(remaining - net, 2),
              "cumulative_pct": _pct(prior_net + net, po["amount"]), "variance": over}
    if over > 0.01:
        if within:
            out.append(_f("PRICE_VARIANCE_WITHIN_TOLERANCE", "info", S,
                          f"Invoice exceeds the open PO balance by {fmt_money(over)} ({_pct(over, po['amount']):.1f}%), "
                          f"within policy (≤{tol_pct}% and ≤{fmt_money(tol_abs)}). Accepted.",
                          "po", variance=over, unplanned=[r["description"] for r in line_rows if r.get("status") == "not on PO"]))
        elif prior and remaining <= 0.01:
            out.append(_f("PO_OVERBILLED", "hold", S,
                          f"{po['po_number']} is already fully billed ({fmt_money(prior_net)} by {', '.join(header['prior_invoices'])}). "
                          f"Paying this invoice would bring billing to {header['cumulative_pct']:.0f}% of the PO.",
                          "po", **header))
        elif prior:
            out.append(_f("PO_OVERBILLED", "hold", S,
                          f"Split billing on {po['po_number']}: {len(prior)} earlier invoice(s) ({', '.join(header['prior_invoices'])}) "
                          f"already billed {fmt_money(prior_net)} of {fmt_money(po['amount'])}. This invoice ({fmt_money(net)}) brings the total to "
                          f"{fmt_money(prior_net + net)} ({header['cumulative_pct']:.0f}% of PO), {fmt_money(over)} over. "
                          f"Only {fmt_money(remaining)} remains on the PO.",
                          "po", **header))
        else:
            out.append(_f("PO_AMOUNT_EXCEEDED", "hold", S,
                          f"Invoice net {fmt_money(net)} exceeds PO value {fmt_money(po['amount'])} by {fmt_money(over)} "
                          f"({_pct(over, po['amount']):.1f}%), outside tolerance (≤{tol_pct}% and ≤{fmt_money(tol_abs)}).", "po", **header))
    if prior and over <= 0.01:
        out.append(_f("PARTIAL_BILLING", "info", S,
                      f"Split billing recognised: {len(prior)} earlier invoice(s) on this PO; {fmt_money(remaining - net)} will remain after this one.",
                      "po", **header))

    # tax check against the PO's expected rate
    tax = val.get("tax") or 0.0
    expected_tax = round(net * po["tax_rate"], 2)
    tax_ok = abs(tax - expected_tax) <= max(0.05, net * policy["tax_rate_tolerance_pp"] / 100)
    if not tax_ok:
        out.append(_f("TAX_MISMATCH", "hold", S,
                      f"Tax billed {fmt_money(tax)} vs expected {fmt_money(expected_tax)} at the PO rate of {po['tax_rate']:.2%}.",
                      "po", expected=expected_tax, billed=tax))

    summary = {"po_number": po["po_number"], "how": how, "candidates": candidates, "match_type": po["match_type"],
               "po_description": po["description"], "owner": po["owner"], "cost_center": po["cost_center"],
               "line_match_score": round(mscore, 3), "lines": line_rows, "header": header,
               "tax": {"billed": tax, "expected": expected_tax, "rate": po["tax_rate"], "ok": tax_ok}}
    return summary, out


def _prior_billed_qty(po_number: str, run_id: str) -> dict[int, float]:
    qty: dict[int, float] = {}
    for r in db.ledger_rows(po_number=po_number):
        if r["source"] == run_id or r["status"] not in ("paid", "approved"):
            continue
        for l in r["lines"]:
            if l.get("po_line"):
                qty[l["po_line"]] = qty.get(l["po_line"], 0.0) + (l.get("qty") or 0)
    return qty
