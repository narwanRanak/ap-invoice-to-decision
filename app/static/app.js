/* Invoice -> Decision : single-page UI (vanilla JS, no build step). */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x === null || x === undefined || Number.isNaN(x) ? "—" : (x < 0 ? "−$" : "$") + Math.abs(Number(x)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pct = (x) => (x === null || x === undefined ? "—" : Math.round(x * 100) + "%");
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
};
const timeAgo = (iso) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return new Date(iso).toLocaleDateString();
};
function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

const ICON = {
  check: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 10.5l3.2 3.2L15 7"/></svg>',
  x: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M6 6l8 8M14 6l-8 8"/></svg>',
  bang: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M10 5v6M10 14.5v.5"/></svg>',
  dash: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 10h8"/></svg>',
  dot: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="3" fill="currentColor"/></svg>',
  chev: '<svg class="s-chev" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 5l5 5-5 5"/></svg>',
  approved: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  review: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
  rejected: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M7 7l10 10M17 7L7 17"/></svg>',
};
const STAGE_ICON = { pending: ICON.dot, running: "", pass: ICON.check, warn: ICON.bang, hold: ICON.bang, reject: ICON.x, error: ICON.x, skip: ICON.dash };
const DECISION_LABEL = { APPROVED: "Approved", NEEDS_REVIEW: "Needs review", REJECTED: "Rejected" };
const DECISION_CHIP = { APPROVED: "chip-green", NEEDS_REVIEW: "chip-amber", REJECTED: "chip-red" };
const decisionChip = (d) => (d ? `<span class="chip ${DECISION_CHIP[d] || "chip-gray"}"><span class="dot"></span>${DECISION_LABEL[d] || d}</span>` : '<span class="chip chip-gray">Running…</span>');

const state = { health: null, samples: [], es: null, timer: null, poll: null, openStages: new Set(), rightTab: "doc" };

/* ------------------------------------------------------------------ router */
async function router() {
  if (state.es) { state.es.close(); state.es = null; }
  clearInterval(state.timer);
  clearInterval(state.poll);
  const [, route, arg] = (location.hash || "#/process").split("/");
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (route === "run" ? "process" : route)));
  window.scrollTo(0, 0);
  try {
    if (route === "run" && arg) return await renderRun(arg);
    if (route === "dashboard") return await renderDashboard();
    if (route === "reference") return await renderReference();
    if (route === "how") return renderHow();
    return renderProcess();
  } catch (e) {
    view.innerHTML = `<div class="card empty"><b>Something went wrong</b>${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", router);

async function refreshReviewCount() {
  try {
    const s = await api("/api/stats");
    const el = $("#nav-review-count");
    el.textContent = s.pending_review;
    el.classList.toggle("hidden", !s.pending_review);
  } catch (_) {}
}

/* ----------------------------------------------------------- process page */
function renderProcess() {
  const cards = state.samples.map((s) => {
    const happy = s.kind.startsWith("Happy");
    return `<div class="scen">
      <div class="kind ${happy ? "happy" : "edge"}">${esc(s.kind)}</div>
      <div class="title">${esc(s.title)}</div>
      <div class="blurb">${esc(s.blurb)}</div>
      <div class="foot">
        <button class="btn btn-sm btn-primary" data-run="${esc(s.id)}">▶ Run</button>
        <a class="btn btn-sm btn-ghost" href="/api/samples/${esc(s.id)}/file" target="_blank">View PDF</a>
        <span class="grow"></span>
        <span class="small faint" title="Outcome the scenario was designed to produce">expects ${decisionChip(s.expected)}</span>
      </div>
    </div>`;
  }).join("");
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Process an invoice</h1>
      <p>Drop in a vendor invoice PDF. Every stage runs live: read, extract, validate, verify, de-duplicate, match, decide.</p></div>
    </div>
    <div class="intake">
      <label class="drop" id="drop">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M12 18v-6m0 0l-2.5 2.5M12 12l2.5 2.5"/></svg>
        <b>Upload invoice PDF</b>
        <p>Drag &amp; drop, or click to browse.<br>Text or scanned PDFs, up to 15 MB.</p>
      </label>
      <div>
        <div class="scen-head">
          <h3>Prepared scenarios <span class="muted small" style="font-weight:400">· 2 happy paths and 4 edge cases</span></h3>
          <button class="btn btn-sm" id="run-all">Run all six in order</button>
        </div>
        <div class="scenarios">${cards}</div>
      </div>
    </div>
    <div class="card" id="recent"></div>`;
  const drop = $("#drop");
  drop.addEventListener("click", (e) => { e.preventDefault(); $("#file-input").click(); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) uploadAndRun(f); });
  $$("[data-run]").forEach((b) => b.addEventListener("click", () => startSample(b.dataset.run)));
  $("#run-all").addEventListener("click", runAll);
  renderRecent();
}

async function renderRecent() {
  const runs = (await api("/api/runs")).slice(0, 5);
  const el = $("#recent");
  if (!el) return;
  el.innerHTML = `<div class="card-h"><h3>Recent runs</h3><a href="#/dashboard" class="small">Open dashboard →</a></div>` +
    (runs.length ? runsTable(runs, true) : `<div class="empty"><b>No runs yet</b>Run a scenario above or upload an invoice to get started.</div>`);
  bindRunRows(el);
}

$("#file-input").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) uploadAndRun(f); e.target.value = ""; });

async function uploadAndRun(file) {
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return toast("Please choose a PDF file.");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const { run_id } = await api("/api/runs", { method: "POST", body: fd });
    location.hash = `#/run/${run_id}`;
  } catch (e) { toast("Upload failed: " + e.message, 4000); }
}

async function startSample(id) {
  const fd = new FormData();
  fd.append("sample_id", id);
  const { run_id } = await api("/api/runs", { method: "POST", body: fd });
  location.hash = `#/run/${run_id}`;
}

