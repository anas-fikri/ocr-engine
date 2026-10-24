/* Photo Quality Engine - Web UI (vanilla JS, tanpa build step). */
"use strict";

const KEY_STORE = "pqe_api_key";
const state = { me: null, tenants: [] };
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (s) => (s ? new Date(s).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" }) : "-");
const kb = (n) => (n == null ? "-" : n >= 1048576 ? (n / 1048576).toFixed(2) + " MB" : Math.round(n / 1024) + " KB");
const badge = (v, label) => `<span class="badge b-${esc(v || "pending")}">${esc(label || v || "pending")}</span>`;
const REVIEW_LABEL = { none: "belum direview", approved: "disetujui", retake_requested: "minta foto ulang", fraud: "kecurangan" };

function storedKey() { try { return localStorage.getItem(KEY_STORE); } catch { return null; } }
function saveKey(k) { try { k ? localStorage.setItem(KEY_STORE, k) : localStorage.removeItem(KEY_STORE); } catch { /* abaikan */ } }

async function api(path, opts = {}) {
  const headers = { "X-API-Key": storedKey() || "", "Accept-Language": "id", ...(opts.headers || {}) };
  let body = opts.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") { body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, { ...opts, headers, body });
  if (r.status === 401) { saveKey(null); showLogin("Sesi berakhir, silakan masuk lagi."); throw new Error("unauthorized"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error && data.error.message) || r.statusText);
  return data;
}

// Presigned URL gagal dimuat (browser/HP tidak bisa menjangkau host SILO): ambil lewat engine.
async function viaEngine(img) {
  if (img.dataset.retried) return;
  img.dataset.retried = "1";
  try {
    const r = await fetch(`/v1/photos/${img.dataset.photo}/file?variant=${img.dataset.variant}`, { headers: { "X-API-Key": storedKey() || "" } });
    if (r.ok) img.src = URL.createObjectURL(await r.blob());
  } catch { /* biarkan kosong */ }
}

// Thumbnail tidak disimpan di SILO: engine membuatnya saat diminta (butuh API key, jadi diambil lewat fetch).
const thumbObserver = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { thumbObserver.unobserve(e.target); loadThumb(e.target); }
}, { rootMargin: "200px" }) : null;
async function loadThumb(img) {
  try {
    const r = await fetch(`/v1/photos/${img.dataset.photo}/file?variant=thumbnail`, { headers: { "X-API-Key": storedKey() || "" } });
    if (r.ok) img.src = URL.createObjectURL(await r.blob());
    else img.replaceWith(Object.assign(document.createElement("div"), { className: "thumb none", textContent: "tidak ada file" }));
  } catch { /* biarkan kosong */ }
}
function loadThumbs(root = document) {
  root.querySelectorAll("img.thumb[data-photo]:not([src])").forEach((img) => (thumbObserver ? thumbObserver.observe(img) : loadThumb(img)));
}

// Judul halaman yang seragam: judul, penjelasan singkat, tombol/aksi di kanan.
const head = (title, desc = "", actions = "") =>
  `<div class="page-head"><div><h1>${title}</h1>${desc ? `<p class="muted">${desc}</p>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;

// Setiap halaman menandai field utamanya dengan data-autofocus; kursor langsung di sana.
function focusPage(root = $("#main")) {
  const el = root.querySelector("[data-autofocus]");
  if (el && !matchMedia("(hover: none)").matches) el.focus({ preventScroll: true }); // HP: jangan munculkan keyboard
}

async function loadVersion() {
  try {
    const v = (await (await fetch("/healthz")).json()).version;
    document.querySelectorAll("[data-version]").forEach((el) => (el.textContent = `v${v}`));
  } catch { /* abaikan */ }
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 4000);
}

function showLogin(msg = "") {
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); $("#login-error").textContent = msg;
  $("#login-key").focus();
}

async function boot() {
  if (!storedKey()) return showLogin();
  try { state.me = await api("/v1/me"); } catch (e) { return showLogin(e.message === "unauthorized" ? "" : e.message); }
  if (!["admin", "ops", "viewer"].includes(state.me.role)) { saveKey(null); return showLogin(`Role ${state.me.role} tidak bisa memakai Web UI.`); }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#who").textContent = `${state.me.name} (${state.me.role})`;
  document.querySelectorAll("#nav [data-role=admin]").forEach((a) => a.classList.toggle("hidden", state.me.role !== "admin"));
  try { state.tenants = await api("/v1/admin/tenants"); } catch { state.tenants = []; }
  route();
}

$("#login-form").addEventListener("submit", (e) => { e.preventDefault(); saveKey($("#login-key").value.trim()); loadVersion();
boot(); });
$("#logout").addEventListener("click", () => { saveKey(null); showLogin(); });
$("#drawer-close").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); toggleSide(false); } });
window.addEventListener("hashchange", route);
const toggleSide = (open) => { $("#side").classList.toggle("open", open); $("#side-backdrop").classList.toggle("hidden", !open); };
$("#menu").addEventListener("click", () => toggleSide(!$("#side").classList.contains("open")));
$("#side-backdrop").addEventListener("click", () => toggleSide(false));

