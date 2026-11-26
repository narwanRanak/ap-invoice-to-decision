/* AP Desk - single-page UI (vanilla JS, no build step). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x === null || x === undefined || Number.isNaN(x) ? "—" : (x < 0 ? "−$" : "$") + Math.abs(Number(x)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const moneyShort = (x) => (x >= 1000 ? "$" + (x / 1000).toFixed(x >= 10000 ? 0 : 1) + "k" : money(x));
const pct = (x) => (x === null || x === undefined ? "—" : Math.round(x * 100) + "%");
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
  return r.json();
};
const ago = (iso) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return new Date(iso).toLocaleDateString();
};
function toast(msg, ms = 2600) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), ms); }

const STATUS = {
  APPROVED: { label: "Ready to pay", cls: "pay" },
  NEEDS_REVIEW: { label: "On hold", cls: "hold" },
  REJECTED: { label: "Rejected", cls: "rej" },
};
const pill = (d) => (d ? `<span class="pill ${STATUS[d]?.cls || "neutral"}">${STATUS[d]?.label || esc(d)}</span>` : `<span class="pill run">Processing</span>`);
const I = {
  check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  x: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4.5 4.5l7 7m0-7l-7 7"/></svg>',
  bang: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M8 4v5m0 3v.5"/></svg>',
  dash: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 8h6"/></svg>',
  down: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v8m0 0L5 7.5m3 3l3-3M3 13h10"/></svg>',
  up: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 10.5V3m0 0L5 6m3-3l3 3M3 10v2a1 1 0 001 1h8a1 1 0 001-1v-2"/></svg>',
  play: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M5 3.5v9l7-4.5z"/></svg>',
  arrow: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h10m0 0L9 4m4 4l-4 4"/></svg>',
};
const DOT = { pending: "", running: "", pass: I.check, warn: I.check, hold: I.bang, reject: I.x, error: I.x, skip: I.dash };

const st = { health: null, samples: [], es: null, timer: null, poll: null, open: new Set(), tab: "activity", run: null, recs: {}, liveFields: null };

/* ---------------------------------------------------------------- router */
const setTop = (crumbs, actions = "") => { $("#crumbs").innerHTML = crumbs; $("#top-actions").innerHTML = actions; };
async function router() {
  if (st.es) { st.es.close(); st.es = null; }
  clearInterval(st.timer); clearInterval(st.poll);
  let [, route, arg] = (location.hash || "#/invoices").split("/");
  if (route === "dashboard") route = "invoices";
  if (route === "process") route = "upload";
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (route === "run" ? "invoices" : route)));
  window.scrollTo(0, 0);
  try {
    if (route === "run" && arg) return await renderRun(arg);
    if (route === "upload") return renderUpload();
    if (route === "reference") return await renderReference();
    if (route === "how") return renderHow();
    return await renderInvoices();
  } catch (e) {
    view.innerHTML = `<div class="panel empty"><b>Something went wrong</b>${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", router);

async function refreshBadge() {
  try { const s = await api("/api/stats"); const b = $("#nav-hold"); b.textContent = s.pending_review; b.classList.toggle("hidden", !s.pending_review); } catch (_) {}
}

/* ---------------------------------------------------------------- upload */
function renderUpload() {
  setTop(`<b>Upload</b>`);
  const rows = st.samples.map((s) => `
    <tr>
      <td style="width:118px"><span class="tag ${s.kind.startsWith("Happy") ? "happy" : "edge"}">${esc(s.kind)}</span></td>
      <td><div class="scen-title">${esc(s.title)}</div><div class="scen-desc">${esc(s.blurb)}</div></td>
      <td class="nowrap">${pill(s.expected)}</td>
      <td class="right nowrap">
        <a class="btn btn-sm btn-ghost" href="/api/samples/${esc(s.id)}/file" target="_blank">PDF</a>
        <button class="btn btn-sm btn-dark" data-run="${esc(s.id)}">${I.play} Run</button>
      </td>
    </tr>`).join("");
  view.innerHTML = `
    <div class="ph"><div><h1>Upload invoices</h1><p>Each invoice runs through 8 stages: read, extract, validate, vendor, duplicates, PO match, decide. You watch every one.</p></div></div>
    <label class="drop" id="drop">
      <div class="drop-icon">${I.up}</div>
      <div class="grow"><b>Drop an invoice PDF here, or click to browse</b><div class="hint">Born-digital or scanned · single invoice per file · up to 15 MB</div></div>
      <span class="btn btn-sm">Choose file</span>
    </label>
    <div class="panel">
      <div class="panel-h"><h3>Test scenarios</h3><span class="muted small">2 happy paths · 4 edge cases, each built to exercise a different control</span><span class="grow"></span>
        <button class="btn btn-sm" id="run-all">${I.play} Run all in order</button></div>
      <div class="table-wrap"><table class="t"><tr><th>Type</th><th>Scenario</th><th>Designed outcome</th><th></th></tr>${rows}</table></div>
    </div>
    <p class="muted small" style="margin-top:14px">More unseen inputs (unknown vendor, blocked vendor, price increase with no goods receipt, missing invoice number) are in <span class="mono">test_inputs/</span> in the repo.</p>`;
  const drop = $("#drop");
  drop.addEventListener("click", (e) => { e.preventDefault(); $("#file-input").click(); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
  $$("[data-run]").forEach((b) => b.addEventListener("click", () => startSample(b.dataset.run)));
  $("#run-all").addEventListener("click", runAll);
}
$("#file-input").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) upload(f); e.target.value = ""; });

async function upload(file) {
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return toast("Please choose a PDF file.");
  const fd = new FormData(); fd.append("file", file);
  try { const { run_id } = await api("/api/runs", { method: "POST", body: fd }); location.hash = `#/run/${run_id}`; }
  catch (e) { toast("Upload failed: " + e.message, 4000); }
}
async function startSample(id) {
  const fd = new FormData(); fd.append("sample_id", id);
  const { run_id } = await api("/api/runs", { method: "POST", body: fd });
  location.hash = `#/run/${run_id}`;
}
async function runAll() {
  const b = $("#run-all"); b.disabled = true;
  for (const [i, s] of st.samples.entries()) {
    b.textContent = `Running ${i + 1} of ${st.samples.length}…`;
    const fd = new FormData(); fd.append("sample_id", s.id);
    const { run_id } = await api("/api/runs", { method: "POST", body: fd });
    for (;;) { await new Promise((r) => setTimeout(r, 400)); if ((await api(`/api/runs/${run_id}`)).status !== "running") break; }
  }
  toast("All six scenarios processed");
  location.hash = "#/invoices";
}

