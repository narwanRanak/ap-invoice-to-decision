# 5-minute demo video script

**Goal (from the brief):** show the happy path running live, run at least one edge case, and narrate what the process is doing and why. Five minutes max. No slides, no editing.

**Plan:** 1 happy path + 3 edge cases + dashboard. Each run takes about 4 seconds, so the clock goes on narration, not waiting.

---

## Before you hit record (2 minutes)

- [ ] Open **https://ranak363-invoice-to-decision.hf.space** a minute early. Free hosting sleeps when idle, and the first load wakes it.
- [ ] Click **Reset demo data** (bottom of the sidebar), so the inbox starts at 0.
- [ ] Use a browser window about 1440 px wide at 100% zoom. Close other tabs and turn on Do Not Disturb.
- [ ] Do a full rehearsal once, then **Reset demo data** again before the real take.
- [ ] Record with Loom (or QuickTime → New Screen Recording) with your microphone on.

> Skip the **scanned** scenario in the video: on the free server it takes ~15 s. Mention it in one line instead (it's in the script below).

---

## The script

The "Say" lines are about 600 words: roughly 4¼ minutes at a relaxed pace, plus ~30 s of clicking. Don't read them word for word; hit the points in your own voice.

### 0:00 – 0:30 · Intro
**Screen:** the **Upload** page.

**Say:**
> "This is AP Desk, my build for PS-1: invoice processing, from PDF to decision. An AP team gets hundreds of messy vendor invoices a month. Someone has to read each one, find the PO, check the numbers and decide whether to pay. This process does that end to end and shows its work at every step.
> One design choice drives everything: **AI reads, rules decide.** The model's only job is to read the document. Approve, hold or reject is deterministic policy code, so every decision is auditable and reproducible."

### 0:30 – 1:40 · Happy path: clean invoice, 3-way match
**Do:** next to **"Clean itemised invoice, explicit PO"**, click **Run**.

**Say, while the progress bar moves:**
> "Each stage streams in live. Intake fingerprints the file. Read pulls the text layer, or runs OCR if it's a scan. Extract turns it into structured fields. Then the checks: validate the maths, verify the vendor against the vendor master, check the AP ledger for duplicates, and match to the purchase order."

**Do:** when it lands on **Ready to pay**, point at **"Why it was approved"**.

**Say:**
> "Approved and scheduled for payment on its Net 30 date. And it says *why*: vendor verified including the bank account, not a duplicate, all three lines match the PO on price and quantity, and against the goods receipt. That's a 3-way match. The tax matches the PO's rate."

**Do:** click the **Extracted fields** tab. Hover **Total**, then **Invoice number**.

**Say:**
> "Every value is located on the invoice itself. Hover a field and you see exactly where it was read. A reviewer can verify in seconds instead of re-reading the PDF."

### 1:40 – 2:55 · Edge case 4: bank details changed (the money shot)
**Do:** click **Upload** in the sidebar, then **Run** on **"Bank details changed"**. Don't use the "Next" button here; it would run the scenarios in between.

**Say, while it runs:**
> "This is the one that costs companies real money. This invoice matches the PO perfectly: right vendor, right amounts, right tax. A tired person on a Friday pays it."

**Do:** when it lands **On hold**, hover the **BANK_DETAILS_CHANGED** flag. The invoice lights up.

**Say:**
> "But it asks us to pay a *different bank account* from the one in our vendor master. And the email domain is a lookalike: halden-elec dot co instead of haldenelectrical dot com. That's the classic business-email-compromise pattern. So it's held as a security issue and routed to the AP Manager and Treasury, not a regular clerk.
> Notice there's deliberately **no email to the vendor**. Replying to contact details on a suspicious invoice could reach the fraudster. The next action is to call the vendor on the number *already on file*."

**Do:** type your name and the note *"Called vendor on the number on file: no bank change. Reported to IT Security."*, then click **Reject**.

**Say:**
> "A human makes the final call, and the note goes into the audit trail."

### 2:55 – 3:40 · Edge case 1: split PO overbilling
**Do:** **Upload** → **Run** on **"Split PO: third invoice overbills"**. When it's **On hold**, click the **PO match** tab.

**Say:**
> "On its own, this $5,400 invoice looks fine. But it's the third milestone on an $18,000 PO, and the first two already billed $14,400. The process tracks billing *across invoices*. This one takes the PO to 110%, $1,800 over. It's routed to the PO owner, and it drafts the email to the vendor asking for a revised invoice of $3,600."

### 3:40 – 4:10 · Edge case 3: disguised duplicate
**Do:** **Upload** → **Run** on **"Disguised duplicate (past-due reminder)"**.

**Say:**
> "Duplicates are a top cause of AP leakage. This 'past due' reminder is for an invoice we've already paid, but the number was reformatted: 912 instead of 0912. Invoice numbers are normalised before comparing, so it's caught and rejected, and the vendor gets a note saying it was already paid.
> The other prepared cases, a scanned invoice with no PO number and a freight surcharge inside tolerance, are on the Upload page too."

### 4:10 – 4:45 · Dashboard and audit
**Do:** click **Invoices** in the sidebar.

**Say:**
> "The inbox shows history and status across every run: what's ready to pay, what's on hold and who owns it, and why invoices were stopped. On the right is what a finance leader cares about: AP hours and cost saved, and the value of risky payments stopped, with the assumptions spelled out."

**Do:** open any run and click **Audit packet**.

**Say:**
> "And any decision exports as an audit packet: inputs, findings, the policy that was applied, and who reviewed it."

### 4:45 – 5:00 · Close
**Say:**
> "It runs live and handles real PDFs, including scans. It works with Claude for extraction, or fully offline so a demo can't break. Next I'd add email-inbox intake and an ERP connector. Thanks for watching."

---

## If something goes wrong while recording
- **A page looks stuck:** refresh. Runs are stored, so nothing is lost.
- **You run a scenario twice:** the second run is correctly rejected as a duplicate. Say so; it's a feature. Then **Reset demo data** before the next take.
- **The site is asleep or slow to start:** wait 30–60 s and refresh. Or run locally (`.venv/bin/uvicorn app.main:app --port 8000`) and record from http://localhost:8000.