const pages = { dashboard, review, photos, evidence, test: testPhoto, tenants, keys, silo, nas, system };
function route() {
  const [path, query] = location.hash.replace(/^#\//, "").split("?");
  const page = path || "dashboard";
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  if (page === "photos" && query) photos.f = params; // contoh: #/photos?awb=AWB123&id=<photo_id>
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  const link = $(`#nav a[data-page="${page}"]`);
  $("#crumb").textContent = link ? link.textContent : "";
  toggleSide(false);
  const fn = pages[page] || dashboard;
  $("#main").innerHTML = `<div class="empty">Memuat...</div>`;
  fn().then(() => { focusPage(); loadThumbs(); if (params.id) openPhoto(params.id); })
    .catch((e) => { if (e.message !== "unauthorized") $("#main").innerHTML = `<div class="card error">${esc(e.message)}</div>`; });
}

const tenantOptions = (sel = "", all = true) =>
  (all ? `<option value="">Semua tenant</option>` : "") +
  state.tenants.map((t) => `<option value="${esc(t.code)}" ${t.code === sel ? "selected" : ""}>${esc(t.name)} (${esc(t.code)})</option>`).join("");

/* ---------------- Dashboard ---------------- */
async function dashboard() {
  const f = dashboard.f || (dashboard.f = { tenant: "", days: 14 });
  const q = new URLSearchParams({ days: f.days, ...(f.tenant ? { tenant: f.tenant } : {}) });
  const s = await api("/v1/stats?" + q);
  const total = s.total || 0;
  const pct = (n) => (total ? Math.round((100 * (n || 0)) / total) : 0);
  const v = s.by_verdict || {};
  const max = Math.max(1, ...s.daily.map((d) => d.PASS + d.WARN + d.REJECT + d.pending));
  const bar = (n, color) => `<span style="height:${(100 * n) / max}%;background:${color}" title="${n}"></span>`;
  const list = (rows, key) => rows.length ? `<table>${rows.map((r) => `<tr><td>${esc(r[key])}</td><td style="text-align:right">${r.count}</td></tr>`).join("")}</table>` : `<p class="muted">Belum ada data.</p>`;
  $("#main").innerHTML = head("Dashboard", "Ringkasan kualitas foto yang dinilai engine.", `
      <label>Tenant<select id="f-tenant" data-autofocus>${tenantOptions(f.tenant)}</select></label>
      <label>Periode<select id="f-days">${[1, 7, 14, 30, 90].map((d) => `<option ${d == f.days ? "selected" : ""} value="${d}">${d} hari</option>`).join("")}</select></label>`) + `
    <div class="grid cols-4">
      <div class="card kpi"><div class="v">${total}</div><div class="l">Foto diterima</div></div>
      <div class="card kpi"><div class="v" style="color:var(--pass)">${pct(v.PASS)}%</div><div class="l">Layak (PASS) - ${v.PASS || 0}</div></div>
      <div class="card kpi"><div class="v" style="color:var(--warn)">${pct(v.WARN)}%</div><div class="l">Kurang ideal (WARN) - ${v.WARN || 0}</div></div>
      <div class="card kpi"><div class="v" style="color:var(--reject)">${pct(v.REJECT)}%</div><div class="l">Tidak layak (REJECT) - ${v.REJECT || 0}</div></div>
      <div class="card kpi"><div class="v"><a href="#/review">${s.review_open}</a></div><div class="l">Menunggu review ops</div></div>
    </div>
    <div class="card">
      <h2>Tren harian</h2>
      <div class="legend"><span><i class="dot" style="background:var(--pass)"></i>PASS</span><span><i class="dot" style="background:var(--warn)"></i>WARN</span><span><i class="dot" style="background:var(--reject)"></i>REJECT</span><span><i class="dot" style="background:#cbd2db"></i>diproses</span></div>
      ${s.daily.length ? `<div class="chart">${s.daily.map((d) => `<div class="col">${bar(d.PASS, "var(--pass)")}${bar(d.WARN, "var(--warn)")}${bar(d.REJECT, "var(--reject)")}${bar(d.pending, "#cbd2db")}</div>`).join("")}</div>
      <div class="chart-labels">${s.daily.map((d) => `<span>${esc(d.date.slice(5))}</span>`).join("")}</div>` : `<p class="muted">Belum ada foto pada periode ini.</p>`}
    </div>
    <div class="grid cols-2">
      <div class="card"><h2>Alasan terbanyak</h2>${s.top_reasons.length ? `<table>${s.top_reasons.map((r) => `<tr><td><a href="#/photos" data-reason="${esc(r.code)}">${esc(r.code)}</a></td><td style="text-align:right">${r.count}</td></tr>`).join("")}</table>` : `<p class="muted">Belum ada data.</p>`}</div>
      <div class="card"><h2>Kurir dengan foto bermasalah terbanyak</h2>${list(s.top_couriers, "courier_id")}</div>
      <div class="card"><h2>Cabang dengan foto bermasalah terbanyak</h2>${list(s.top_branches, "branch")}</div>
    </div>`;
  $("#f-tenant").onchange = (e) => { f.tenant = e.target.value; dashboard(); };
  $("#f-days").onchange = (e) => { f.days = e.target.value; dashboard(); };
  document.querySelectorAll("[data-reason]").forEach((a) => a.addEventListener("click", () => { photos.f = { reason: a.dataset.reason }; }));
}

/* ---------------- Daftar foto (dipakai Review & Cari) ---------------- */
function photoTable(items) {
  if (!items.length) return `<div class="empty">Tidak ada foto yang cocok dengan filter.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th></th><th>Resi</th><th class="hide-sm">Tenant / jenis</th><th class="hide-sm">Kurir</th><th>Verdict</th><th>Alasan utama</th><th class="hide-sm">Review</th><th class="hide-sm">Diterima</th></tr></thead><tbody>
    ${items.map((p) => {
      const main = (p.reasons || []).find((r) => r.severity !== "info");
      return `<tr class="clickable" data-id="${esc(p.id)}">
        <td>${p.object || p.archive ? `<img class="thumb" data-photo="${esc(p.id)}" alt="">` : `<div class="thumb none" title="Arsip belum tersimpan / lewat masa simpan, dan aplikasi tidak melaporkan lokasi">tidak ada file</div>`}</td>
        <td><b>${esc(p.awb || "-")}</b><div class="muted small">${esc(p.external_id || "")}</div></td>
        <td class="hide-sm">${esc(p.tenant)}<div class="muted small">${esc(p.photo_type || "-")}</div></td>
        <td class="hide-sm">${esc(p.courier_id || "-")}<div class="muted small">${esc(p.branch || "")}</div></td>
        <td>${p.status === "error" ? badge("REJECT", "ERROR") : badge(p.verdict)}${p.retake_required ? `<div class="small" style="color:var(--reject)">foto ulang</div>` : ""}</td>
        <td>${main ? `${esc(main.message)}<div class="muted small">${esc(main.code)}</div>` : `<span class="muted">-</span>`}</td>
        <td class="hide-sm">${badge(p.review.status, REVIEW_LABEL[p.review.status])}</td>
        <td class="small hide-sm">${fmtDate(p.received_at)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

async function listPage(title, desc, f, extraFilters, fixed) {
  const params = { limit: 50, offset: f.offset || 0, ...fixed };
  for (const k of ["tenant", "awb", "courier_id", "branch", "verdict", "status", "review_status", "reason", "date_from", "date_to"]) if (f[k]) params[k] = f[k];
  const data = await api("/v1/photos?" + new URLSearchParams(params));
  const sel = (id, label, opts) => `<label>${label}<select id="${id}">${opts.map(([v, l]) => `<option value="${v}" ${f[id.slice(2)] === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>`;
  $("#main").innerHTML = head(title, desc) + `
    <form class="filters card" id="f-form">
      <label class="primary">Resi<input id="f-awb" value="${esc(f.awb || "")}" placeholder="AWB..." data-autofocus></label>
      <label>Tenant<select id="f-tenant">${tenantOptions(f.tenant)}</select></label>
      <label>Kurir<input id="f-courier_id" value="${esc(f.courier_id || "")}"></label>
      <label>Cabang<input id="f-branch" value="${esc(f.branch || "")}"></label>
      ${sel("f-verdict", "Verdict", [["", "Semua"], ["PASS", "PASS"], ["WARN", "WARN"], ["REJECT", "REJECT"]])}
      <label>Kode alasan<input id="f-reason" value="${esc(f.reason || "")}" placeholder="BLURRY"></label>
      ${extraFilters}
      <div class="actions"><button type="submit">Terapkan</button><button type="button" class="secondary" id="f-reset">Reset</button>
        <button type="button" class="secondary only-sm" id="f-more">Filter lainnya</button></div>
    </form>
    <div class="card">${photoTable(data.items)}
      <div class="pager"><button class="secondary" id="prev" ${!params.offset ? "disabled" : ""}>Sebelumnya</button>
      <span class="muted">${data.total ? `${params.offset + 1}-${Math.min(params.offset + 50, data.total)} dari ${data.total}` : "0"}</span>
      <button class="secondary" id="next" ${params.offset + 50 >= data.total ? "disabled" : ""}>Berikutnya</button></div></div>`;
  const rerun = () => (location.hash.startsWith("#/review") ? review() : photos()).then(() => { focusPage(); loadThumbs(); });
  $("#f-form").onsubmit = (e) => {
    e.preventDefault();
    for (const el of document.querySelectorAll("#f-form [id^=f-]")) if (el.tagName !== "BUTTON") f[el.id.slice(2)] = el.value.trim();
    f.offset = 0; rerun();
  };
  $("#f-reset").onclick = () => { for (const k of Object.keys(f)) delete f[k]; rerun(); };
  const more = Object.keys(f).some((k) => !["awb", "offset"].includes(k) && f[k]);
  $("#f-form").classList.toggle("expanded", more);
  $("#f-more").onclick = () => $("#f-form").classList.toggle("expanded");
  $("#prev").onclick = () => { f.offset = Math.max(0, params.offset - 50); rerun(); };
  $("#next").onclick = () => { f.offset = params.offset + 50; rerun(); };
  document.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openPhoto(tr.dataset.id, rerun)));
}

async function review() {
  const f = review.f || (review.f = {});
  await listPage("Antrean review", "Foto yang perlu keputusan ops. Klik baris untuk melihat foto dan memutuskan.", f, "", { needs_review: true });
}

async function photos() {
  const f = photos.f || (photos.f = {});
  const extra = `
    <label>Status review<select id="f-review_status">${[["", "Semua"], ["none", "belum direview"], ["approved", "disetujui"], ["retake_requested", "minta foto ulang"], ["fraud", "kecurangan"]].map(([v, l]) => `<option value="${v}" ${f.review_status === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>Dari<input type="date" id="f-date_from" value="${esc(f.date_from || "")}"></label>
    <label>Sampai<input type="date" id="f-date_to" value="${esc(f.date_to || "")}"></label>`;
  await listPage("Cari foto", "Cari berdasarkan resi, kurir, cabang, verdict, atau alasan.", f, extra, {});
}

/* ---------------- Detail foto ---------------- */
function closeDrawer() { $("#drawer").classList.add("hidden"); }

function scoreBars(scores) {
  const color = (v) => (v >= 55 ? "var(--pass)" : v >= 30 ? "var(--warn)" : "var(--reject)");
  return Object.entries(scores || {}).filter(([k]) => k !== "authenticity_risk").map(([k, v]) =>
    `<div class="score-row"><span>${esc(k)}</span><div class="bar"><span style="width:${v}%;background:${color(v)}"></span></div><b>${Math.round(v)}</b></div>`).join("");
}

function reasonsHtml(reasons) {
  if (!reasons || !reasons.length) return `<p class="muted">Tidak ada catatan.</p>`;
  return reasons.map((r) => `<div class="reason ${esc(r.severity)}"><b>${esc(r.message)}</b> ${badge(r.severity === "info" ? "info" : r.severity === "reject" ? "REJECT" : "WARN", r.severity)}
    <span class="muted small">${esc(r.code)}${r.score != null ? ` · skor ${Math.round(r.score)}` : ""}</span>
    ${r.severity !== "info" && r.suggestion ? `<div class="small">${esc(r.suggestion)}</div>` : ""}</div>`).join("");
}

async function openPhoto(id, onChange) {
  const p = await api(`/v1/photos/${id}`);
  const canReview = ["admin", "ops"].includes(state.me.role);
  const c = p.compression || {};
  const kv = (rows) => `<div class="kv">${rows.map(([k, v]) => `<div>${esc(k)}</div><div>${v ?? "-"}</div>`).join("")}</div>`;
  $("#drawer-body").innerHTML = `
    <h1>${esc(p.awb || "(tanpa resi)")} ${p.status === "error" ? badge("REJECT", "ERROR") : badge(p.verdict)} ${badge(p.review.status, REVIEW_LABEL[p.review.status])}</h1>
    <p class="muted">${esc(p.tenant)} · profile ${esc(p.profile || "-")} · aksi mode: <b>${esc(p.action || "-")}</b></p>
    <div class="grid cols-2">
      <div class="images">
        ${p.urls.processed ? `<a href="${esc(p.urls.processed)}" target="_blank"><img src="${esc(p.urls.processed)}" data-photo="${esc(p.id)}" data-variant="processed" onerror="viaEngine(this)" alt="foto"></a>`
          : `<div class="empty card">Tidak ada file foto.<div class="small">Arsip engine belum tersimpan atau sudah lewat masa simpan, dan aplikasi tidak melaporkan lokasinya.</div></div>`}
        ${p.urls.processed && p.archive && p.verdict === "REJECT" && !p.object ? `<div class="small muted">Foto asli yang ditolak (arsip engine, untuk review & kalibrasi). Tidak disimpan aplikasi.</div>` : ""}
        <div class="small">${p.urls.processed ? `<a href="${esc(p.urls.processed)}" target="_blank">Buka foto</a>` : ""}${p.awb ? `${p.urls.processed ? " · " : ""}<a href="#" data-evidence="${esc(p.awb)}">Evidence PDF resi ini</a>` : ""}</div>
      </div>
      <div>
        ${p.retake_required ? `<div class="reason reject"><b>Wajib foto ulang</b><div class="small">${esc(p.retake_message || "")}</div></div>` : ""}
        ${p.enhancement && p.enhancement.applied ? `<div class="reason info"><b>Diperbaiki otomatis</b> <span class="small">(kurva pencahayaan global, terverifikasi tanpa perubahan isi: ${esc((p.enhancement.operations || []).join(", "))})</span>
          <div class="small">Sebelum perbaikan: ${badge(p.enhancement.before.verdict)} ${esc((p.enhancement.before.codes || []).join(", "))}. Foto original tetap disimpan utuh.</div></div>` : ""}
        <h3>Skor</h3>${scoreBars(p.scores)}
        <h3>Catatan</h3>${reasonsHtml(p.reasons)}
        ${p.error ? `<div class="reason reject"><b>${esc(p.error.code)}</b><div class="small">${esc(p.error.message)}</div></div>` : ""}
        ${canReview ? `<h3>Keputusan ops</h3>
          <textarea id="rv-note" rows="2" style="width:100%" placeholder="Catatan (opsional)" data-autofocus>${esc(p.review.note || "")}</textarea>
          <div class="actions">
            <button data-act="approve">Setujui (layak tagih)</button>
            <button class="secondary" data-act="request_retake">Minta foto ulang</button>
            <button class="secondary" data-act="mark_fraud">Tandai kecurangan</button>
            <button class="secondary" data-act="reset">Reset</button>
            <button class="secondary" id="rv-reprocess">Analisis ulang</button>
          </div>` : ""}
      </div>
    </div>
    <h3>Data pengiriman</h3>${kv([["Kurir", esc(p.courier_id)], ["Cabang", esc(p.branch)], ["Pelanggan", esc(p.customer_id)], ["ID eksternal", esc(p.external_id)],
      ["Waktu foto", fmtDate(p.captured_at)], ["Diterima", fmtDate(p.received_at)], ["Dianalisis", fmtDate(p.analyzed_at)],
      ["Lokasi", p.location ? `<a target="_blank" href="https://maps.google.com/?q=${p.location.lat},${p.location.lng}">${p.location.lat}, ${p.location.lng}</a>` : "-"],
      ["Sumber", esc(p.ingest_source)], ["Arsip engine", p.archive ? `<code>${esc(p.archive.bucket)}/${esc(p.archive.key)}</code>` : "-"],
      ["Lokasi di aplikasi", p.object ? `<code>${esc(p.object.bucket)}/${esc(p.object.key)}</code>` : "tidak dilaporkan aplikasi"]])}
    <h3>Gambar & kompresi</h3>${kv([["Format", esc((p.image || {}).format)], ["Dimensi", p.image && p.image.width ? `${p.image.width} × ${p.image.height}` : "-"],
      ["Ukuran", c.original_bytes ? `${kb(c.original_bytes)} → ${kb(c.output_bytes)} ${c.passthrough ? "(tidak perlu dikompres)" : `(quality ${c.quality}, SSIM ${c.ssim})`}` : "-"],
      ["Teks setelah kompres", c.text_guard ? `skor ${c.text_guard.before} → ${c.text_guard.after}${c.text_guard.raised ? " (kualitas dinaikkan agar teks tetap terbaca)" : " (tetap terbaca)"}` : "-"],
      ["Teks terbaca", p.metrics && p.metrics.ocr && p.metrics.ocr.words != null ? `${p.metrics.ocr.words} kata, confidence ${p.metrics.ocr.confidence}${p.metrics.ocr.awb_found ? " · nomor resi terbaca" : ""}` : "-"],
      ["SHA-256", `<code class="small">${esc(p.sha256)}</code>`]])}
    <h3>EXIF</h3>${Object.keys(p.exif || {}).length ? kv(Object.entries(p.exif).map(([k, v]) => [k, esc(v)])) : `<p class="muted">Tidak ada EXIF.</p>`}
    <h3>Riwayat review</h3><div id="rv-history" class="muted">Memuat...</div>
    <details><summary class="muted">Metrik mentah</summary><pre class="json">${esc(JSON.stringify(p.metrics, null, 2))}</pre></details>`;
  $("#drawer").classList.remove("hidden");
  focusPage($("#drawer-body"));
  if (!$("#drawer-body [data-autofocus]")) $("#drawer-close").focus();
  api(`/v1/photos/${id}/reviews`).then((rows) => {
    $("#rv-history").innerHTML = rows.length ? rows.map((r) => `<div>${fmtDate(r.at)} · <b>${esc(r.action)}</b> oleh ${esc(r.actor)} ${r.note ? `- ${esc(r.note)}` : ""}</div>`).join("") : "Belum ada.";
  }).catch(() => { $("#rv-history").textContent = "-"; });
  document.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await api(`/v1/photos/${id}/review`, { method: "POST", body: { action: b.dataset.act, note: $("#rv-note").value || null } });
      toast("Keputusan tersimpan"); closeDrawer(); onChange && onChange();
    } catch (e) { toast(e.message, true); }
  }));
  document.querySelectorAll("#drawer-body [data-evidence]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); evidencePdf([a.dataset.evidence]); }));
  const rp = $("#rv-reprocess");
  if (rp) rp.onclick = async () => { try { await api(`/v1/photos/${id}/reprocess`, { method: "POST" }); toast("Masuk antrean analisis ulang"); closeDrawer(); onChange && onChange(); } catch (e) { toast(e.message, true); } };
}

/* ---------------- Uji foto (preview, tidak disimpan) ---------------- */
async function testPhoto() {
  const builtin = await api("/v1/admin/profiles/builtin").catch(() => ({}));
  $("#main").innerHTML = head("Uji foto", "Analisis foto tanpa mencatat atau menyimpan apa pun. Untuk mencoba threshold atau melatih tim.") + `
    <form class="filters card" id="t-form">
      <label class="span-all">File foto<input id="t-file" type="file" accept="image/*" data-autofocus required></label>
      <label>Tenant<select id="t-tenant"><option value="">(profile bawaan)</option>${tenantOptions("", false)}</select></label>
      <label>Jenis foto / profile<select id="t-type">${Object.keys(builtin).map((p) => `<option>${esc(p)}</option>`).join("")}</select></label>
      <div class="actions"><button type="submit" id="t-run">Analisis</button></div>
    </form><div id="t-out"></div>`;
  $("#t-tenant").onchange = async (e) => {
    let names = Object.keys(builtin);
    if (e.target.value) {
      try { names = Object.keys((await api(`/v1/admin/tenants/${e.target.value}`)).resolved_profiles); } catch { /* pakai bawaan */ }
    }
    $("#t-type").innerHTML = names.map((p) => `<option>${esc(p)}</option>`).join("");
  };
  $("#t-file").onchange = () => $("#t-form").requestSubmit();
  $("#t-form").onsubmit = async (e) => {
    e.preventDefault();
    const file = $("#t-file").files[0];
    if (!file) return toast("Pilih file dulu", true);
    const fd = new FormData(); fd.append("file", file);
    const q = new URLSearchParams({ photo_type: $("#t-type").value, ...($("#t-tenant").value ? { tenant: $("#t-tenant").value } : {}) });
    $("#t-out").innerHTML = `<div class="empty">Menganalisis...</div>`;
    try {
      const r = await api("/v1/analyze/preview?" + q, { method: "POST", body: fd });
      const c = r.compression || {};
      $("#t-out").innerHTML = `<div class="card grid cols-2"><div><img src="${URL.createObjectURL(file)}" style="max-width:100%;border-radius:6px"></div><div>
        <h2>${badge(r.verdict)} <span class="muted small">profile ${esc(r.profile)} · mode ${esc(r.mode)} · aksi ${esc(r.action)}</span></h2>
        ${r.retake_required ? `<div class="reason reject"><b>Wajib foto ulang</b><div class="small">${esc(r.retake_message || "")}</div></div>` : ""}
        ${r.enhancement && r.enhancement.applied ? `<div class="reason info"><b>Diperbaiki otomatis</b> <span class="small">(${esc(r.enhancement.operations.join(", "))}; sebelum: ${esc(r.enhancement.before.verdict)})</span></div>` : ""}
        <h3>Skor</h3>${scoreBars(r.scores)}<h3>Catatan</h3>${reasonsHtml(r.reasons)}
        <h3>Kompresi</h3><p>${kb(c.original_bytes)} → ${kb(c.output_bytes)} ${c.passthrough ? "(tidak perlu dikompres)" : `(${c.width}×${c.height}, quality ${c.quality}, SSIM ${c.ssim}${c.target_met ? "" : ", target belum tercapai"})`}</p>
        <p class="muted small">Waktu proses ${r.timings_ms.total} ms</p>
        <details><summary class="muted">JSON lengkap</summary><pre class="json">${esc(JSON.stringify(r, null, 2))}</pre></details></div></div>`;
    } catch (e) { $("#t-out").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  };
}

/* ---------------- Tenant (admin) ---------------- */
const TENANT_TEMPLATE = `code: aplikasi-baru
name: Aplikasi Baru
default_lang: id
timezone: Asia/Jakarta
retention_days: 1095
default_profile: default
sources:
  - type: s3_poll
    bucket: nama-bucket
    prefix: "folder/"
    poll_interval_sec: 15
mapping:
  awb: ["meta:awb"]
  photo_type: ["meta:photo-type"]
sinks: []
profiles:
  default:
    mode: shadow
`;

async function tenants() {
  state.tenants = await api("/v1/admin/tenants");
  $("#main").innerHTML = head("Tenant", "Setiap aplikasi yang terhubung = satu tenant. Perubahan berlaku tanpa restart (maks. 5 detik). Referensi field: <code>docs/configuration.md</code>.",
    `<button id="tn-new" data-autofocus>Tenant baru</button>`) + `
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Kode</th><th>Nama</th><th>Versi</th><th>Source</th><th>Mode per profile</th><th>Status</th><th></th></tr></thead><tbody>
    ${state.tenants.map((t) => `<tr><td><b>${esc(t.code)}</b></td><td>${esc(t.name)}</td><td>v${t.version}</td><td>${esc((t.sources || []).join(", ") || "API saja")}</td>
      <td class="small">${Object.entries(t.modes || {}).map(([k, v]) => `${esc(k)}: <b>${esc(v)}</b>`).join(" · ")}</td>
      <td>${t.enabled ? badge("PASS", "aktif") : badge("REJECT", "nonaktif")}</td>
      <td><button class="secondary" data-edit="${esc(t.code)}">Ubah</button></td></tr>`).join("")}
    </tbody></table></div></div><div id="tn-editor"></div>`;
  document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => editTenant(b.dataset.edit)));
  $("#tn-new").onclick = () => editTenant(null);
}

async function editTenant(code) {
  const t = code ? await api(`/v1/admin/tenants/${code}`) : { config_yaml: TENANT_TEMPLATE, enabled: true };
  $("#tn-editor").innerHTML = `<div class="card" style="margin-top:16px"><h2>${code ? `Ubah ${esc(code)} (v${t.version})` : "Tenant baru"}</h2>
    <textarea id="tn-yaml" class="code" spellcheck="false">${esc(t.config_yaml)}</textarea>
    <div class="actions"><button class="secondary" id="tn-validate">Validasi</button><button id="tn-save">Simpan</button>
      ${code ? `<button class="secondary" id="tn-toggle">${t.enabled ? "Nonaktifkan" : "Aktifkan"}</button><button class="secondary" id="tn-history">Riwayat versi</button>` : ""}</div>
    <div id="tn-msg"></div></div>`;
  const yaml = () => $("#tn-yaml").value;
  const post = (url, method) => api(url, { method, body: yaml(), headers: { "Content-Type": "application/x-yaml" } });
  $("#tn-validate").onclick = async () => {
    try { const r = await post("/v1/admin/tenants/validate", "POST"); $("#tn-msg").innerHTML = `<p style="color:var(--pass)">Valid. Profile: ${Object.keys(r.resolved_profiles).map(esc).join(", ")}</p><details><summary class="muted">Profile hasil gabungan</summary><pre class="json">${esc(JSON.stringify(r.resolved_profiles, null, 2))}</pre></details>`; }
    catch (e) { $("#tn-msg").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  };
  $("#tn-save").onclick = async () => {
    try { const r = await post("/v1/admin/tenants", "PUT"); toast(`Tersimpan: ${r.code} v${r.version}`); await tenants(); editTenant(r.code); }
    catch (e) { $("#tn-msg").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  };
  if (code) {
    $("#tn-toggle").onclick = async () => { await api(`/v1/admin/tenants/${code}/${t.enabled ? "disable" : "enable"}`, { method: "POST" }); toast("Status tenant diubah"); tenants(); };
    $("#tn-history").onclick = async () => {
      const h = await api(`/v1/admin/tenants/${code}/history`);
      $("#tn-msg").innerHTML = h.map((x) => `<details><summary>v${x.version} · ${fmtDate(x.changed_at)} · ${esc(x.changed_by)}</summary><pre class="json">${esc(x.config_yaml)}</pre></details>`).join("");
    };
  }
  $("#tn-editor").scrollIntoView({ behavior: "smooth" });
  $("#tn-yaml").focus({ preventScroll: true });
}

/* ---------------- API key (admin) ---------------- */
async function keys() {
  const rows = await api("/v1/admin/api-keys");
  $("#main").innerHTML = head("API key", "Satu key per orang atau aplikasi. Key hanya ditampilkan sekali saat dibuat.") + `
    <div class="card"><h2>Buat key baru</h2><form class="filters" id="k-form">
      <label>Nama / pemakai<input id="k-name" placeholder="contoh: aplikasi-kurir-prod" data-autofocus required></label>
      <label>Role<select id="k-role"><option value="app">app (aplikasi)</option><option value="ops">ops</option><option value="viewer">viewer</option><option value="customer">customer (pelanggan)</option><option value="admin">admin</option></select></label>
      <label>Tenant<select id="k-tenant"><option value="">-</option>${tenantOptions("", false)}</select></label>
      <label>Customer ID<input id="k-cust" placeholder="untuk role customer"></label>
      <div class="actions"><button type="submit">Buat</button></div></form><div id="k-out"></div></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Nama</th><th>Prefix</th><th>Role</th><th>Tenant</th><th>Customer</th><th>Dibuat</th><th>Terakhir dipakai</th><th></th></tr></thead><tbody>
    ${rows.map((k) => `<tr><td>${esc(k.name)}</td><td><code>${esc(k.prefix)}…</code></td><td>${esc(k.role)}</td><td>${esc(k.tenant || "-")}</td><td>${esc(k.customer_id || "-")}</td>
      <td class="small">${fmtDate(k.created_at)}</td><td class="small">${fmtDate(k.last_used_at)}</td>
      <td>${k.revoked ? badge("REJECT", "dicabut") : `<button class="secondary" data-revoke="${esc(k.id)}">Cabut</button>`}</td></tr>`).join("")}</tbody></table></div></div>`;
  $("#k-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/v1/admin/api-keys", { method: "POST", body: { name: $("#k-name").value, role: $("#k-role").value, tenant: $("#k-tenant").value || null, customer_id: $("#k-cust").value || null } });
      $("#k-out").innerHTML = `<div class="keybox"><b>Simpan sekarang, key ini tidak bisa dilihat lagi:</b><br><code>${esc(r.api_key)}</code></div>`;
    } catch (e) { toast(e.message, true); }
  };
  document.querySelectorAll("[data-revoke]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Cabut API key ini? Aplikasi yang memakainya akan langsung ditolak.")) return;
    await api(`/v1/admin/api-keys/${b.dataset.revoke}`, { method: "DELETE" }); toast("Key dicabut"); keys();
  }));
}

