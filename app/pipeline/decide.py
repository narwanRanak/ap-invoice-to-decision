"""Stage 8 - deterministic decision policy.

decision = f(findings). No model involved. Same inputs always give the same decision,
and the explanation is built from the same findings, so the two can never disagree.
"""
from __future__ import annotations

from datetime import timedelta

from .normalize import fmt_money, to_date

ROUTING = {
    "security": "AP Manager + Treasury (payment-fraud review)",
    "duplicate": "AP Specialist",
    "vendor": "Vendor Master team",
    "po": "PO owner",
    "data": "AP Specialist",
    "policy": "Finance Controller",
}


def _terms_days(terms: str | None) -> int:
    if not terms:
        return 30
    t = terms.lower()
    if "receipt" in t:
        return 0
    digits = "".join(ch for ch in t if ch.isdigit())
    return int(digits) if digits else 30


def _short_headline(decision: str, findings: list[dict], po: dict, primary_code: str | None) -> str | None:
    """One scannable sentence for the primary reason (the one that decided routing); detail stays in the findings."""
    blocking = [f for f in findings if f["severity"] in ("reject", "hold") and f["code"] == primary_code]
    codes = {f["code"]: f["evidence"] for f in blocking}
    if decision == "REJECTED" and "DUPLICATE_INVOICE" in codes:
        e = codes["DUPLICATE_INVOICE"]
        how = "re-sent with a reformatted number" if e.get("reformatted") else "submitted again"
        return f"Duplicate - this is invoice {e['original']} again ({e['original_status']}, {fmt_money(e['original_total'])}), {how}."
    if "BANK_DETAILS_CHANGED" in codes:
        e = codes["BANK_DETAILS_CHANGED"]
        return (f"Payment blocked - invoice asks to pay a new bank account (••••{e['invoice_account'][-4:]} at {e['invoice_bank']}) "
                f"instead of the one on file (••••{e['master_account_last4']}).")
    if "PO_OVERBILLED" in codes:
        e = codes["PO_OVERBILLED"]
        return (f"Over PO - with earlier invoices {', '.join(e['prior_invoices'])}, this would bill {e['cumulative_pct']:.0f}% of "
                f"{po.get('po_number')}: {fmt_money(e['variance'])} more than the PO allows.")
    if "NOT_AN_INVOICE" in codes:
        return "This document doesn't look like an invoice: no invoice number, date or total could be found. Sent to AP for a person to check."
    if decision == "REJECTED" and "UNKNOWN_VENDOR" in codes:
        return "Unknown vendor - not in the vendor master, so it cannot be paid."
    return None