async function runAll() {
  const btn = $("#run-all");
  btn.disabled = true;
  for (const [i, s] of state.samples.entries()) {
    btn.textContent = `Running ${i + 1}/${state.samples.length}…`;
    const fd = new FormData();
    fd.append("sample_id", s.id);
    const { run_id } = await api("/api/runs", { method: "POST", body: fd });
    for (;;) {
      await new Promise((r) => setTimeout(r, 400));
      const r = await api(`/api/runs/${run_id}`);
      if (r.status !== "running") break;
    }
  }
  toast("All six scenarios processed");
  location.hash = "#/dashboard";
}

/* -------------------------------------------------------------- run view */
async function renderRun(runId) {
  const [run, health] = await Promise.all([api(`/api/runs/${runId}`), state.health ? Promise.resolve(state.health) : api("/api/health")]);
  state.openStages = new Set();
  const sample = (run.source || "").startsWith("sample:") ? state.samples.find((s) => "sample:" + s.id === run.source) : null;
  const stagesHtml = health.stages.map((s) => `
    <li class="stage pending" id="st-${s.key}" data-key="${s.key}">
      <div class="stage-row">
        <div class="s-icon">${ICON.dot}</div>
        <div><div class="s-title">${esc(s.label)}</div><div class="s-summary">${esc(s.description)}</div></div>
        <div class="row"><span class="s-dur"></span>${ICON.chev}</div>
      </div>
      <div class="findings"></div>
      <div class="s-detail hidden"></div>
    </li>`).join("");
  view.innerHTML = `
    <div class="crumb"><a href="#/process">Process invoice</a> / <a href="#/dashboard">Runs</a> / ${esc(run.id)}</div>
    <div class="run-bar">
      <h2>${esc(run.id)}</h2>
      <span class="muted">${esc(run.filename)}</span>
      ${sample ? `<span class="chip ${sample.kind.startsWith("Happy") ? "chip-green" : "chip-blue"}">${esc(sample.kind)}: ${esc(sample.title)}</span>` : '<span class="chip chip-gray">Uploaded file</span>'}
      <span class="grow"></span>
      <span class="timer" id="timer"></span>
      <span id="run-status">${decisionChip(run.status === "running" ? null : run.final_status)}</span>
      <a class="btn btn-sm" href="#/process">＋ New invoice</a>
      ${nextScenarioButton(sample)}
    </div>
    <div id="decision"></div>
    <div class="run-grid">
      <div class="card">
        <div class="card-h"><h3>Pipeline</h3><span class="small muted">Click a stage for details</span></div>
        <ul class="timeline" id="timeline">${stagesHtml}</ul>
      </div>
      <div class="card" id="right">
        <div class="tabs">
          <button class="tab" data-tab="doc">Document</button>
          <button class="tab" data-tab="data">Extracted data</button>
          <button class="tab" data-tab="text">Raw text</button>
        </div>
        <div id="tab-body"></div>
      </div>
    </div>`;
  $$("#timeline .stage").forEach((li) => $(".stage-row", li).addEventListener("click", () => toggleStage(li.dataset.key)));
  $$(".tab").forEach((t) => t.addEventListener("click", () => { state.rightTab = t.dataset.tab; renderRight(run); }));
  const nb = $("#next-scen");
  if (nb) nb.addEventListener("click", () => startSample(nb.dataset.id));
  state.rightTab = "doc";
  renderRight(run);

  if (run.status === "running") {
    const t0 = Date.now();
    state.timer = setInterval(() => { const el = $("#timer"); if (el) el.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s"; }, 100);
    const es = new EventSource(`/api/runs/${runId}/events`);
    state.es = es;
    es.onmessage = async (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.type === "stage_start") setStageRunning(ev.stage);
      else if (ev.type === "stage_end") setStageResult(ev);
      else if (ev.type === "done") {
        es.close();
        state.es = null;
        clearInterval(state.timer);
        const full = await api(`/api/runs/${runId}`);
        finishRun(full);
      }
    };
    es.onerror = () => { /* the browser retries automatically; server replays from storage */ };
  } else {
    (run.result?.stages || []).forEach((st) => setStageResult(st));
    finishRun(run);
  }
}

function nextScenarioButton(sample) {
  if (!sample) return "";
  const i = state.samples.findIndex((s) => s.id === sample.id);
  const nxt = state.samples[i + 1];
  return nxt ? `<button class="btn btn-sm btn-primary" id="next-scen" data-id="${esc(nxt.id)}">Next: ${esc(nxt.kind)} ▶</button>` : "";
}

function setStageRunning(key) {
  const li = $(`#st-${key}`);
  if (!li) return;
  li.className = "stage running";
  $(".s-icon", li).innerHTML = "";
  $(".s-summary", li).textContent = "Running…";
}

function setStageResult(rec) {
  const li = $(`#st-${rec.stage}`);
  if (!li) return;
  li.className = `stage done ${rec.status}` + (state.openStages.has(rec.stage) ? " open" : "");
  $(".s-icon", li).innerHTML = STAGE_ICON[rec.status] || ICON.check;
  $(".s-summary", li).textContent = rec.summary;
  $(".s-dur", li).textContent = rec.duration_ms != null ? rec.duration_ms + " ms" : "";
  li._rec = rec;
  const shown = (rec.findings || []).filter((f) => f.severity !== "info" || ["PO_INFERRED", "PRICE_VARIANCE_WITHIN_TOLERANCE", "SCANNED_DOCUMENT", "PARTIAL_BILLING", "REMINDER_NOTICE"].includes(f.code));
  $(".findings", li).innerHTML = shown.map(findingHtml).join("");
  // auto-open the stage that drove a non-approval, so the "why" is visible without clicking
  if (["hold", "reject", "error"].includes(rec.status) && rec.stage !== "decide") openStage(rec.stage);
  else if (state.openStages.has(rec.stage)) $(".s-detail", li).innerHTML = stageDetail(rec);
}

function findingHtml(f) {
  return `<div class="finding ${esc(f.severity)}"><span class="code-chip">${esc(f.code)}</span><span>${esc(f.message)}</span></div>`;
}

function toggleStage(key) {
  if (state.openStages.has(key)) {
    state.openStages.delete(key);
    const li = $(`#st-${key}`);
    li.classList.remove("open");
    $(".s-detail", li).classList.add("hidden");
  } else openStage(key);
}
function openStage(key) {
  const li = $(`#st-${key}`);
  if (!li || !li._rec) return;
  state.openStages.add(key);
  li.classList.add("open");
  const d = $(".s-detail", li);
  d.innerHTML = stageDetail(li._rec);
  d.classList.remove("hidden");
}

const checklist = (checks) => `<ul class="checklist">${(checks || []).map(([label, st]) =>
  `<li><span class="ck ${st}">${st === "pass" ? "✓" : st === "fail" ? "✕" : st === "warn" ? "!" : "–"}</span>${esc(label)}</li>`).join("")}</ul>`;
const kv = (pairs) => `<dl class="kv">${pairs.filter(([, v]) => v !== undefined).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

function stageDetail(rec) {
  const d = rec.details || {};
  switch (rec.stage) {
    case "intake":
      return kv([["File", esc(d.filename)], ["Size", d.size_kb + " KB"], ["Pages", d.pages], ["SHA-256", `<code>${esc(d.sha256)}…</code>`],
        ["Seen before", d.previously_seen ? `<a href="#/run/${esc(d.previously_seen)}">${esc(d.previously_seen)}</a>` : "No"]]);
    case "read":
      return kv([["Method", d.method === "ocr" ? "OCR (image-only scan, local RapidOCR)" : "Native PDF text layer"], ["Lines recovered", d.lines],
        ["OCR confidence", d.ocr_confidence != null ? pct(d.ocr_confidence) : "n/a"]]) + `<div class="small muted" style="margin-top:8px">Full text is in the <b>Raw text</b> tab.</div>`;
    case "extract": {
      const f = d.fields || {};
      return kv([["Engine", esc(f.engine === "rules" ? "Local layout-aware rules parser" : f.engine)],
        ["Fallback", d.engine_note ? esc(d.engine_note) : undefined],
        ["Vendor", esc(f.vendor_name)], ["Invoice #", esc(f.invoice_number)], ["Date", esc(f.invoice_date)], ["PO", esc(f.po_number) || '<span class="faint">not on invoice</span>'],
        ["Total", money(f.total)], ["Line items", (f.line_items || []).length]]) + `<div class="small muted" style="margin-top:8px">All fields with confidence scores are in the <b>Extracted data</b> tab.</div>`;
    }
    case "validate":
      return checklist(d.checks) + (d.derived?.length ? `<div class="small muted" style="margin-top:8px">Derived (not printed): ${esc(d.derived.join(", "))}</div>` : "");
    case "vendor":
      if (!d.matched) return kv([["Closest vendor", esc(d.best_guess)], ["Score", pct(d.score)]]);
      return `<div class="two-col"><div>${kv([["Vendor", `${esc(d.vendor_id)} · ${esc(d.vendor_name)}`], ["Match signals", Object.keys(d.signals || {}).map((s) => `<span class="code-chip">${esc(s)}</span>`).join(" ")],
        ["Status", esc(d.status)], ["Terms on file", esc(d.payment_terms)], ["Phone on file", esc(d.phone)]])}</div><div>${checklist(d.checks)}</div></div>`;
    case "duplicate":
      if (d.skipped) return `<span class="muted">${esc(d.skipped)}</span>`;
      return kv([["Normalised key", `<code>${esc(d.normalised_key)}</code>`]]) + `<div style="margin-top:8px">${checklist(d.checks)}</div>`;
    case "po_match":
      return poDetail(d);
    case "decide":
      return kv([["Decision", decisionChip(d.decision)], ["Routed to", esc(d.routed_to)], ["Blocking reasons", (d.reasons || []).length], ["Notes", (d.notes || []).length]]);
    default:
      return `<pre class="small">${esc(JSON.stringify(d, null, 2))}</pre>`;
  }
}

function poDetail(d) {
  if (d.skipped) return `<span class="muted">${esc(d.skipped)}</span>`;
  let html = "";
  if (d.candidates?.length) {
    html += `<div class="subhead">PO candidates (vendor's open POs)</div><table class="t compact"><tr><th>PO</th><th>Description</th><th class="right">Amount</th><th class="right">Score</th></tr>` +
      d.candidates.map((c, i) => `<tr><td>${esc(c.po_number)} ${i === 0 && d.po_number ? '<span class="chip chip-green">chosen</span>' : ""}</td><td>${esc(c.description)}</td><td class="right num">${money(c.amount)}</td><td class="right num">${c.score.toFixed(2)}</td></tr>`).join("") + `</table>`;
  }
  if (!d.po_number) return html || '<span class="muted">No PO matched.</span>';
  html += `<div style="margin:${d.candidates?.length ? "12px" : "0"} 0 10px">` + kv([["PO", `${esc(d.po_number)} · ${esc(d.po_description)}`], ["How matched", d.how === "inferred" ? "Inferred from line items" : "Explicit reference on invoice"],
    ["Match type", d.match_type === "3-way" ? "3-way (PO + invoice + goods receipt)" : "2-way (PO + invoice)"], ["PO owner", esc(d.owner)], ["Cost centre", esc(d.cost_center)]]) + `</div>`;
  html += `<div class="subhead">Line match</div><div class="table-wrap"><table class="t compact"><tr><th>Invoice line</th><th class="right">Qty</th><th class="right">Price</th><th class="right">PO price</th>${d.match_type === "3-way" ? '<th class="right">Received</th>' : ""}<th>Status</th></tr>` +
    d.lines.map((l) => {
      const st = l.status === "ok" ? "chip-green" : l.status === "milestone / partial" ? "chip-blue" : l.status === "not on PO" ? "chip-gray" : "chip-amber";
      return `<tr><td>${esc(l.description)}${l.po_line ? `<div class="small faint">→ PO line ${l.po_line}: ${esc(l.po_description)}</div>` : ""}</td>
        <td class="right num">${l.qty ?? "—"}${l.po_qty ? `<span class="faint"> / ${l.po_qty}</span>` : ""}</td>
        <td class="right num">${money(l.unit_price)}</td><td class="right num">${l.po_unit_price != null ? money(l.po_unit_price) : "—"}</td>
        ${d.match_type === "3-way" ? `<td class="right num">${l.qty_received ?? "—"}</td>` : ""}
        <td><span class="chip ${st}">${esc(l.status)}</span></td></tr>`;
    }).join("") + `</table></div>`;
  const h = d.header;
  const base = Math.max(h.po_amount, h.previously_billed + h.this_invoice_net);
  const w = (x) => Math.max(0, (100 * x) / base) + "%";
  const thisWithin = Math.min(h.this_invoice_net, Math.max(0, h.remaining_before));
  const over = Math.max(0, h.this_invoice_net - Math.max(0, h.remaining_before));
  html += `<div class="bill"><div class="subhead">PO consumption · ${money(h.po_amount)} PO</div>
    <div class="bill-bar"><span class="bill-prev" style="width:${w(h.previously_billed)}"></span><span class="bill-this" style="width:${w(thisWithin)}"></span><span class="bill-over" style="width:${w(over)}"></span></div>
    <div class="bill-legend"><span><i style="background:#98a2b3"></i>Billed before ${money(h.previously_billed)}${h.prior_invoices.length ? " (" + esc(h.prior_invoices.join(", ")) + ")" : ""}</span>
    <span><i style="background:var(--primary)"></i>This invoice ${money(h.this_invoice_net)}</span>
    ${over > 0.005 ? `<span><i style="background:var(--red)"></i>Over PO ${money(over)}</span>` : `<span>Remaining after: ${money(h.remaining_after)}</span>`}
    <span>Tax: billed ${money(d.tax.billed)} vs expected ${money(d.tax.expected)} ${d.tax.ok ? "✓" : "✕"}</span></div></div>`;
  return html;
}