/* ---------------- Pengaturan SILO (admin) ---------------- */
const SILO_FIELDS = [
  ["endpoint", "Endpoint (URL SILO)", "https://s3.pcpexpress.com", "Alamat yang diakses SERVER engine. Di Docker, 127.0.0.1 berarti container engine itu sendiri: pakai nama service (contoh http://silo:9000) atau IP/host server SILO."],
  ["public_endpoint", "Endpoint publik (opsional)", "https://s3.pcpexpress.com", "Alamat yang dipakai browser/pelanggan untuk membuka foto. Kosong = sama dengan Endpoint."],
  ["region", "Region", "us-east-1", "Wajib diisi untuk library S3; nilainya tidak berpengaruh di SILO."],
  ["access_key", "Access key", "", "Service account KHUSUS engine (jangan akun admin/root)."],
  ["secret_key", "Secret key", "", "Disimpan terenkripsi dan tidak pernah ditampilkan lagi. Kosongkan jika tidak ingin mengubah."],
  ["addressing_style", "Gaya alamat (path)", "", "auto | path | virtual. SILO/MinIO umumnya path (format mc: path \"on\")."],
  ["signature_version", "Signature (api)", "s3v4", "s3v4 (disarankan) atau s3 (v2, lama)."],
  ["app_bucket", "Bucket foto aplikasi", "ekas-pcptransport", "Tempat aplikasi menyimpan foto. Engine hanya MEMBACA (polling, Web UI); menulis/menghapus diblokir di kode."],
  ["archive_bucket", "Bucket arsip engine", "ocr", "Engine menyimpan salinan foto yang dinilai di sini, dipisah per aplikasi (prefix tenant). Diterima: foto hasil; ditolak: foto asli. Masa simpan 520 hari (atur per tenant: archive_retention_days)."],
];
const SOURCE_LABEL = { database: "dari halaman ini", env: "dari env server", default: "bawaan" };