/* --------------------------------------------------- evidence mapping */
function lineKeys(run, pred) {
  const lines = run?.result?.po?.lines || [];
  return lines.map((l, i) => (pred(l) ? `line_${i}` : null)).filter(Boolean);
}
function evKeys(f, run) {
  const fx = run?.result?.fields || st.liveFields || {};
  const notices = (fx.notices || []).map((_, i) => `notice_${i}`);
  const lines = (fx.line_items || []).map((_, i) => `line_${i}`);
  switch (f.code) {
    case "BANK_DETAILS_CHANGED": return ["remit_account", "remit_bank_name", ...notices];
    case "CONTACT_DOMAIN_MISMATCH": return ["vendor_email"];
    case "DUPLICATE_INVOICE": case "POSSIBLE_DUPLICATE": return ["invoice_number"];
    case "REMINDER_NOTICE": return notices;
    case "PO_OVERBILLED": case "PO_AMOUNT_EXCEEDED": return ["subtotal", ...lines];
    case "PRICE_VARIANCE_WITHIN_TOLERANCE": return lineKeys(run, (l) => l.status === "not on PO");
    case "PRICE_VARIANCE": case "QTY_NOT_RECEIVED": case "QTY_EXCEEDS_PO": return lineKeys(run, (l) => !["ok", "milestone / partial", "not on PO"].includes(l.status));
    case "PO_INFERRED": return lines;
    case "PO_NOT_FOUND": case "PO_VENDOR_MISMATCH": case "PO_CLOSED": return ["po_number"];
    case "VENDOR_BLOCKED": case "UNKNOWN_VENDOR": return ["vendor_name"];
    case "TAX_ID_MISMATCH": return ["vendor_tax_id"];
    case "TAX_MISMATCH": return ["tax_amount"];
    case "MATH_MISMATCH": return ["subtotal", "total"];
    case "MISSING_FIELD": case "LOW_CONFIDENCE_FIELD": return f.evidence?.field ? [f.evidence.field] : [];
    default: return [];
  }
}