def decide(fx: dict, val: dict, vendor: dict, dup: dict, po: dict, findings: list[dict], policy: dict) -> dict:
    total = val.get("total") or fx.get("total") or 0.0
    rejects = [f for f in findings if f["severity"] == "reject"]
    holds = [f for f in findings if f["severity"] == "hold"]

    if not rejects and not holds and total > policy["auto_approve_limit"]:
        f = {"code": "APPROVAL_LIMIT", "severity": "hold", "stage": "decide", "category": "policy", "evidence": {},
             "message": f"Invoice total {fmt_money(total)} is above the auto-approval limit of {fmt_money(policy['auto_approve_limit'])}."}
        findings.append(f)
        holds.append(f)

    vendor_name = vendor.get("vendor_name") or fx.get("vendor_name") or "the vendor"
    inv = fx.get("invoice_number") or "(no number)"
    po_no = po.get("po_number")
    owner = po.get("owner")
    actions: list[str] = []
    vendor_msg = None

    if rejects:
        decision = "REJECTED"
        primary = rejects[0]
        routed = ROUTING.get(primary["category"], "AP Specialist")
        headline = primary["message"]
        codes = {f["code"] for f in rejects}
        if "DUPLICATE_INVOICE" in codes:
            ev = next(f for f in rejects if f["code"] == "DUPLICATE_INVOICE")["evidence"]
            actions += ["Do not pay. Close this document as a duplicate.",
                        f"Reply to the vendor confirming invoice {ev['original']} status ({ev['original_status']})."]
            vendor_msg = (f"Subject: Re: Invoice {inv}\n\nHello {vendor_name} team,\n\n"
                          f"We received invoice {inv} for {fmt_money(total)}. This matches invoice {ev['original']} dated "
                          f"{ev['original_date']}, which is already {ev['original_status']} in our system"
                          + (". Payment was issued per your terms" if ev['original_status'] == 'paid' else "")
                          + ", so we will not process it again. If you believe a balance is still outstanding, please send a "
                          "statement of account and we will reconcile it.\n\nThanks,\nNorthwind Manufacturing - Accounts Payable")
        elif codes & {"UNKNOWN_VENDOR", "VENDOR_BLOCKED"}:
            actions += ["Do not pay.", "Route to the Vendor Master team to onboard / reinstate the vendor if the purchase was legitimate."]
            vendor_msg = (f"Subject: Invoice {inv} - vendor registration required\n\nHello,\n\nWe cannot process invoice {inv} because "
                          f"{'your company is not yet registered' if 'UNKNOWN_VENDOR' in codes else 'your vendor account is currently inactive'} "
                          "in our supplier system. Please complete our supplier onboarding (W-9, banking letter on company letterhead, "
                          "and certificate of insurance) and re-submit the invoice quoting a valid PO number.\n\n"
                          "Thanks,\nNorthwind Manufacturing - Accounts Payable")
    elif holds:
        decision = "NEEDS_REVIEW"
        cats = [f["category"] for f in holds]
        primary_cat = "security" if "security" in cats else cats[0]
        routed = ROUTING[primary_cat] + (f" ({owner})" if primary_cat == "po" and owner else "")
        primary = next((f for f in holds if f["code"] == "NOT_AN_INVOICE"), None) or next(f for f in holds if f["category"] == primary_cat)
        headline = primary["message"]
        codes = {f["code"] for f in holds}
        if "BANK_DETAILS_CHANGED" in codes:
            ev = next(f for f in holds if f["code"] == "BANK_DETAILS_CHANGED")["evidence"]
            actions += ["Payment BLOCKED. Do not update bank details from the invoice.",
                        f"Call {vendor_name} on the phone number held in the vendor master ({ev['phone_on_file']}), not any number or email on the invoice, to verify the change.",
                        "If the change is confirmed, have the vendor submit a signed bank letter through the vendor-master change process (dual approval).",
                        "If not confirmed, report to IT Security as an attempted business-email-compromise fraud."]
            # deliberately NO vendor-facing reply: replying to the invoice's contact could tip off a fraudster
        if "PO_OVERBILLED" in codes and next(f for f in holds if f["code"] == "PO_OVERBILLED")["evidence"]["remaining_before"] <= 0.01:
            actions += [f"{po_no} is already fully billed. Ask {owner or 'the PO owner'} whether this is new work needing a new PO; otherwise reject."]
        elif "PO_OVERBILLED" in codes:
            ev = next(f for f in holds if f["code"] == "PO_OVERBILLED")["evidence"]
            actions += [f"Ask {owner or 'the PO owner'} whether the extra {fmt_money(ev['variance'])} is approved scope.",
                        f"Either raise a PO change order for {fmt_money(ev['variance'])}, or request a revised invoice for {fmt_money(ev['remaining_before'])}."]
            vendor_msg = (f"Subject: Invoice {inv} on hold - exceeds PO {po_no}\n\nHello {vendor_name} team,\n\n"
                          f"Invoice {inv} ({fmt_money(total)}) is on hold. PO {po_no} is for {fmt_money(ev['po_amount'])}, and invoices "
                          f"{', '.join(ev['prior_invoices'])} have already billed {fmt_money(ev['previously_billed'])}. The remaining balance on "
                          f"the PO is {fmt_money(ev['remaining_before'])}, so this invoice exceeds it by {fmt_money(ev['variance'])}.\n\n"
                          f"Please send either a revised invoice for {fmt_money(ev['remaining_before'])}, or a credit note for "
                          f"{fmt_money(ev['variance'])}. If the additional amount reflects agreed extra scope, let us know and we will "
                          "arrange a PO amendment with the requester.\n\nThanks,\nNorthwind Manufacturing - Accounts Payable")
        if "PO_MISSING" in codes:
            actions.append("Ask the buyer to assign the correct PO from the candidate list, then re-run matching.")
        if codes & {"PRICE_VARIANCE", "PO_AMOUNT_EXCEEDED", "TAX_MISMATCH", "QTY_EXCEEDS_PO"} and "PO_OVERBILLED" not in codes:
            actions.append(f"PO owner {owner or ''} to approve the variance or request a corrected invoice from the vendor.".replace("  ", " "))
        if "QTY_NOT_RECEIVED" in codes:
            actions.append("Hold until the warehouse posts the goods receipt; re-run matching once received.")
        if "NOT_AN_INVOICE" in codes:
            actions.append("AP specialist to check what this document is (statement, quote, letter) and file or forward it.")
        elif codes & {"MISSING_FIELD", "LOW_CONFIDENCE_FIELD", "MATH_MISMATCH"}:
            actions.append("AP specialist to confirm the flagged fields against the document and correct them.")
        if "POSSIBLE_DUPLICATE" in codes:
            actions.append("AP specialist to compare with the earlier invoice before releasing.")
        if "APPROVAL_LIMIT" in codes:
            actions.append("Finance Controller approval required before payment.")
        if not actions:
            actions.append("Review the flagged items and approve or reject.")
    else:
        decision = "APPROVED"
        routed = "Payment run"
        terms = fx.get("payment_terms") or vendor.get("payment_terms")
        inv_date = to_date(fx.get("invoice_date"))
        pay_date = (inv_date + timedelta(days=_terms_days(terms))).isoformat() if inv_date else None
        infos = [f for f in findings if f["severity"] in ("info", "warn")]
        headline = (f"Matched to {po_no} ({po.get('match_type')}) - {fmt_money(total)} approved for payment"
                    + (f" on {pay_date} ({terms})." if pay_date else "."))
        actions.append(f"Post to GL cost centre {po.get('cost_center')} and schedule payment" + (f" for {pay_date}." if pay_date else "."))
        if any(f["code"] == "PO_INFERRED" for f in infos):
            actions.append("PO was inferred - vendor reminded to quote PO numbers on future invoices.")
            vendor_msg = (f"Subject: Invoice {inv} received - please quote PO numbers\n\nHello {vendor_name} team,\n\n"
                          f"Thank you for invoice {inv} ({fmt_money(total)}). It has been approved and matched to PO {po_no}, "
                          f"and is scheduled for payment on {pay_date}.\n\nYour invoice did not show a PO number, which slows down "
                          f"processing. Please include the PO number on all future invoices.\n\n"
                          "Thanks,\nNorthwind Manufacturing - Accounts Payable")
        if any(f["code"] == "PRICE_VARIANCE_WITHIN_TOLERANCE" for f in infos):
            actions.append("Variance within tolerance recorded for spend reporting; no action needed.")

    primary_code = primary["code"] if decision != "APPROVED" else None
    headline = _short_headline(decision, findings, po, primary_code) or headline

    # controls that passed - lets a non-technical reader see *why* something was trusted
    controls = []
    if vendor.get("matched") and not any(f["category"] in ("vendor", "security") and f["severity"] in ("hold", "reject") for f in findings):
        sig = ", ".join(k.replace("_", " ") for k in vendor.get("signals", {}))
        controls.append(f"Vendor verified against master data ({sig}); remit-to bank matches the account on file.")
    if dup.get("compared_against") is not None and not any(f["category"] == "duplicate" and f["severity"] in ("hold", "reject") for f in findings):
        n = dup["compared_against"]
        controls.append("Not a duplicate: " + (f"checked against {n} earlier invoice(s) from this vendor, including reformatted numbers."
                                                if n else "first invoice on file from this vendor."))
    if po.get("po_number") and po.get("lines") is not None:
        ok_lines = sum(1 for l in po["lines"] if l.get("status") in ("ok", "milestone / partial"))
        recv = " and goods receipt" if po.get("match_type") == "3-way" else ""
        controls.append(f"{ok_lines}/{len(po['lines'])} invoice lines match {po['po_number']} on price and quantity{recv}.")
        if po.get("tax", {}).get("ok"):
            controls.append(f"Tax {fmt_money(po['tax']['billed'])} matches the PO's expected rate ({po['tax']['rate']:.2%}).")
    if decision != "APPROVED":
        controls = []

    return {
        "decision": decision,
        "controls_passed": controls,
        "headline": headline,
        "routed_to": routed,
        "next_actions": actions,
        "vendor_message": vendor_msg,
        "reasons": [f for f in findings if f["severity"] in ("reject", "hold")],
        "notes": [f for f in findings if f["severity"] in ("info", "warn")],
    }