async function silo() {
  const cfg = await api("/v1/admin/settings/storage");
  const f = cfg.fields;
  const val = (k) => (k === "secret_key" ? "" : f[k].value ?? "");
  $("#main").innerHTML = head("Koneksi SILO", "Engine menyimpan arsipnya sendiri di <b>bucket arsip</b> (bawaan <code>ocr</code>) dan hanya <b>membaca</b> bucket aplikasi. Aplikasi tetap menyimpan foto pengirimannya lewat jalurnya sendiri. Berlaku tanpa restart (maks. 10 detik); rahasia disimpan terenkripsi.") + `
    ${cfg.secret_key_configured ? "" : `<div class="reason warn"><b>PQE_SECRET_KEY belum diisi di server.</b> Secret key tidak bisa disimpan lewat halaman ini sampai PQE_SECRET_KEY diisi (lihat docs/configuration.md). Field lain tetap bisa disimpan.</div>`}
    <div class="grid cols-2">
      <div class="card"><h2>Koneksi</h2>
        ${SILO_FIELDS.map(([k, label, ph, help]) => `<label style="margin-bottom:10px">${esc(label)}
          ${k === "addressing_style" ? `<select id="s-${k}">${["", "auto", "path", "virtual"].map((o) => `<option value="${o}" ${val(k) === o ? "selected" : ""}>${o || "(ikut env)"}</option>`).join("")}</select>`
            : k === "signature_version" ? `<select id="s-${k}">${["s3v4", "s3"].map((o) => `<option ${val(k) === o ? "selected" : ""}>${o}</option>`).join("")}</select>`
            : `<input id="s-${k}" ${k === "endpoint" ? "data-autofocus" : ""} type="${k === "secret_key" ? "password" : "text"}" autocomplete="off" value="${esc(val(k))}" placeholder="${esc(k === "secret_key" ? (f.secret_key.value.set ? "•••••• (tersimpan)" : "belum diisi") : ph)}">`}
          <span class="small">${esc(help)} <i>Saat ini: ${esc(SOURCE_LABEL[f[k].source])}</i>${f[k].source === "database" ? ` · <a href="#" data-clear="${k}">kembalikan ke env</a>` : ""}</span></label>`).join("")}
        <div class="actions"><button class="secondary" id="s-test">Test koneksi</button><button id="s-save">Simpan</button></div>
        <div id="s-out"></div>
      </div>
      <div class="card"><h2>Impor dari konfigurasi mc / MinIO</h2>
        <p class="muted small">Tempel JSON dari <code>mc alias</code> / <code>~/.mc/config.json</code>. Form di samping akan terisi otomatis (belum disimpan).</p>
        <textarea id="s-mc" class="code" style="min-height:160px" placeholder='{"url":"http://127.0.0.1:9000","accessKey":"...","secretKey":"...","api":"s3v4","path":"auto"}'></textarea>
        <div class="actions"><button class="secondary" id="s-import">Isi form dari JSON</button></div>
        <h3>Permission minimal service account</h3>
        <pre class="json">${esc(JSON.stringify({ Version: "2012-10-17", Statement: [
          { Effect: "Allow", Action: ["s3:GetObject", "s3:ListBucket"], Resource: [`arn:aws:s3:::${val("app_bucket") || "ekas-pcptransport"}`, `arn:aws:s3:::${val("app_bucket") || "ekas-pcptransport"}/*`] },
          { Effect: "Allow", Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource: [`arn:aws:s3:::${val("archive_bucket") || "ocr"}`, `arn:aws:s3:::${val("archive_bucket") || "ocr"}/*`] },
        ] }, null, 2))}</pre>
        <p class="muted small">Baca saja di bucket aplikasi; baca, tulis, hapus di bucket arsip (hapus untuk membersihkan arsip lewat 520 hari).</p>
        <p class="muted small">Detail pembuatan service account: docs/operations.md bagian setup SILO produksi.</p>
      </div>
    </div>`;
  const form = () => {
    const out = {};
    for (const [k] of SILO_FIELDS) { const v = $(`#s-${k}`).value.trim(); if (v && v !== String(val(k))) out[k] = v; }
    return out;
  };
  const showChecks = (r) => {
    $("#s-out").innerHTML = `<h3>${r.ok ? "Koneksi berhasil" : "Koneksi bermasalah"} <span class="muted small">${esc(r.endpoint)}</span></h3>` +
      r.checks.map((c) => `<div class="reason ${c.ok ? "info" : "reject"}">${c.ok ? "✅" : "❌"} <b>${esc(c.check)}</b><div class="small">${esc(c.detail)}</div></div>`).join("");
  };
  $("#s-import").onclick = () => {
    let j;
    try { j = JSON.parse($("#s-mc").value); } catch (e) { return toast("JSON tidak valid: " + e.message, true); }
    const map = { url: "endpoint", accessKey: "access_key", secretKey: "secret_key" };
    for (const [from, to] of Object.entries(map)) if (j[from]) $(`#s-${to}`).value = String(j[from]).replace(/\/+$/, "");
    if (j.api) $("#s-signature_version").value = /v2|^s3$/i.test(j.api) ? "s3" : "s3v4";
    if (j.path) $("#s-addressing_style").value = { on: "path", off: "virtual", dns: "virtual" }[String(j.path).toLowerCase()] || "auto";
    if (/\/\/(127\.0\.0\.1|localhost)[:/]/.test(j.url || "")) toast("Perhatian: 127.0.0.1/localhost di dalam Docker merujuk ke container engine, bukan SILO. Ganti dengan host/IP SILO jika perlu.", true);
    else toast("Form terisi. Klik Test koneksi lalu Simpan.");
  };
  $("#s-test").onclick = async () => {
    $("#s-out").innerHTML = `<p class="muted">Menguji...</p>`;
    try { showChecks(await api("/v1/admin/settings/storage/test", { method: "POST", body: form() })); } catch (e) { $("#s-out").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  };
  $("#s-save").onclick = async () => {
    const body = form();
    if (!Object.keys(body).length) return toast("Tidak ada perubahan");
    try { await api("/v1/admin/settings/storage", { method: "PUT", body }); toast("Tersimpan, berlaku dalam 10 detik"); silo(); } catch (e) { toast(e.message, true); }
  };
  document.querySelectorAll("[data-clear]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault();
    try { await api("/v1/admin/settings/storage", { method: "PUT", body: { clear: [a.dataset.clear] } }); toast("Dikembalikan ke nilai env"); silo(); } catch (err) { toast(err.message, true); }
  }));
}

/* ---------------- Evidence penagihan ---------------- */
async function evidencePdf(awbs) {
  try {
    const r = await fetch(awbs.length === 1 ? `/v1/evidence/${encodeURIComponent(awbs[0])}/pdf` : "/v1/evidence/batch", {
      method: awbs.length === 1 ? "GET" : "POST",
      headers: { "X-API-Key": storedKey() || "", "Content-Type": "application/json" },
      body: awbs.length === 1 ? undefined : JSON.stringify({ awbs }),
    });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((j.error && j.error.message) || r.statusText); }
    window.open(URL.createObjectURL(await r.blob()), "_blank");
  } catch (e) { toast("Evidence gagal: " + e.message, true); }
}