/* -------------------------------------------------------------- run view */
async function renderRun(runId) {
  const run = await api(`/api/runs/${runId}`);
  const health = st.health || (st.health = await api("/api/health"));
  st.run = run; st.recs = {}; st.open = new Set(); st.tab = "activity"; st.liveFields = null;
  const sample = (run.source || "").startsWith("sample:") ? st.samples.find((s) => "sample:" + s.id === run.source) : null;
  const nxt = sample ? st.samples[st.samples.findIndex((s) => s.id === sample.id) + 1] : null;
  setTop(`<a href="#/invoices">Invoices</a><span class="sep">/</span><b>${esc(run.id)}</b>`,
    `<a class="btn btn-sm hidden" id="audit-btn" href="/api/runs/${esc(run.id)}/audit" download>${I.down} Audit packet</a>
     ${nxt ? `<button class="btn btn-sm btn-dark" id="next-btn">Next: ${esc(nxt.kind)} ${I.arrow}</button>` : `<a class="btn btn-sm" href="#/upload">${I.up} Upload another</a>`}`);
  if (nxt) $("#next-btn").addEventListener("click", () => startSample(nxt.id));

  view.innerHTML = `
    <div class="run-head">
      <h1 id="rh-vendor">${esc(run.vendor_name || run.filename)}</h1>
      <span class="inv" id="rh-inv">${run.invoice_number ? esc(run.invoice_number) : ""}</span>
      <span id="rh-status">${pill(run.status === "running" ? null : run.final_status)}</span>
      ${sample ? `<span class="tag ${sample.kind.startsWith("Happy") ? "happy" : "edge"}">${esc(sample.kind)}</span>` : `<span class="tag">Uploaded</span>`}
    </div>
    <div class="stepper">${health.stages.map((s) => `<div class="step" id="sp-${s.key}" data-key="${s.key}" title="${esc(s.description)}"><div class="bar"></div><div class="lbl">${esc(s.label)}</div></div>`).join("")}</div>
    <div class="step-meta"><span id="sm-left">Queued</span><span id="sm-right" class="mono"></span></div>
    <div class="work">
      <div class="panel viewer">
        <div class="panel-h"><h3>Document</h3><span class="muted small mono">${esc(run.filename)}</span><span class="grow"></span>
          <div class="viewer-legend"><span><i style="background:rgba(239,68,68,.25);border:1px solid #dc2626"></i>Flagged</span><span><i style="background:rgba(250,204,21,.5);border:1px solid #ca8a04"></i>Selected</span></div></div>
        <div class="viewer-stage"><div class="sheet" id="sheet"><img src="/api/runs/${esc(run.id)}/page/0.png" alt="Invoice page"><div id="boxes"></div></div></div>
      </div>
      <div><div id="verdict"></div>
      <div class="panel" id="right">
        <div class="tabs">
          <button class="tab" data-tab="activity">Activity</button>
          <button class="tab" data-tab="fields">Extracted fields</button>
          <button class="tab" data-tab="match">PO match</button>
          <button class="tab" data-tab="text">Raw text</button>
        </div>
        <div id="tab-body"></div>
      </div></div>
    </div>`;
  $$(".tab").forEach((t) => t.addEventListener("click", () => { st.tab = t.dataset.tab; renderTab(); }));
  $$(".step").forEach((s) => s.addEventListener("click", () => { st.tab = "activity"; openStage(s.dataset.key, true); }));
  renderTab();

  if (run.status === "running") {
    const t0 = Date.now();
    st.timer = setInterval(() => { const el = $("#sm-right"); if (el) el.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s"; }, 100);
    const es = new EventSource(`/api/runs/${runId}/events`);
    st.es = es;
    es.onmessage = async (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "stage_start") stageRunning(ev.stage, ev.label);
      else if (ev.type === "stage_end") stageDone(ev);
      else if (ev.type === "done") { es.close(); st.es = null; clearInterval(st.timer); finish(await api(`/api/runs/${runId}`)); }
    };
  } else {
    (run.result?.stages || []).forEach(stageDone);
    finish(run);
  }
}

function stageRunning(key, label) {
  const sp = $(`#sp-${key}`); if (sp) sp.className = "step running";
  const idx = st.health.stages.findIndex((s) => s.key === key);
  $("#sm-left").textContent = `Stage ${idx + 1} of 8 · ${label}…`;
  st.recs[key] = { stage: key, status: "running" };
  if (st.tab === "activity") renderTab();
}
function stageDone(rec) {
  st.recs[rec.stage] = rec;
  const sp = $(`#sp-${rec.stage}`); if (sp) sp.className = `step done ${rec.status}`;
  if (rec.stage === "extract" && rec.details?.fields) {
    const f = rec.details.fields;
    if (f.vendor_name) $("#rh-vendor").textContent = f.vendor_name;
    if (f.invoice_number) $("#rh-inv").textContent = f.invoice_number;
    st.liveFields = f;
    drawBoxes();
  }
  if (["hold", "reject", "error"].includes(rec.status) && rec.stage !== "decide") st.open.add(rec.stage);
  if (st.tab === "activity") renderTab();
}

function finish(run) {
  st.run = run;
  $("#rh-status").innerHTML = pill(run.final_status);
  if (run.vendor_name) $("#rh-vendor").textContent = run.vendor_name;
  if (run.invoice_number) $("#rh-inv").textContent = run.invoice_number;
  $("#sm-left").textContent = `Completed · ${run.engine === "rules" ? "local OCR + rules engine" : run.engine || ""}`;
  if (run.duration_ms) $("#sm-right").textContent = (run.duration_ms / 1000).toFixed(1) + "s end to end";
  $("#audit-btn")?.classList.remove("hidden");
  renderVerdict(run);
  drawBoxes();
  renderTab();
  refreshBadge();
}

/* evidence boxes on the document */
function drawBoxes() {
  const fx = st.run?.result?.fields || st.liveFields;
  const layer = $("#boxes");
  if (!fx || !layer) return;
  const ev = fx.evidence || {};
  const flagged = new Set();
  (st.run?.result?.findings || []).filter((f) => ["hold", "reject"].includes(f.severity)).forEach((f) => evKeys(f, st.run).forEach((k) => flagged.add(k)));
  layer.innerHTML = Object.entries(ev).filter(([, v]) => v.page === 0).map(([k, v]) => {
    const [x0, y0, x1, y1] = v.box; const p = 0.004;
    return `<div class="hl ${flagged.has(k) ? "flag" : "field"}" data-k="${esc(k)}" style="left:${(x0 - p) * 100}%;top:${(y0 - p) * 100}%;width:${(x1 - x0 + 2 * p) * 100}%;height:${(y1 - y0 + 2 * p) * 100}%"></div>`;
  }).join("");
}
function highlight(keys, on) {
  $$("#boxes .hl").forEach((b) => b.classList.toggle("on", on && keys.includes(b.dataset.k)));
  if (on) {
    const first = $$("#boxes .hl").find((b) => keys.includes(b.dataset.k));
    const stage = $(".viewer-stage");
    if (first && stage) {
      const r = first.getBoundingClientRect(), s = stage.getBoundingClientRect();
      if (r.top < s.top || r.bottom > s.bottom) stage.scrollBy({ top: r.top - s.top - s.height / 3, behavior: "smooth" });
    }
  }
}
function bindEvidence(root) {
  $$("[data-ev]", root).forEach((el) => {
    const keys = el.dataset.ev.split(",").filter(Boolean);
    el.addEventListener("mouseenter", () => highlight(keys, true));
    el.addEventListener("mouseleave", () => highlight(keys, false));
    el.addEventListener("click", (e) => { e.stopPropagation(); highlight(keys, true); });
  });
}

function findingHtml(f, run) {
  const r = run || st.run;
  const ev = r?.result?.fields?.evidence || st.liveFields?.evidence || {};
  const keys = evKeys(f, r).filter((k) => ev[k]);
  return `<div class="finding ${esc(f.severity)}" ${keys.length ? `data-ev="${esc(keys.join(","))}"` : ""}>
    <span class="code">${esc(f.code)}</span>
    <span>${esc(f.message)}${keys.length ? ` <span class="loc">· show on invoice ↗</span>` : ""}</span></div>`;
}

/* verdict */
function renderVerdict(run) {
  const dec = run.result?.decision; const el = $("#verdict");
  if (!dec || !el) return;
  const d = dec.decision;
  const reviewed = run.review;
  const noted = (dec.notes || []).filter((n) => ["PO_INFERRED", "PRICE_VARIANCE_WITHIN_TOLERANCE", "SCANNED_DOCUMENT", "AMBIGUOUS_DATE", "CONTACT_DOMAIN_MISMATCH", "EXTRACTOR_FALLBACK"].includes(n.code));
  const label = { APPROVED: "Approved for payment", NEEDS_REVIEW: "Held for review", REJECTED: "Will not be paid" }[d];
  const security = d === "NEEDS_REVIEW" && (dec.reasons || []).some((r) => r.category === "security");
  const reviewHtml = d !== "NEEDS_REVIEW" ? "" : reviewed
    ? `<div class="review"><div class="eyebrow">Human decision</div><div style="margin-top:6px">${pill(run.final_status)} by <b>${esc(reviewed.reviewer)}</b> · <span class="muted">${ago(reviewed.at)}</span></div><div class="muted" style="margin-top:6px">“${esc(reviewed.note)}”</div></div>`
    : `<div class="review"><div class="eyebrow">Your decision</div>
        <input id="rv-name" placeholder="Reviewer name" value="${esc(localStorage.getItem("reviewer") || "")}">
        <textarea id="rv-note" placeholder="${security ? "e.g. Called vendor on the number on file: no bank change. Reported to IT Security." : "Note for the audit trail"}"></textarea>
        <div class="row" style="margin-top:8px"><button class="btn btn-sm btn-pay" data-review="approve">Approve for payment</button><button class="btn btn-sm btn-rej" data-review="reject">Reject</button></div></div>`;
  el.innerHTML = `<div class="verdict ${d}">
    <div class="verdict-top"><div class="grow">
      <div class="row">${pill(run.final_status)}<span class="eyebrow">${label}${run.final_status !== d ? ` · decided by reviewer` : ""}</span></div>
      <h2>${esc(dec.headline)}</h2>
      <div class="verdict-meta"><span>Owner <b>${esc(dec.routed_to)}</b></span><span>Amount <b class="num">${money(run.total)}</b></span>${run.po_number ? `<span>PO <b class="mono">${esc(run.po_number)}</b></span>` : ""}<span>Blocking issues <b>${(dec.reasons || []).length}</b></span><span>Decided in <b class="num">${((run.duration_ms || 0) / 1000).toFixed(1)}s</b></span></div>
    </div></div>
    <div class="verdict-body">
      <div>
        <div class="eyebrow">Next actions</div>
        <ol class="steps-list">${dec.next_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
        ${(dec.controls_passed || []).length ? `<div class="eyebrow" style="margin-top:14px">Why it was approved</div><ul class="checks">${dec.controls_passed.map((c) => `<li><span class="ck pass">✓</span><span>${esc(c)}</span></li>`).join("")}</ul>` : ""}
        ${(dec.reasons || []).length ? `<div class="eyebrow" style="margin-top:14px">Why it was stopped</div>${dec.reasons.map((f) => findingHtml(f, run)).join("")}` : ""}
        ${d === "APPROVED" && noted.length ? `<div class="eyebrow" style="margin-top:14px">Noted, within policy</div>${noted.map((f) => findingHtml(f, run)).join("")}` : ""}
      </div>
      <div>
        ${dec.vendor_message ? `<div class="row"><span class="eyebrow grow">Draft to vendor</span><button class="btn btn-sm btn-ghost" id="copy-msg">Copy</button></div><div class="email">${esc(dec.vendor_message)}</div>`
          : security ? `<div class="eyebrow">Draft to vendor</div><div class="email">None, on purpose. Replying to contact details on a suspicious invoice can reach the fraudster. Verification goes through the phone number already on file.</div>`
          : `<div class="eyebrow">Draft to vendor</div><div class="email">No vendor action needed.</div>`}
        ${reviewHtml}
      </div>
    </div></div>`;
  bindEvidence(el);
  $("#copy-msg")?.addEventListener("click", () => { navigator.clipboard.writeText(dec.vendor_message); toast("Copied"); });
  $$("[data-review]", el).forEach((b) => b.addEventListener("click", async () => {
    const reviewer = $("#rv-name").value.trim(), note = $("#rv-note").value.trim();
    if (!reviewer || note.length < 5) return toast("Add your name and a short note. It goes into the audit trail.");
    localStorage.setItem("reviewer", reviewer);
    try {
      await api(`/api/runs/${run.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: b.dataset.review, reviewer, note }) });
      toast(b.dataset.review === "approve" ? "Approved for payment" : "Rejected");
      finish(await api(`/api/runs/${run.id}`));
    } catch (e) { toast(e.message, 4000); }
  }));
}

/* right-hand tabs */
function renderTab() {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === st.tab));
  const body = $("#tab-body"); if (!body) return;
  const run = st.run; const res = run?.result;
  if (st.tab === "activity") {
    body.innerHTML = `<ul class="log">${st.health.stages.map((s) => {
      const r = st.recs[s.key]; const status = r?.status || "pending";
      const shown = (r?.findings || []).filter((f) => f.severity !== "info" || ["PO_INFERRED", "PRICE_VARIANCE_WITHIN_TOLERANCE", "SCANNED_DOCUMENT", "PARTIAL_BILLING", "REMINDER_NOTICE"].includes(f.code));
      const open = st.open.has(s.key) && r?.details;
      return `<li class="ev ${status}" id="ev-${s.key}">
        <div class="ev-row" data-key="${s.key}"><div class="dot">${DOT[status] || ""}</div>
          <div><div class="ev-title">${esc(s.label)}</div><div class="ev-sum">${esc(status === "running" ? "Running…" : r?.summary || s.description)}</div></div>
          <div class="ev-time">${r?.duration_ms != null ? r.duration_ms + " ms" : ""}</div></div>
        ${shown.length || open ? `<div class="ev-body">${shown.map((f) => findingHtml(f, run)).join("")}${open ? `<div class="ev-detail">${stageDetail(r)}</div>` : ""}</div>` : ""}
      </li>`;
    }).join("")}</ul>`;
    $$(".ev-row", body).forEach((row) => row.addEventListener("click", () => openStage(row.dataset.key)));
    bindEvidence(body);
    return;
  }
  const fx = res?.fields || st.liveFields;
  if (!fx) { body.innerHTML = `<div class="empty"><b>Waiting for extraction</b>Fields appear as soon as the extract stage finishes.</div>`; return; }
  if (st.tab === "text") { body.innerHTML = `<pre class="raw">${esc(st.recs.read?.details?.text || "(no text)")}</pre>`; return; }
  if (st.tab === "match") { body.innerHTML = `<div class="panel-b">${res?.po ? poDetail(res.po) : '<div class="muted">Available when matching completes.</div>'}</div>`; bindEvidence(body); return; }
  const c = fx.confidence || {}; const ev = fx.evidence || {};
  const rows = [
    ["Vendor", fx.vendor_name, "vendor_name", c.vendor_name], ["Tax ID", fx.vendor_tax_id, "vendor_tax_id"], ["Email", fx.vendor_email, "vendor_email"],
    ["Invoice number", fx.invoice_number, "invoice_number", c.invoice_number], ["Invoice date", fx.invoice_date, "invoice_date", c.invoice_date],
    ["Due date", fx.due_date, "due_date"], ["PO reference", fx.po_number || "Not on invoice", "po_number", c.po_number], ["Terms", fx.payment_terms, "payment_terms"],
    ["Subtotal", money(fx.subtotal), "subtotal", c.subtotal], ["Tax", fx.tax_amount != null ? money(fx.tax_amount) + (fx.tax_rate ? ` · ${+(fx.tax_rate * 100).toFixed(3)}%` : "") : "—", "tax_amount"],
    ["Total", money(fx.total), "total", c.total], ["Remit-to bank", fx.remit_bank_name, "remit_bank_name"], ["Account", fx.remit_account, "remit_account"], ["Routing", fx.remit_routing, "remit_routing"],
  ];
  const mono = ["Subtotal", "Tax", "Total", "Account", "Routing", "Invoice number", "PO reference", "Tax ID"];
  body.innerHTML = `<div class="panel-b">
    <div class="row" style="margin-bottom:10px"><span class="muted small grow">Hover a row to see where it was read on the invoice.</span><span class="tag">${esc(fx.engine === "rules" ? "Local rules engine" : fx.engine || "")}</span></div>
    <table class="t tight fields">${rows.map(([k, v, key, cf]) => `<tr ${ev[key] ? `data-ev="${key}"` : ""}><td>${esc(k)}</td><td class="${mono.includes(k) ? "mono" : ""}">${esc(v ?? "—")}</td><td class="right">${conf(cf)}</td></tr>`).join("")}</table>
    <div class="subhead">Line items ${conf(c.line_items)}</div>
    <table class="t tight fields"><tr><th>Description</th><th class="right">Qty</th><th class="right">Unit</th><th class="right">Amount</th></tr>
    ${(fx.line_items || []).map((l, i) => `<tr ${ev["line_" + i] ? `data-ev="line_${i}"` : ""}><td style="color:var(--ink)">${l.sku ? `<span class="code">${esc(l.sku)}</span> ` : ""}${esc(l.description)}</td><td class="right num">${l.qty ?? "—"}</td><td class="right num">${money(l.unit_price)}</td><td class="right num">${money(l.amount)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No line items found</td></tr>'}</table>
    ${(fx.notices || []).length ? `<div class="subhead">Notices printed on the document</div>${fx.notices.map((n, i) => `<div class="finding warn" style="grid-template-columns:1fr" ${ev["notice_" + i] ? `data-ev="notice_${i}"` : ""}>“${esc(n)}”</div>`).join("")}` : ""}
  </div>`;
  bindEvidence(body);
}
function conf(c) {
  if (c === undefined || c === null) return "";
  const cls = c >= 0.85 ? "" : c >= 0.7 ? "mid" : "low";
  return `<span class="conf"><span class="conf-bar ${cls}"><span style="width:${Math.round(c * 100)}%"></span></span>${Math.round(c * 100)}</span>`;
}
function openStage(key, force) {
  if (st.open.has(key) && !force) st.open.delete(key); else st.open.add(key);
  st.tab = "activity";
  renderTab();
  if (force) $(`#ev-${key}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

const checklist = (checks) => `<ul class="checks">${(checks || []).map(([l, s]) => `<li><span class="ck ${s}">${s === "pass" ? "✓" : s === "fail" ? "✕" : s === "warn" ? "!" : "–"}</span><span>${esc(l)}</span></li>`).join("")}</ul>`;
const kv = (pairs) => `<dl class="kv">${pairs.filter(([, v]) => v !== undefined).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

function stageDetail(r) {
  const d = r.details || {};
  switch (r.stage) {
    case "intake": return kv([["File", esc(d.filename)], ["Size", d.size_kb + " KB"], ["Pages", d.pages], ["SHA-256", `<span class="mono">${esc(d.sha256)}…</span>`], ["Seen before", d.previously_seen ? `<a href="#/run/${esc(d.previously_seen)}" style="text-decoration:underline">${esc(d.previously_seen)}</a>` : "No"]]);
    case "read": return kv([["Method", d.method === "ocr" ? "OCR (image-only scan)" : "Native PDF text layer"], ["Lines recovered", d.lines], ["OCR confidence", d.ocr_confidence != null ? pct(d.ocr_confidence) : "n/a"]]);
    case "extract": { const f = d.fields || {}; return kv([["Engine", esc(f.engine === "rules" ? "Local layout-aware rules engine" : f.engine)], ["Fallback", d.engine_note ? esc(d.engine_note) : undefined], ["Located on page", `${Object.keys(f.evidence || {}).length} values (see Extracted fields)`]]); }
    case "validate": return checklist(d.checks) + (d.derived?.length ? `<div class="muted small" style="margin-top:8px">Derived, not printed: ${esc(d.derived.join(", "))}</div>` : "");
    case "vendor": return d.matched ? kv([["Vendor", `${esc(d.vendor_id)} · ${esc(d.vendor_name)}`], ["Matched on", Object.keys(d.signals || {}).map((s) => `<span class="code">${esc(s)}</span>`).join(" ")], ["Phone on file", esc(d.phone)]]) + `<div style="margin-top:10px">${checklist(d.checks)}</div>` : `<span class="muted">${esc(d.skipped || "Closest vendor: " + d.best_guess)}</span>`;
    case "duplicate": return d.skipped ? `<span class="muted">${esc(d.skipped)}</span>` : kv([["Normalised key", `<span class="mono">${esc(d.normalised_key)}</span>`]]) + `<div style="margin-top:10px">${checklist(d.checks)}</div>`;
    case "po_match": return poDetail(d);
    case "decide": return kv([["Decision", pill(d.decision)], ["Owner", esc(d.routed_to)], ["Blocking issues", (d.reasons || []).length]]);
    default: return "";
  }
}

function poDetail(d) {
  if (d.skipped) return `<span class="muted">${esc(d.skipped)}</span>`;
  let h = "";
  if (d.candidates?.length) {
    h += `<div class="subhead">Candidate POs (vendor's open POs)</div><table class="t tight"><tr><th>PO</th><th>Description</th><th class="right">Amount</th><th class="right">Score</th></tr>` +
      d.candidates.map((c, i) => `<tr><td class="mono nowrap">${esc(c.po_number)} ${i === 0 && d.po_number ? '<span class="pill pay" style="height:18px">chosen</span>' : ""}</td><td>${esc(c.description)}</td><td class="right num">${money(c.amount)}</td><td class="right num">${c.score.toFixed(2)}</td></tr>`).join("") + `</table>`;
  }
  if (!d.po_number) return h || '<span class="muted">No PO matched.</span>';
  h += `<div class="subhead">Purchase order</div>` + kv([["PO", `<span class="mono">${esc(d.po_number)}</span> · ${esc(d.po_description)}`], ["Matched", d.how === "inferred" ? "Inferred from line items" : "Reference printed on invoice"], ["Control", d.match_type === "3-way" ? "3-way: PO + invoice + goods receipt" : "2-way: PO + invoice"], ["Owner", esc(d.owner)], ["Cost centre", esc(d.cost_center)]]);
  h += `<div class="subhead">Line match</div><div class="table-wrap"><table class="t tight fields"><tr><th>Invoice line</th><th class="right">Qty</th><th class="right">Price</th><th class="right">PO price</th>${d.match_type === "3-way" ? '<th class="right">Rcvd</th>' : ""}<th>Result</th></tr>` +
    d.lines.map((l, i) => {
      const cls = l.status === "ok" ? "pay" : l.status === "milestone / partial" || l.status === "not on PO" ? "neutral" : "hold";
      return `<tr data-ev="line_${i}"><td style="color:var(--ink)">${esc(l.description)}${l.po_line ? `<div class="faint small">PO line ${l.po_line}</div>` : ""}</td><td class="right num">${l.qty ?? "—"}${l.po_qty ? `<span class="faint">/${l.po_qty}</span>` : ""}</td><td class="right num">${money(l.unit_price)}</td><td class="right num">${l.po_unit_price != null ? money(l.po_unit_price) : "—"}</td>${d.match_type === "3-way" ? `<td class="right num">${l.qty_received ?? "—"}</td>` : ""}<td><span class="pill ${cls}">${esc(l.status)}</span></td></tr>`;
    }).join("") + `</table></div>`;
  const x = d.header, base = Math.max(x.po_amount, x.previously_billed + x.this_invoice_net), w = (v) => Math.max(0, (100 * v) / base) + "%";
  const within = Math.min(x.this_invoice_net, Math.max(0, x.remaining_before)), over = Math.max(0, x.this_invoice_net - Math.max(0, x.remaining_before));
  h += `<div class="subhead">PO consumption · <span class="num">${money(x.po_amount)}</span></div>
    <div class="bill-bar"><span class="b-prev" style="width:${w(x.previously_billed)}"></span><span class="b-this" style="width:${w(within)}"></span><span class="b-over" style="width:${w(over)}"></span></div>
    <div class="legend"><span><i style="background:#a1a1aa"></i>Billed before <span class="num">${money(x.previously_billed)}</span>${x.prior_invoices.length ? ` (${esc(x.prior_invoices.join(", "))})` : ""}</span><span><i style="background:var(--ink)"></i>This invoice <span class="num">${money(x.this_invoice_net)}</span></span>${over > 0.005 ? `<span><i style="background:#ef4444"></i>Over PO <span class="num">${money(over)}</span></span>` : `<span>Remaining after <span class="num">${money(x.remaining_after)}</span></span>`}<span>Tax ${money(d.tax.billed)} vs expected ${money(d.tax.expected)} ${d.tax.ok ? "✓" : "✕"}</span></div>`;
  return h;
}

/* --------------------------------------------------------------- invoices */
async function renderInvoices() {
  setTop(`<b>Invoices</b>`, `<a class="btn btn-sm btn-dark" href="#/upload">${I.up} Upload invoice</a>`);
  const f = st.filter || "ALL", q = st.q || "";
  const [s, runs] = await Promise.all([api("/api/stats"), api("/api/runs")]);
  const fs = s.final_status || {}, n = s.runs;
  const shown = runs.filter((r) => (f === "ALL" || r.final_status === f) && (!q || [r.id, r.vendor_name, r.invoice_number, r.po_number].join(" ").toLowerCase().includes(q.toLowerCase())));
  const seg = (k, l, c) => `<button class="${f === k ? "active" : ""}" data-f="${k}">${l}<span class="n">${c}</span></button>`;
  const maxR = Math.max(1, ...s.top_reasons.map((r) => r[1]));
  const roi = s.roi || {};
  view.innerHTML = `
    <div class="ph"><div><h1>Invoices</h1><p>Every invoice received, what the process decided, and what's waiting on a person.</p></div></div>
    <div class="metrics">
      <div class="metric"><div class="k">Processed</div><div class="v">${n}</div><div class="s num">${money(s.value_processed)}</div></div>
      <div class="metric"><div class="k">Straight-through</div><div class="v">${s.straight_through_rate == null ? "—" : pct(s.straight_through_rate)}</div><div class="s">no human touch</div></div>
      <div class="metric"><div class="k">On hold</div><div class="v" style="color:${s.pending_review ? "var(--hold)" : "inherit"}">${s.pending_review}</div><div class="s num">${money(s.value_on_hold)}</div></div>
      <div class="metric"><div class="k">Ready to pay</div><div class="v">${fs.APPROVED || 0}</div><div class="s num">${money(s.value_approved)}</div></div>
      <div class="metric"><div class="k">Avg. time to decision</div><div class="v">${s.avg_duration_ms ? (s.avg_duration_ms / 1000).toFixed(1) + "s" : "—"}</div><div class="s">vs ~12 min manual</div></div>
    </div>
    <div class="inbox-grid">
      <div class="panel">
        <div class="panel-h"><div class="seg">${seg("ALL", "All", n)}${seg("NEEDS_REVIEW", "On hold", fs.NEEDS_REVIEW || 0)}${seg("APPROVED", "Ready to pay", fs.APPROVED || 0)}${seg("REJECTED", "Rejected", fs.REJECTED || 0)}</div>
          <span class="grow"></span><input class="search" id="q" placeholder="Search vendor, invoice, PO…" value="${esc(q)}"></div>
        ${shown.length ? `<div class="table-wrap"><table class="t"><tr><th>Vendor / invoice</th><th>PO</th><th class="right">Amount</th><th>Status</th><th>Owner</th><th class="right">Received</th></tr>
          ${shown.map((r) => `<tr class="click" data-id="${esc(r.id)}">
            <td><div style="font-weight:500">${esc(r.vendor_name || r.filename)}</div><div class="mono faint" style="font-size:11.5px">${esc(r.invoice_number || "—")} · ${esc(r.id)}</div></td>
            <td class="mono nowrap">${esc(r.po_number || "—")}</td>
            <td class="right num nowrap">${money(r.total)}</td>
            <td>${r.status === "running" ? pill(null) : pill(r.final_status)}${r.review ? `<div class="faint small" style="margin-top:3px">by ${esc(r.review.reviewer)}</div>` : ""}</td>
            <td class="small" style="max-width:230px">${r.status === "running" ? "" : r.final_status === "NEEDS_REVIEW" ? esc(r.result?.routed_to || "") : r.final_status === "APPROVED" ? '<span class="muted">Payment run</span>' : '<span class="muted">Closed</span>'}
              ${(r.result?.reason_codes || []).length && r.final_status !== "APPROVED" ? `<div style="margin-top:3px">${r.result.reason_codes.slice(0, 2).map((c) => `<span class="code">${esc(c)}</span>`).join(" ")}</div>` : ""}</td>
            <td class="right muted small nowrap">${ago(r.created_at)}</td></tr>`).join("")}</table></div>`
          : `<div class="empty"><b>${runs.length ? "Nothing matches this view" : "No invoices yet"}</b>${runs.length ? "" : `<a href="#/upload" style="text-decoration:underline">Upload one</a> or run the test scenarios.`}</div>`}
      </div>
      <div>
        <div class="roi">
          <div class="eyebrow">Value delivered</div>
          <div class="big">${(Math.round((roi.minutes_saved || 0) / 6) / 10).toFixed(1)} h</div>
          <div class="sub">AP time saved on ${n} invoice${n === 1 ? "" : "s"}</div>
          <div class="roi-grid"><div><b>${money(roi.cost_saved || 0)}</b><span>labour cost avoided</span></div><div><b>${moneyShort(roi.risk_value_stopped || 0)}</b><span>duplicate / fraud payments stopped</span></div></div>
          ${n ? (() => { const stp = s.straight_through_rate || 0; const hrs = Math.round((1000 * (12 - (1 - stp) * 4)) / 60); return `<div class="roi-grid"><div style="grid-column:1/-1"><span>At 1,000 invoices / month and this straight-through rate</span><b>${hrs.toLocaleString()} h · ${money(hrs * 45).replace(".00", "")} saved per month</b></div></div>`; })() : ""}
          <div class="fine">Assumes ${esc(roi.assumptions || "")}.</div>
        </div>
        <div class="panel">
          <div class="panel-h"><h3>Why invoices were stopped</h3></div>
          <div class="panel-b">
            ${n ? `<div class="mix">${["APPROVED", "NEEDS_REVIEW", "REJECTED"].map((k) => (fs[k] ? `<span style="width:${(100 * fs[k]) / n}%;background:${{ APPROVED: "#16a34a", NEEDS_REVIEW: "#f59e0b", REJECTED: "#ef4444" }[k]}"></span>` : "")).join("")}</div>` : ""}
            ${s.top_reasons.length ? `<div class="reasons">${s.top_reasons.map(([c, k]) => `<div class="reason"><div><span class="code">${esc(c)}</span><div class="track"><div class="fill ${["DUPLICATE_INVOICE", "UNKNOWN_VENDOR", "VENDOR_BLOCKED"].includes(c) ? "rej" : ""}" style="width:${(100 * k) / maxR}%"></div></div></div><span class="num right">${k}</span></div>`).join("")}</div>` : '<div class="muted small">Nothing stopped yet.</div>'}
          </div>
        </div>
      </div>
    </div>`;
  $$("tr.click").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/run/${tr.dataset.id}`)));
  $$("[data-f]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.f; renderInvoices(); }));
  const qi = $("#q");
  qi.addEventListener("input", () => { st.q = qi.value; clearTimeout(st.qt); st.qt = setTimeout(() => renderInvoices().then(() => { const e = $("#q"); e.focus(); e.setSelectionRange(e.value.length, e.value.length); }), 250); });
  refreshBadge();
  clearInterval(st.poll);
  if (runs.some((r) => r.status === "running")) st.poll = setInterval(() => { if (location.hash.startsWith("#/invoices") && document.activeElement?.id !== "q") renderInvoices(); }, 2000);
}

/* ------------------------------------------------------------- reference */
async function renderReference() {
  setTop(`<b>Vendors &amp; POs</b>`);
  const d = await api("/api/reference");
  const tab = st.refTab || "pos";
  const tabs = [["pos", "Purchase orders"], ["vendors", "Vendor master"], ["ledger", "AP ledger"], ["receipts", "Goods receipts"], ["policy", "AP policy"]];
  let body = "";
  if (tab === "pos") body = `<table class="t"><tr><th>PO</th><th>Vendor</th><th>Description</th><th>Control</th><th>Owner</th><th class="right">Amount</th><th>Billed</th><th class="right">Remaining</th><th>Status</th></tr>${d.purchase_orders.map((p) => { const r = p.billed / p.amount; return `<tr><td class="mono nowrap">${esc(p.po_number)}</td><td>${esc(p.vendor_name)}</td><td>${esc(p.description)}<div class="faint small">${p.lines.map((l) => `${l.qty} × ${esc(l.description)} @ ${money(l.unit_price)}`).join(" · ")}</div></td><td><span class="tag">${esc(p.match_type)}</span></td><td class="small">${esc(p.owner)}</td><td class="right num">${money(p.amount)}</td><td class="nowrap"><span class="prog"><span class="${r > 1.0001 ? "over" : ""}" style="width:${Math.min(100, r * 100)}%"></span></span> <span class="num small">${Math.round(r * 100)}%</span>${p.on_hold ? `<div class="small" style="color:var(--hold)">${money(p.on_hold)} on hold</div>` : ""}</td><td class="right num nowrap" style="${p.remaining < 0 ? "color:var(--rej)" : ""}">${money(p.remaining)}</td><td>${p.status === "open" ? '<span class="pill pay">Open</span>' : '<span class="pill neutral">Closed</span>'}</td></tr>`; }).join("")}</table>`;
  else if (tab === "vendors") body = `<table class="t"><tr><th>ID</th><th>Name</th><th>Tax ID</th><th>Status</th><th>Terms</th><th>Contact</th><th>Phone</th><th>Bank on file</th></tr>${d.vendors.map((v) => `<tr><td class="mono">${esc(v.vendor_id)}</td><td style="font-weight:500">${esc(v.name)}</td><td class="mono">${esc(v.tax_id)}</td><td>${v.status === "approved" ? '<span class="pill pay">Approved</span>' : '<span class="pill rej">Blocked</span>'}</td><td>${esc(v.payment_terms)}</td><td>${esc(v.contact_email)}</td><td class="small nowrap">${esc(v.phone)}</td><td class="small">${esc(v.bank_name)} <span class="mono">${esc(v.bank_account)}</span></td></tr>`).join("")}</table>`;
  else if (tab === "ledger") body = `<table class="t"><tr><th>Vendor</th><th>Invoice</th><th>Normalised key</th><th>Date</th><th>PO</th><th class="right">Net</th><th class="right">Total</th><th>Status</th><th>Source</th></tr>${d.ledger.map((l) => `<tr><td class="mono">${esc(l.vendor_id)}</td><td class="mono nowrap">${esc(l.invoice_number)}</td><td class="mono faint">${esc(l.invoice_key)}</td><td class="nowrap">${esc(l.invoice_date)}</td><td class="mono nowrap">${esc(l.po_number || "—")}</td><td class="right num">${money(l.subtotal)}</td><td class="right num">${money(l.total)}</td><td><span class="pill ${l.status === "paid" || l.status === "approved" ? "pay" : l.status === "on_hold" ? "hold" : "rej"}">${esc(l.status.replace("_", " "))}</span></td><td>${l.source.startsWith("RUN-") ? `<a class="mono" style="text-decoration:underline" href="#/run/${esc(l.source)}">${esc(l.source)}</a>` : `<span class="muted">${esc(l.source)}</span>`}</td></tr>`).join("")}</table>`;
  else if (tab === "receipts") body = `<table class="t"><tr><th>GRN</th><th>PO</th><th>PO line</th><th class="right">Qty received</th><th>Date</th></tr>${d.receipts.map((r) => `<tr><td class="mono">${esc(r.grn)}</td><td class="mono">${esc(r.po_number)}</td><td>${r.line}</td><td class="right num">${r.qty_received}</td><td>${esc(r.receipt_date)}</td></tr>`).join("")}</table>`;
  else { const p = d.policy; body = `<div class="panel-b">${kv([["Header tolerance", `Over the open PO balance by ≤ ${p.header_tolerance_pct}% <b>and</b> ≤ ${money(p.header_tolerance_abs)} is accepted`], ["Line price tolerance", `±${p.line_price_tolerance_pct}% vs PO unit price`], ["Tax rate tolerance", `±${p.tax_rate_tolerance_pp} pp vs PO rate`], ["Auto-approval limit", `${money(p.auto_approve_limit)}; above this the Finance Controller always approves`], ["Max invoice age", `${p.max_invoice_age_days} days`], ["Fuzzy duplicate window", `${p.duplicate_fuzzy_window_days} days, same vendor + same amount`], ["Min field confidence", pct(p.min_field_confidence)], ["Min OCR confidence", pct(p.min_ocr_confidence)], ["Inferred PO", `score ≥ ${p.inferred_po_min_score} and ≥ 0.25 ahead of the next candidate`], ["Currency", p.currency]])}</div>`; }
  view.innerHTML = `<div class="ph"><div><h1>Vendors &amp; POs</h1><p>The procurement data every invoice is checked against. It stands in for the ERP. Billed amounts update as invoices are approved.</p></div></div>
    <div class="panel"><div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${k === tab ? "active" : ""}" data-rt="${k}">${l}</button>`).join("")}</div><div class="table-wrap">${body}</div></div>`;
  $$("[data-rt]").forEach((b) => b.addEventListener("click", () => { st.refTab = b.dataset.rt; renderReference(); }));
}

/* ---------------------------------------------------------- how it works */
function renderHow() {
  setTop(`<b>How it works</b>`);
  const steps = [["Intake", "Fingerprint file, catch exact re-uploads"], ["Read", "PDF text layer, or OCR for scans"], ["Extract", "Fields + line items, with confidence and on-page location", 1], ["Validate", "Required fields, maths, dates, currency"], ["Vendor", "Master data, status, tax ID, bank account"], ["Duplicates", "Normalised and fuzzy match against the AP ledger"], ["PO match", "Explicit or inferred; lines, tolerance, split billing, receipts"], ["Decide", "Policy turns findings into decision, owner, actions"]];
  view.innerHTML = `<div class="ph"><div><h1>How it works</h1><p>The design decisions and assumptions behind the process.</p></div></div>
    <div class="flow" style="margin-bottom:18px">${steps.map(([t, p, ai], i) => `<div class="${ai ? "ai" : ""}"><div class="n">0${i + 1}</div><b>${t}</b><p>${p}</p></div>`).join("")}</div>
    <div class="cols2">
      <div class="panel"><div class="panel-b prose">
        <h3>Design principles</h3><ul>
        <li><b>AI reads, rules decide.</b> The model only turns a messy document into structured data. Approve / hold / reject is deterministic policy code, so every decision is auditable and reproducible.</li>
        <li><b>Show your work.</b> Every extracted value is located on the page, and every flag links to the exact spot that caused it. A reviewer verifies in seconds instead of re-reading the invoice.</li>
        <li><b>Fail safe, never silent.</b> Low confidence, missing data or a processing error goes to a named person with a specific reason. Reject only when paying would clearly be wrong.</li>
        <li><b>Route to the owner.</b> Fraud signals go to AP Manager + Treasury, PO problems to the PO owner, data issues to an AP specialist, high-value invoices to the Finance Controller.</li>
        <li><b>Demo-proof.</b> With an API key, extraction uses Claude. Without one, local OCR + a layout-aware parser run the same pipeline. The UI always shows which engine ran.</li></ul>
        <h3>Edge cases, and why these four</h3><ul>
        <li><b>Split-PO overbilling:</b> each invoice looks valid alone; only cumulative tracking across invoices catches it.</li>
        <li><b>Scanned, no PO:</b> the two most common real-world gaps together. Needs OCR and PO inference, with a guard against guessing.</li>
        <li><b>Disguised duplicate:</b> "past due" re-sends with reformatted numbers are a leading cause of duplicate payments.</li>
        <li><b>Bank-detail change:</b> the costliest AP failure (business-email compromise). Every number is right, so only a vendor-master cross-check catches it.</li></ul>
      </div></div>
      <div class="panel"><div class="panel-b prose">
        <h3>Beyond the brief</h3><ul>
        <li><b>Evidence highlighting.</b> Hover any field or flag to see where it came from on the invoice.</li>
        <li><b>Audit packet.</b> One click exports the full decision record (inputs, findings, policy, reviewer) as JSON.</li>
        <li><b>Value delivered.</b> Time and cost saved, and risky payments stopped, in terms a finance leader cares about.</li>
        <li><b>Human-in-the-loop.</b> Held invoices are approved or rejected in-app with a mandatory audit note.</li></ul>
        <h3>Assumptions</h3><ul>
        <li>Buyer is a fictional US manufacturer; currency USD. All vendors, POs and invoices are synthetic.</li>
        <li>CSVs stand in for the ERP (vendor master, POs, PO lines, goods receipts); the AP ledger is SQLite.</li>
        <li>Goods POs use a 3-way match; service POs a 2-way match.</li>
        <li>Overage within ≤2% and ≤$250 of the open PO balance is accepted (e.g. surcharges); beyond that the PO owner decides.</li>
        <li>A PO is inferred only on a unique, strong line-level match; otherwise a buyer picks.</li>
        <li>Bank changes are never accepted from an invoice; they need verification via the phone number on file.</li>
        <li>Invoices over $25,000 always need Finance Controller approval.</li></ul>
        <h3>What I'd build next</h3><ul>
        <li>Email-inbox intake with multi-invoice splitting.</li><li>ERP connector (NetSuite / SAP) with bill write-back.</li><li>Reviewer corrections fed back as per-vendor extraction hints.</li><li>SLA timers on held invoices to protect early-payment discounts.</li></ul>
      </div></div>
    </div>`;
}

/* ------------------------------------------------------------------ boot */
$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset demo data? This clears all runs and restores the AP ledger to its seeded state.")) return;
  await api("/api/reset", { method: "POST" });
  toast("Demo data reset");
  location.hash = "#/invoices"; router(); refreshBadge();
});
(async function boot() {
  [st.health, st.samples] = await Promise.all([api("/api/health"), api("/api/samples")]);
  $("#engine-label").textContent = st.health.engine === "claude" ? `Claude · ${st.health.model}` : "Local OCR + rules";
  refreshBadge();
  router();
})();