function finishRun(run) {
  const st = $("#run-status");
  if (st) st.innerHTML = decisionChip(run.final_status);
  const tm = $("#timer");
  if (tm && run.duration_ms) tm.textContent = (run.duration_ms / 1000).toFixed(1) + "s total";
  renderDecision(run);
  if (state.rightTab === "doc" && run.result?.fields) state.rightTab = "data";
  renderRight(run);
  refreshReviewCount();
}

function renderDecision(run) {
  const dec = run.result?.decision;
  const el = $("#decision");
  if (!dec || !el) return;
  const d = dec.decision;
  const reviewed = run.review;
  const noted = (dec.notes || []).filter((n) => ["PO_INFERRED", "PRICE_VARIANCE_WITHIN_TOLERANCE", "SCANNED_DOCUMENT", "AMBIGUOUS_DATE", "CONTACT_DOMAIN_MISMATCH", "EXTRACTOR_FALLBACK"].includes(n.code));
  const reviewHtml = d !== "NEEDS_REVIEW" ? "" : reviewed
    ? `<div class="review-box review-done"><div class="subhead">Human review</div>${decisionChip(run.final_status)} by <b>${esc(reviewed.reviewer)}</b> · ${timeAgo(reviewed.at)}<div class="muted" style="margin-top:6px">“${esc(reviewed.note)}”</div></div>`
    : `<div class="review-box"><div class="subhead">Reviewer action</div>
        <input id="rv-name" placeholder="Your name" value="${esc(localStorage.getItem("reviewer") || "")}">
        <textarea id="rv-note" placeholder="Note for the audit trail, e.g. 'Called vendor on number on file, change confirmed'"></textarea>
        <div class="row"><button class="btn btn-green btn-sm" data-review="approve">Approve for payment</button><button class="btn btn-red btn-sm" data-review="reject">Reject</button></div></div>`;
  el.innerHTML = `<div class="decision ${d}">
    <div class="icon">${d === "APPROVED" ? ICON.approved : d === "REJECTED" ? ICON.rejected : ICON.review}</div>
    <div>
      <div class="label">${DECISION_LABEL[d]}${run.final_status !== d ? ` → ${DECISION_LABEL[run.final_status]} after review` : ""}</div>
      <div class="headline">${esc(dec.headline)}</div>
      <div class="meta"><span>Routed to <b>${esc(dec.routed_to)}</b></span><span>Invoice <b>${money(run.total)}</b></span>
        ${run.po_number ? `<span>PO <b>${esc(run.po_number)}</b></span>` : ""}<span>${(dec.reasons || []).length} blocking · ${(dec.notes || []).length} notes</span></div>
      <div class="decision-cols">
        <div><div class="subhead">Next actions</div><ol>${dec.next_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
          ${(dec.controls_passed || []).length ? `<div class="subhead" style="margin-top:12px">Why it was approved</div><ul class="checklist">${dec.controls_passed.map((c) => `<li><span class="ck pass">✓</span>${esc(c)}</li>`).join("")}</ul>` : ""}
          ${noted.length && d === "APPROVED" ? `<div class="subhead" style="margin-top:12px">Noted, but within policy</div><div class="findings" style="margin:0">${noted.map(findingHtml).join("")}</div>` : ""}
          ${(dec.reasons || []).length > 1 ? `<div class="subhead" style="margin-top:12px">All blocking reasons</div><div class="findings" style="margin:0">${dec.reasons.map(findingHtml).join("")}</div>` : ""}</div>
        <div>${dec.vendor_message ? `<div class="row" style="justify-content:space-between"><div class="subhead">Draft message to vendor</div><button class="btn btn-sm btn-ghost" id="copy-msg">Copy</button></div><div class="vendor-msg">${esc(dec.vendor_message)}</div>` :
          d === "NEEDS_REVIEW" && (dec.reasons || []).some((r) => r.category === "security") ? `<div class="subhead">Vendor message</div><div class="vendor-msg">Deliberately none. Replying to contact details on a suspicious invoice could reach the fraudster. Verification goes through the phone number on file.</div>` : ""}
          ${reviewHtml ? `<div style="margin-top:12px">${reviewHtml}</div>` : ""}</div>
      </div>
    </div></div>`;
  const cp = $("#copy-msg");
  if (cp) cp.addEventListener("click", () => { navigator.clipboard.writeText(dec.vendor_message); toast("Copied to clipboard"); });
  $$("[data-review]").forEach((b) => b.addEventListener("click", async () => {
    const reviewer = $("#rv-name").value.trim();
    const note = $("#rv-note").value.trim();
    if (!reviewer || note.length < 5) return toast("Add your name and a short note: it goes into the audit trail.");
    localStorage.setItem("reviewer", reviewer);
    try {
      await api(`/api/runs/${run.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: b.dataset.review, reviewer, note }) });
      toast(b.dataset.review === "approve" ? "Approved for payment" : "Invoice rejected");
      const fresh = await api(`/api/runs/${run.id}`);
      finishRun(fresh);
    } catch (e) { toast(e.message, 4000); }
  }));
}

function confBadge(c) {
  if (c === undefined || c === null) return "";
  const cls = c >= 0.85 ? "" : c >= 0.7 ? "mid" : "low";
  return `<span class="conf"><span class="conf-bar ${cls}"><span style="width:${Math.round(c * 100)}%"></span></span>${Math.round(c * 100)}%</span>`;
}

function renderRight(run) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === state.rightTab));
  const body = $("#tab-body");
  if (!body) return;
  if (state.rightTab === "doc") {
    body.innerHTML = `<iframe class="doc-frame" src="/api/runs/${esc(run.id)}/file#toolbar=0&view=FitH" title="Invoice document"></iframe>`;
    return;
  }
  const res = run.result;
  if (!res) { body.innerHTML = `<div class="empty"><b>Waiting for extraction…</b>Results appear here as soon as the run completes.</div>`; return; }
  if (state.rightTab === "text") {
    const read = res.stages.find((s) => s.stage === "read");
    body.innerHTML = `<pre class="raw-text">${esc(read?.details?.text || "(no text)")}</pre>`;
    return;
  }
  const f = res.fields || {};
  const c = f.confidence || {};
  const rows = [
    ["Vendor", f.vendor_name, c.vendor_name], ["Vendor tax ID", f.vendor_tax_id], ["Vendor email", f.vendor_email],
    ["Invoice number", f.invoice_number, c.invoice_number], ["Invoice date", f.invoice_date, c.invoice_date], ["Due date", f.due_date],
    ["PO reference", f.po_number || "— not on invoice", c.po_number], ["Payment terms", f.payment_terms], ["Currency", f.currency],
    ["Subtotal", money(f.subtotal), c.subtotal], ["Tax", f.tax_amount != null ? money(f.tax_amount) + (f.tax_rate ? ` (${(f.tax_rate * 100).toFixed(2).replace(/\.?0+$/, "")}%)` : "") : "—"],
    ["Total", money(f.total), c.total], ["Remit-to bank", f.remit_bank_name], ["Account", f.remit_account], ["Routing", f.remit_routing],
  ];
  body.innerHTML = `<div class="card-b">
    <div class="row" style="margin-bottom:10px"><span class="subhead" style="margin:0">Header fields</span><span class="grow"></span><span class="chip chip-gray">${esc(f.engine === "rules" ? "Local rules parser" : f.engine || "")}</span></div>
    <table class="t compact"><tr><th>Field</th><th>Value</th><th class="right">Confidence</th></tr>
    ${rows.map(([k, v, cf]) => `<tr><td class="muted">${esc(k)}</td><td>${esc(v ?? "—")}</td><td class="right">${confBadge(cf)}</td></tr>`).join("")}</table>
    <div class="subhead" style="margin-top:16px">Line items ${confBadge(c.line_items)}</div>
    <table class="t compact"><tr><th>Description</th><th class="right">Qty</th><th class="right">Unit</th><th class="right">Amount</th></tr>
    ${(f.line_items || []).map((l) => `<tr><td>${l.sku ? `<span class="code-chip">${esc(l.sku)}</span> ` : ""}${esc(l.description)}</td><td class="right num">${l.qty ?? "—"}</td><td class="right num">${money(l.unit_price)}</td><td class="right num">${money(l.amount)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No line items found</td></tr>'}</table>
    ${(f.notices || []).length ? `<div class="subhead" style="margin-top:16px">Notices on the document</div>${f.notices.map((n) => `<div class="finding warn" style="grid-template-columns:1fr;margin-bottom:6px">“${esc(n)}”</div>`).join("")}` : ""}
  </div>`;
}

/* --------------------------------------------------------------- dashboard */
function runsTable(runs, compact = false) {
  return `<div class="table-wrap"><table class="t ${compact ? "compact" : ""}"><tr><th>Run</th><th>When</th><th>Vendor</th><th>Invoice</th><th>PO</th><th class="right">Total</th><th>Auto decision</th><th>Status</th>${compact ? "" : "<th>Reasons</th><th class=right>Time</th>"}</tr>
    ${runs.map((r) => `<tr class="clickable" data-id="${esc(r.id)}">
      <td class="nowrap"><b>${esc(r.id)}</b></td><td class="muted small nowrap">${timeAgo(r.created_at)}</td><td>${esc(r.vendor_name || "—")}</td>
      <td class="mono nowrap">${esc(r.invoice_number || "—")}</td><td class="mono nowrap">${esc(r.po_number || "—")}</td><td class="right num nowrap">${money(r.total)}</td>
      <td>${decisionChip(r.status === "running" ? null : r.decision)}</td>
      <td>${r.status === "running" ? "" : r.review ? `${decisionChip(r.final_status)} <span class="small faint">by ${esc(r.review.reviewer)}</span>` : r.final_status === "NEEDS_REVIEW" ? `<span class="small" style="color:var(--amber)">Awaiting ${esc(r.result?.routed_to || "review")}</span>` : '<span class="small muted">Final</span>'}</td>
      ${compact ? "" : `<td>${(r.result?.reason_codes || []).map((c) => `<span class="code-chip">${esc(c)}</span>`).join(" ")}</td><td class="right mono small">${r.duration_ms ? (r.duration_ms / 1000).toFixed(1) + "s" : ""}</td>`}
    </tr>`).join("")}</table></div>`;
}
function bindRunRows(el) { $$("tr.clickable", el).forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/run/${tr.dataset.id}`))); }

async function renderDashboard() {
  const filter = state.dashFilter || "ALL";
  const q = state.dashQuery || "";
  const [s, runs] = await Promise.all([api("/api/stats"), api("/api/runs")]);
  const fs = s.final_status || {};
  const n = s.runs || 0;
  const shown = runs.filter((r) => (filter === "ALL" || r.final_status === filter) &&
    (!q || [r.id, r.vendor_name, r.invoice_number, r.po_number].join(" ").toLowerCase().includes(q.toLowerCase())));
  const seg = (k, label) => `<button class="${filter === k ? "active" : ""}" data-f="${k}">${label}</button>`;
  const mixSeg = (k, color) => (fs[k] ? `<span style="width:${(100 * fs[k]) / n}%;background:${color}">${fs[k]}</span>` : "");
  const maxR = Math.max(1, ...s.top_reasons.map((r) => r[1]));
  view.innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><p>Every invoice processed, its decision, and what's waiting on a person.</p></div>
      <a class="btn btn-primary" href="#/process">＋ Process invoice</a></div>
    <div class="kpis">
      <div class="card kpi"><div class="k-label">Invoices processed</div><div class="k-val">${n}</div><div class="k-sub">${money(s.value_processed)} total value</div></div>
      <div class="card kpi"><div class="k-label">Straight-through rate</div><div class="k-val">${s.straight_through_rate == null ? "—" : pct(s.straight_through_rate)}</div><div class="k-sub">auto-approved, no human touch</div></div>
      <div class="card kpi"><div class="k-label">Awaiting review</div><div class="k-val" style="color:${s.pending_review ? "var(--amber)" : "inherit"}">${s.pending_review}</div><div class="k-sub">${money(s.value_on_hold)} on hold</div></div>
      <div class="card kpi"><div class="k-label">Approved for payment</div><div class="k-val">${fs.APPROVED || 0}</div><div class="k-sub">${money(s.value_approved)}</div></div>
      <div class="card kpi"><div class="k-label">Payments prevented</div><div class="k-val" style="color:${fs.REJECTED ? "var(--red)" : "inherit"}">${fs.REJECTED || 0}</div><div class="k-sub">${money(s.value_blocked)} not paid</div></div>
      <div class="card kpi"><div class="k-label">Avg. processing time</div><div class="k-val">${s.avg_duration_ms ? (s.avg_duration_ms / 1000).toFixed(1) + "s" : "—"}</div><div class="k-sub">per invoice, end to end</div></div>
    </div>
    <div class="dash-grid">
      <div class="card"><div class="card-h"><h3>Decision mix</h3><span class="small muted">current status, after human review</span></div><div class="card-b">
        ${n ? `<div class="mix">${mixSeg("APPROVED", "var(--green)")}${mixSeg("NEEDS_REVIEW", "var(--amber-500)")}${mixSeg("REJECTED", "var(--red)")}</div>
        <div class="legend"><span><i style="background:var(--green)"></i>Approved ${fs.APPROVED || 0}</span><span><i style="background:var(--amber-500)"></i>Needs review ${fs.NEEDS_REVIEW || 0}</span><span><i style="background:var(--red)"></i>Rejected ${fs.REJECTED || 0}</span></div>
        <div class="small muted" style="margin-top:10px">Auto decisions: ${s.auto_decision.APPROVED || 0} approved · ${s.auto_decision.NEEDS_REVIEW || 0} held · ${s.auto_decision.REJECTED || 0} rejected</div>`
        : '<div class="muted">No runs yet.</div>'}</div></div>
      <div class="card"><div class="card-h"><h3>Why invoices were stopped</h3><span class="small muted">hold / reject reason codes</span></div><div class="card-b">
        ${s.top_reasons.length ? `<div class="bars">${s.top_reasons.map(([code, c]) => `<div class="b"><span class="code-chip" style="justify-self:start">${esc(code)}</span><div class="track"><div class="fill ${["DUPLICATE_INVOICE", "UNKNOWN_VENDOR", "VENDOR_BLOCKED"].includes(code) ? "reject" : ""}" style="width:${(100 * c) / maxR}%"></div></div><span class="num right">${c}</span></div>`).join("")}</div>` : '<div class="muted">Nothing stopped yet.</div>'}
      </div></div>
    </div>
    <div class="card">
      <div class="toolbar"><h3 style="margin:0;font-size:14.5px">Run history</h3><span class="grow"></span>
        <div class="seg">${seg("ALL", "All")}${seg("APPROVED", "Approved")}${seg("NEEDS_REVIEW", "Needs review")}${seg("REJECTED", "Rejected")}</div>
        <input class="search" id="dash-q" placeholder="Search vendor, invoice #, PO, run…" value="${esc(q)}"></div>
      ${shown.length ? runsTable(shown) : `<div class="empty"><b>${runs.length ? "No runs match this filter" : "No runs yet"}</b>${runs.length ? "" : 'Go to <a href="#/process">Process invoice</a> and run a scenario.'}</div>`}
    </div>`;
  bindRunRows(view);
  $$("[data-f]").forEach((b) => b.addEventListener("click", () => { state.dashFilter = b.dataset.f; renderDashboard(); }));
  const qi = $("#dash-q");
  qi.addEventListener("input", () => { state.dashQuery = qi.value; clearTimeout(state.qt); state.qt = setTimeout(() => { renderDashboard().then(() => { const el = $("#dash-q"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }); }, 250); });
  refreshReviewCount();
  clearInterval(state.poll);
  if (runs.some((r) => r.status === "running")) state.poll = setInterval(() => { if (location.hash.startsWith("#/dashboard") && document.activeElement?.id !== "dash-q") renderDashboard(); }, 2000);
}

/* --------------------------------------------------------------- reference */
async function renderReference() {
  const d = await api("/api/reference");
  const tab = state.refTab || "pos";
  const tabs = [["pos", "Purchase orders"], ["vendors", "Vendor master"], ["ledger", "AP ledger"], ["receipts", "Goods receipts"], ["policy", "AP policy"]];
  let body = "";
  if (tab === "pos") {
    body = `<table class="t"><tr><th>PO</th><th>Vendor</th><th>Description</th><th>Match</th><th>Owner</th><th class="right">Amount</th><th>Billed</th><th class="right">Remaining</th><th>Status</th></tr>
    ${d.purchase_orders.map((p) => { const ratio = p.billed / p.amount; return `<tr><td class="mono nowrap">${esc(p.po_number)}</td><td>${esc(p.vendor_name)}</td><td>${esc(p.description)}<div class="small faint">${p.lines.map((l) => `${l.qty} × ${esc(l.description)} @ ${money(l.unit_price)}`).join(" · ")}</div></td>
      <td><span class="chip chip-gray">${esc(p.match_type)}</span></td><td class="small">${esc(p.owner)}</td><td class="right num">${money(p.amount)}</td>
      <td><span class="prog"><span class="${ratio > 1.0001 ? "over" : ""}" style="width:${Math.min(100, ratio * 100)}%"></span></span> <span class="small num">${Math.round(ratio * 100)}%</span>${p.on_hold ? `<div class="small" style="color:var(--amber)">${money(p.on_hold)} on hold</div>` : ""}</td>
      <td class="right num nowrap" style="${p.remaining < 0 ? "color:var(--red)" : ""}">${money(p.remaining)}</td><td><span class="chip ${p.status === "open" ? "chip-green" : "chip-gray"}">${esc(p.status)}</span></td></tr>`; }).join("")}</table>`;
  } else if (tab === "vendors") {
    body = `<table class="t"><tr><th>ID</th><th>Name</th><th>Tax ID</th><th>Status</th><th>Terms</th><th>Contact</th><th>Phone</th><th>Bank on file</th></tr>
    ${d.vendors.map((v) => `<tr><td class="mono">${esc(v.vendor_id)}</td><td>${esc(v.name)}</td><td class="mono">${esc(v.tax_id)}</td><td><span class="chip ${v.status === "approved" ? "chip-green" : "chip-red"}">${esc(v.status)}</span></td><td>${esc(v.payment_terms)}</td><td>${esc(v.contact_email)}</td><td class="small">${esc(v.phone)}</td><td class="small">${esc(v.bank_name)} <span class="mono">${esc(v.bank_account)}</span></td></tr>`).join("")}</table>`;
  } else if (tab === "ledger") {
    body = `<table class="t"><tr><th>Vendor</th><th>Invoice #</th><th>Normalised key</th><th>Date</th><th>PO</th><th class="right">Net</th><th class="right">Total</th><th>Status</th><th>Source</th></tr>
    ${d.ledger.map((l) => `<tr><td class="mono">${esc(l.vendor_id)}</td><td class="mono nowrap">${esc(l.invoice_number)}</td><td class="mono faint">${esc(l.invoice_key)}</td><td class="nowrap">${esc(l.invoice_date)}</td><td class="mono nowrap">${esc(l.po_number || "—")}</td><td class="right num">${money(l.subtotal)}</td><td class="right num">${money(l.total)}</td>
      <td><span class="chip ${l.status === "paid" || l.status === "approved" ? "chip-green" : l.status === "on_hold" ? "chip-amber" : "chip-red"}">${esc(l.status)}</span></td>
      <td>${l.source.startsWith("RUN-") ? `<a href="#/run/${esc(l.source)}">${esc(l.source)}</a>` : `<span class="muted">${esc(l.source)}</span>`}</td></tr>`).join("")}</table>`;
  } else if (tab === "receipts") {
    body = `<table class="t"><tr><th>GRN</th><th>PO</th><th>PO line</th><th class="right">Qty received</th><th>Date</th></tr>${d.receipts.map((r) => `<tr><td class="mono">${esc(r.grn)}</td><td class="mono">${esc(r.po_number)}</td><td>${r.line}</td><td class="right num">${r.qty_received}</td><td>${esc(r.receipt_date)}</td></tr>`).join("")}</table>`;
  } else {
    const p = d.policy;
    body = `<div class="card-b">${kv([
      ["Header tolerance", `Auto-accept if over the open PO balance by ≤ ${p.header_tolerance_pct}% <b>and</b> ≤ ${money(p.header_tolerance_abs)}`],
      ["Line price tolerance", `±${p.line_price_tolerance_pct}% vs PO unit price`], ["Tax rate tolerance", `±${p.tax_rate_tolerance_pp} percentage points vs PO rate`],
      ["Auto-approval limit", `${money(p.auto_approve_limit)}: above this, Finance Controller approval is always required`],
      ["Max invoice age", `${p.max_invoice_age_days} days`], ["Fuzzy duplicate window", `${p.duplicate_fuzzy_window_days} days (same vendor + same amount)`],
      ["Min field confidence", pct(p.min_field_confidence) + " (below this: a person confirms the field)"], ["Min OCR confidence", pct(p.min_ocr_confidence)],
      ["Inferred PO threshold", `Score ≥ ${p.inferred_po_min_score} and a clear gap (≥ 0.25) over the next candidate`], ["Currency", p.currency]])}</div>`;
  }
  view.innerHTML = `<div class="page-head"><div><h1>Reference data</h1><p>The procurement data the process checks against. It stands in for the ERP. Billed amounts update live as invoices are approved.</p></div></div>
    <div class="card"><div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${k === tab ? "active" : ""}" data-rt="${k}">${l}</button>`).join("")}</div><div style="overflow-x:auto">${body}</div></div>`;
  $$("[data-rt]").forEach((b) => b.addEventListener("click", () => { state.refTab = b.dataset.rt; renderReference(); }));
}

/* ------------------------------------------------------------- how it works */
function renderHow() {
  const steps = [
    ["Intake", "Fingerprint the file; spot exact re-uploads"], ["Read", "Text layer, or OCR for scans"], ["Extract", "Claude, or local parser, to structured fields + confidence", true],
    ["Validate", "Required fields, maths, dates, currency"], ["Vendor", "Master data, status, tax ID, bank details"], ["Duplicates", "Normalised + fuzzy match vs AP ledger"],
    ["PO match", "Explicit or inferred; lines, tolerance, split billing, receipts"], ["Decide", "Policy turns findings into a decision, owner, actions"],
  ];
  view.innerHTML = `<div class="page-head"><div><h1>How it works</h1><p>Design decisions and assumptions behind the process.</p></div></div>
  <div class="card" style="margin-bottom:18px"><div class="card-h"><h3>The pipeline</h3><span class="small muted"><span class="chip chip-blue">blue</span> = AI step · everything else is deterministic code</span></div><div class="card-b">
    <div class="flow">${steps.map(([t, p, ai], i) => `<div class="flow-step ${ai ? "ai" : ""}"><div class="n">${i + 1}</div><b>${t}</b><p>${p}</p></div>`).join("")}</div></div></div>
  <div class="two-col">
    <div class="card"><div class="card-b prose">
      <h3 style="margin-top:0">Design principles</h3><ul>
      <li><b>AI reads, rules decide.</b> The model only turns a messy document into structured data. Approve / hold / reject is deterministic policy code, so every decision is auditable and reproducible.</li>
      <li><b>Every decision explains itself.</b> Each check emits findings (code, severity, message, evidence). The decision and its explanation are built from the same findings, so they can't disagree.</li>
      <li><b>Fail safe, never silent.</b> Low confidence, missing data or a processing error routes to a named person with a specific reason. Reject only when paying would clearly be wrong.</li>
      <li><b>Route to the right owner.</b> Fraud signals go to AP Manager + Treasury, PO problems to the PO owner, data issues to an AP specialist, high-value invoices to the Finance Controller.</li>
      <li><b>Demo-proof.</b> With an API key, extraction uses Claude. Without one, local OCR + a layout-aware parser run the same pipeline, and the UI shows which engine ran.</li></ul>
      <h3>Edge cases and why I chose them</h3><ul>
      <li><b>Split-PO overbilling:</b> each invoice looks valid alone; only cumulative tracking across invoices catches it.</li>
      <li><b>Scanned, no PO:</b> the two most common real-world gaps together. Needs OCR <i>and</i> PO inference, with a guard against guessing.</li>
      <li><b>Disguised duplicate:</b> "past due" re-sends with reformatted numbers are a leading cause of duplicate payments.</li>
      <li><b>Bank-detail change:</b> the costliest AP failure (business-email compromise). The invoice is perfect on every number, so only a vendor-master cross-check catches it.</li></ul>
    </div></div>
    <div class="card"><div class="card-b prose">
      <h3 style="margin-top:0">Assumptions</h3><ul>
      <li>Buyer is a fictional US manufacturer (Northwind Manufacturing); currency USD. All vendors, POs and invoices are synthetic.</li>
      <li>CSV files stand in for the ERP: vendor master, POs, PO lines, goods receipts. The AP ledger is SQLite.</li>
      <li>Goods POs use a 3-way match (PO + invoice + goods receipt); service POs a 2-way match.</li>
      <li>Over-billing within ≤2% and ≤$250 of the open PO balance is accepted (e.g. surcharges); beyond that it's held for the PO owner.</li>
      <li>Fixed-fee lines can be billed in milestones; over-billing is judged cumulatively at PO level.</li>
      <li>A PO is inferred only with a unique, strong line-level match; otherwise a buyer picks.</li>
      <li>Bank changes are never accepted from an invoice; they need out-of-band verification via the phone number on file.</li>
      <li>Invoices over $25,000 always need Finance Controller approval.</li>
      <li>Approved means scheduled for payment per terms; payment execution itself is out of scope.</li></ul>
      <h3>What I'd build next</h3><ul>
      <li>Email-inbox intake (forward-to address), with multi-invoice PDFs split automatically.</li>
      <li>ERP connector (NetSuite / SAP) in place of the CSVs, plus write-back of approved bills.</li>
      <li>Learning loop: reviewer corrections become per-vendor extraction hints and tolerance tuning.</li>
      <li>SLA timers and escalation on held invoices to protect early-payment discounts.</li></ul>
    </div></div>
  </div>`;
}

/* ------------------------------------------------------------------- boot */
$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset demo data? This clears all run history and restores the AP ledger to its seeded state.")) return;
  await api("/api/reset", { method: "POST" });
  toast("Demo data reset");
  location.hash = "#/process";
  router();
  refreshReviewCount();
});

(async function boot() {
  [state.health, state.samples] = await Promise.all([api("/api/health"), api("/api/samples")]);
  $("#engine-label").textContent = state.health.engine === "claude" ? `Claude · ${state.health.model}` : "Local OCR + rules parser";
  refreshReviewCount();
  router();
})();