async function evidence() {
  $("#main").innerHTML = head("Evidence penagihan", "PDF bukti pengiriman untuk pelanggan: hanya foto layak (PASS/WARN), diambil dari arsip engine, tanpa skor internal, dengan kode verifikasi keaslian.") + `
    <form class="card" style="max-width:640px" id="ev-form">
      <label>Nomor resi (satu per baris, maks. 500)<textarea id="ev-awbs" class="code" style="min-height:180px" placeholder="AWB123&#10;AWB124" data-autofocus></textarea></label>
      <div class="actions"><button type="submit">Buat PDF</button></div>
      <p class="muted small">Satu resi = PDF per resi; banyak resi = satu PDF gabungan (misal per periode invoice). Resi tanpa foto layak tidak muncul di PDF; jika tidak ada satu pun, PDF berisi keterangan itu.</p>
    </form>`;
  $("#ev-form").onsubmit = (e) => {
    e.preventDefault();
    const awbs = [...new Set($("#ev-awbs").value.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean))];
    if (!awbs.length) return toast("Isi minimal satu nomor resi", true);
    if (awbs.length > 500) return toast("Maksimal 500 resi per PDF", true);
    evidencePdf(awbs);
  };
}

/* ---------------- Backup NAS (admin) ---------------- */
const ago = (iso) => {
  if (!iso) return "belum pernah";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "baru saja" : m < 60 ? `${m} menit lalu` : m < 1440 ? `${Math.round(m / 60)} jam lalu` : `${Math.round(m / 1440)} hari lalu`;
};
const gb = (n) => (n == null ? "-" : (n / 1024 ** 3).toFixed(1) + " GB");

async function nas() {
  const d = await api("/v1/admin/settings/backup");
  const c = d.config, s = d.state || {};
  const chk = (k, label, help) => `<label class="check">
    <input type="checkbox" id="b-${k}" ${c[k] ? "checked" : ""}><span><b>${esc(label)}</b><br><span class="small muted">${esc(help)}</span></span></label>`;
  const inp = (k, label, ph, help, type = "text") => `<label style="margin-bottom:10px">${esc(label)}
    <input id="b-${k}" type="${type}" autocomplete="off" value="${esc(type === "password" ? "" : c[k] ?? "")}" placeholder="${esc(ph)}">${help ? `<span class="small">${esc(help)}</span>` : ""}</label>`;
  $("#main").innerHTML = head("Backup NAS", "Engine menyalin database dan foto (arsip engine di bucket <code>ocr</code>, atau foto di bucket aplikasi untuk foto polling) ke NAS secara terjadwal. Hanya <b>menyalin</b>: file di NAS tidak pernah dihapus engine (kecuali dump database lama sesuai jumlah simpan).") + `
    ${d.secret_key_configured ? "" : `<div class="reason warn"><b>PQE_SECRET_KEY belum diisi di server.</b> Secret key NAS S3 tidak bisa disimpan sampai PQE_SECRET_KEY diisi. Tujuan folder tetap bisa dipakai.</div>`}
    <div class="grid cols-4">
      <div class="card kpi"><div class="v">${c.enabled ? "Aktif" : "Mati"}</div><div class="l">Status jadwal</div></div>
      <div class="card kpi"><div class="v">${d.pending}</div><div class="l">Foto belum tersalin</div></div>
      <div class="card kpi"><div class="v">${d.backed_up}</div><div class="l">Foto sudah tersalin</div></div>
      <div class="card kpi"><div class="v">${d.free_space ? gb(d.free_space.free) : "-"}</div><div class="l">Ruang kosong NAS</div></div>
    </div>
    ${s.last_error ? `<div class="reason reject"><b>Backup terakhir gagal</b> (${esc(ago(s.last_error_at))})<div class="small">${esc(s.last_error)}</div></div>` : ""}
    <div class="grid cols-2">
      <div class="card"><h2>Tujuan</h2>
        ${chk("enabled", "Aktifkan backup terjadwal", "Jika mati, backup tetap bisa dijalankan manual dengan tombol di bawah.")}
        <label style="margin-bottom:10px">Jenis NAS
          <select id="b-target" data-autofocus><option value="folder" ${c.target === "folder" ? "selected" : ""}>Folder (NAS di-mount lewat SMB/NFS)</option>
          <option value="s3" ${c.target === "s3" ? "selected" : ""}>NAS S3-compatible (Synology/QNAP/MinIO)</option></select></label>
        <div id="b-folder">${inp("folder_path", "Folder di dalam container", "/backup", "Bawaan /backup = folder NAS_MOUNT di server (docker-compose). Mount share NAS ke folder itu di server.")}</div>
        <div id="b-s3">
          ${inp("s3_endpoint", "Endpoint NAS", "http://192.168.1.20:9000", "URL layanan S3 di NAS, bisa dijangkau dari server engine.")}
          ${inp("s3_bucket", "Bucket", "pqe-backup", "")}
          ${inp("s3_prefix", "Prefix (opsional)", "photo-quality", "")}
          ${inp("s3_region", "Region", "us-east-1", "")}
          ${inp("s3_access_key", "Access key", "", "Akun khusus backup di NAS.")}
          ${inp("s3_secret_key", "Secret key", c.s3_secret_key.set ? "•••••• (tersimpan)" : "belum diisi", "Disimpan terenkripsi. Kosongkan jika tidak ingin mengubah.", "password")}
          <label style="margin-bottom:10px">Gaya alamat<select id="b-s3_addressing_style">${["path", "auto", "virtual"].map((o) => `<option ${c.s3_addressing_style === o ? "selected" : ""}>${o}</option>`).join("")}</select></label>
        </div>
        <div class="actions"><button class="secondary" id="b-test">Test NAS</button><button id="b-save">Simpan</button></div>
        <div id="b-out"></div>
      </div>
      <div class="card"><h2>Yang dibackup</h2>
        ${chk("include_database", "Database engine", "Hasil analisis, review, config. Wajib.")}
        ${chk("include_photos", "Foto", "Satu salinan per foto: arsip engine (bucket ocr) bila ada, selain itu foto di bucket aplikasi (foto polling). Wajib.")}
        <details><summary class="small muted">Data versi lama</summary>
          ${chk("include_upload_originals", "Foto asli di bucket upload engine", "Hanya foto dari versi sebelum engine berhenti menyimpan ke SILO.")}
          ${chk("include_processed", "Foto hasil kompres di bucket photos-processed", "Hanya foto dari versi lama.")}
          ${chk("include_thumbs", "Thumbnail di bucket engine", "Bisa dibuat ulang, biasanya tidak perlu.")}
        </details>
        <h3>Jadwal</h3>
        <div class="filters">
          <label>Foto setiap (menit)<input id="b-photo_interval_minutes" type="number" min="1" max="1440" value="${c.photo_interval_minutes}"></label>
          <label>Database setiap (jam)<input id="b-db_interval_hours" type="number" min="1" max="720" value="${c.db_interval_hours}"></label>
          <label>Simpan dump terakhir<input id="b-db_keep" type="number" min="1" max="365" value="${c.db_keep}"></label>
        </div>
        <h3>Status</h3>
        <div class="kv">
          <div>Foto terakhir</div><div>${esc(ago(s.last_photo_run))}${s.last_photo_result ? ` · ${s.last_photo_result.photos} foto, ${s.last_photo_result.files} file` : ""}</div>
          <div>Database terakhir</div><div>${esc(ago(s.last_db_run))}${s.last_db_result ? ` · ${esc(s.last_db_result.file)} (${kb(s.last_db_result.bytes)})` : ""}</div>
          <div>Total file disalin</div><div>${s.files_total || 0}</div>
          <div>Permintaan</div><div>${s.requested ? `menunggu worker (${esc(s.requested)})` : s.lease_until ? "sedang berjalan" : "-"}</div>
        </div>
        <div class="actions">
          <button class="secondary" data-run="photos">Backup foto sekarang</button>
          <button class="secondary" data-run="database">Backup database sekarang</button>
        </div>
        <p class="muted small">Tata letak di NAS: <code>photos/&lt;bucket&gt;/&lt;key&gt;</code> dan <code>database/pqe-&lt;waktu&gt;.dump</code>.
          Cara mount NAS & restore: docs/operations.md bagian backup.</p>
      </div>
    </div>`;
  const toggle = () => { const s3 = $("#b-target").value === "s3"; $("#b-s3").classList.toggle("hidden", !s3); $("#b-folder").classList.toggle("hidden", s3); };
  $("#b-target").onchange = toggle; toggle();
  const form = () => {
    const out = { target: $("#b-target").value };
    for (const k of ["enabled", "include_photos", "include_upload_originals", "include_processed", "include_database", "include_thumbs"]) out[k] = $(`#b-${k}`).checked;
    for (const k of ["photo_interval_minutes", "db_interval_hours", "db_keep"]) out[k] = Number($(`#b-${k}`).value);
    for (const k of ["folder_path", "s3_endpoint", "s3_bucket", "s3_prefix", "s3_region", "s3_access_key", "s3_secret_key", "s3_addressing_style"]) {
      const v = $(`#b-${k}`).value.trim(); if (v || k !== "s3_secret_key") out[k] = v;
    }
    return out;
  };
  $("#b-test").onclick = async () => {
    $("#b-out").innerHTML = `<p class="muted">Menguji...</p>`;
    try {
      const r = await api("/v1/admin/settings/backup/test", { method: "POST", body: form() });
      $("#b-out").innerHTML = `<h3>${r.ok ? "NAS siap" : "NAS bermasalah"} <span class="muted small">${esc(r.target || "")}</span></h3>` +
        r.checks.map((x) => `<div class="reason ${x.ok ? "info" : "reject"}">${x.ok ? "✅" : "❌"} <b>${esc(x.check)}</b><div class="small">${esc(x.detail)}</div></div>`).join("");
    } catch (e) { $("#b-out").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  };
  $("#b-save").onclick = async () => {
    try { await api("/v1/admin/settings/backup", { method: "PUT", body: form() }); toast("Tersimpan, berlaku di putaran worker berikutnya"); nas(); } catch (e) { toast(e.message, true); }
  };
  document.querySelectorAll("[data-run]").forEach((b) => (b.onclick = async () => {
    try { await api("/v1/admin/backup/run", { method: "POST", body: { kind: b.dataset.run } }); toast("Diminta, worker menjalankan dalam ±10 detik"); setTimeout(() => location.hash === "#/nas" && nas(), 12000); } catch (e) { toast(e.message, true); }
  }));
}

/* ---------------- Sistem ---------------- */
async function runPreflight() {
  $("#pf-out").innerHTML = `<p class="muted">Memeriksa...</p>`;
  try {
    const r = await api("/v1/admin/preflight");
    const icon = { ok: "✅", warn: "⚠️", fail: "❌" }, cls = { ok: "info", warn: "warn", fail: "reject" };
    $("#pf-out").innerHTML = `<h3>${r.ready ? "Siap production" : "Belum siap production"} <span class="muted small">${r.counts.ok} ok · ${r.counts.warn} peringatan · ${r.counts.fail} gagal</span></h3>` +
      r.checks.map((c) => `<div class="reason ${cls[c.status]}">${icon[c.status]} <b>${esc(c.group)} · ${esc(c.check)}</b> <span class="small">${esc(c.detail)}</span>${c.status !== "ok" && c.fix ? `<div class="small">→ ${esc(c.fix)}</div>` : ""}</div>`).join("");
  } catch (e) { $("#pf-out").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}

async function system() {
  const [jobs, sources, hooks] = await Promise.all([api("/v1/admin/jobs?status=dead"), api("/v1/admin/sources"), api("/v1/admin/webhooks?status=dead")]);
  const audit = state.me.role === "admin" ? await api("/v1/admin/audit?limit=50").catch(() => []) : [];
  const st = jobs.stats;
  $("#main").innerHTML = head("Sistem", "Antrean analisis, kesiapan production, kegagalan, dan aktivitas.") + `
    <div class="grid cols-4">
      <div class="card kpi"><div class="v">${st.queued}</div><div class="l">Antre</div></div>
      <div class="card kpi"><div class="v">${st.running}</div><div class="l">Diproses</div></div>
      <div class="card kpi"><div class="v">${st.done}</div><div class="l">Selesai</div></div>
      <div class="card kpi"><div class="v" style="color:${st.dead ? "var(--reject)" : "inherit"}">${st.dead}</div><div class="l">Gagal permanen (dead)</div></div>
    </div>
    ${state.me.role === "admin" ? `<div class="card"><h2>Kesiapan production (preflight)</h2>
      <p class="muted small">Memeriksa secret, database, koneksi SILO (baca bucket aplikasi, tulis & hapus file uji di bucket arsip), mode tenant, backup NAS, worker, dan OCR.
        Sama dengan perintah <code>pqe preflight</code>.</p>
      <div class="actions"><button id="pf-run" data-autofocus>Jalankan preflight</button></div><div id="pf-out"></div></div>` : ""}
    <div class="card"><h2>Job gagal permanen</h2>
      ${jobs.items.length ? `<table><thead><tr><th>ID</th><th>Tenant</th><th>Objek</th><th>Percobaan</th><th>Error terakhir</th><th></th></tr></thead><tbody>
      ${jobs.items.map((j) => `<tr><td>${j.id}</td><td>${esc(j.tenant)}</td><td><code class="small">${esc(j.object_key)}</code></td><td>${j.attempts}</td><td class="small">${esc(j.last_error)}</td>
        <td>${state.me.role === "admin" ? `<button class="secondary" data-retry="${j.id}">Ulangi</button>` : ""}</td></tr>`).join("")}</tbody></table>
      ${state.me.role === "admin" ? `<div class="actions"><button id="retry-all">Ulangi semua</button></div>` : ""}` : `<p class="muted">Tidak ada. Bagus.</p>`}
    </div>
    <div class="card"><h2>Webhook gagal permanen</h2>
      ${hooks.length ? `<table><thead><tr><th>ID</th><th>Tenant</th><th>Event</th><th>URL</th><th>Percobaan</th><th>Error terakhir</th></tr></thead><tbody>
      ${hooks.map((h) => `<tr><td>${h.id}</td><td>${esc(h.tenant)}</td><td>${esc(h.event)}</td><td class="small">${esc(h.url)}</td><td>${h.attempts}</td><td class="small">${esc(h.last_error)}</td></tr>`).join("")}</tbody></table>
      ${state.me.role === "admin" ? `<div class="actions"><button id="retry-hooks">Kirim ulang semua</button></div>` : ""}` : `<p class="muted">Tidak ada.</p>`}
    </div>
    <div class="card"><h2>Source ingest</h2>
      ${sources.length ? `<table><thead><tr><th>Source</th><th>Terakhir jalan</th><th>Foto baru</th><th>Reconcile terakhir</th><th>Error</th></tr></thead><tbody>
      ${sources.map((s) => `<tr><td><code>${esc(s.id)}</code></td><td class="small">${fmtDate(s.last_run_at)}</td><td>${s.last_found}</td><td class="small">${fmtDate(s.last_reconcile_at)}</td><td class="small error">${esc(s.last_error || "")}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">Belum ada source yang berjalan.</p>`}
    </div>
    ${audit.length ? `<div class="card"><h2>Audit log</h2><table>${audit.map((a) => `<tr><td class="small">${fmtDate(a.at)}</td><td>${esc(a.actor)}</td><td><b>${esc(a.action)}</b></td><td>${esc(a.target || "")}</td></tr>`).join("")}</table></div>` : ""}
    <p class="muted small">Metrik Prometheus: <a href="/metrics" target="_blank">/metrics</a> · Dokumentasi API: <a href="/docs" target="_blank">/docs</a></p>`;
  document.querySelectorAll("[data-retry]").forEach((b) => (b.onclick = async () => { await api(`/v1/admin/jobs/${b.dataset.retry}/retry`, { method: "POST" }); toast("Job diantrekan ulang"); system(); }));
  const rh = $("#retry-hooks");
  if (rh) rh.onclick = async () => { const r = await api("/v1/admin/webhooks/retry-dead", { method: "POST" }); toast(`${r.requeued} webhook diantrekan ulang`); system(); };
  const ra = $("#retry-all");
  if (ra) ra.onclick = async () => { const r = await api("/v1/admin/jobs/retry-dead", { method: "POST" }); toast(`${r.requeued} job diantrekan ulang`); system(); };
  const pf = $("#pf-run");
  if (pf) pf.onclick = runPreflight;
}

boot();
