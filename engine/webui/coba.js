/* Uji Coba Kamera: berperan sebagai aplikasi kurir untuk mencoba engine tanpa membangun aplikasi mobile. */
"use strict";

const KEY_STORE = "pqe_api_key"; // sama dengan Web UI, jadi cukup login sekali
const SYNC_TIMEOUT_MS = 3000;    // sama dengan rekomendasi untuk aplikasi asli
const state = { me: null, tenants: [], profiles: [], reviewRequired: false, mode: "sim", stream: null, facing: "environment", torch: false, history: [], seq: 0 };
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const kb = (n) => (n == null ? "-" : n >= 1048576 ? (n / 1048576).toFixed(2) + " MB" : Math.round(n / 1024) + " KB");
const ms = (n) => (n == null ? "-" : n >= 1000 ? (n / 1000).toFixed(1) + " dtk" : Math.round(n) + " ms");
const badge = (v) => `<span class="badge b-${esc(v || "pending")}">${esc(v || "pending")}</span>`;
const sleep = (t) => new Promise((r) => setTimeout(r, t));
const CAN_SAVE = ["admin", "ops", "app"];

function storedKey() { try { return localStorage.getItem(KEY_STORE); } catch { return null; } }
function saveKey(k) { try { k ? localStorage.setItem(KEY_STORE, k) : localStorage.removeItem(KEY_STORE); } catch { /* abaikan */ } }

async function api(path, opts = {}) {
  const headers = { "X-API-Key": storedKey() || "", "Accept-Language": $("#lang").value, ...(opts.headers || {}) };
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { saveKey(null); showLogin("API key tidak valid atau sudah dicabut."); throw new Error("unauthorized"); }
  if (opts.raw) { if (!r.ok) throw new Error(r.statusText); return r; }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error && data.error.message) || (data && data.detail && JSON.stringify(data.detail)) || r.statusText);
  return { status: r.status, data };
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 4500);
}

function showLogin(msg = "") {
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); $("#logout").classList.add("hidden");
  $("#who").textContent = ""; $("#login-error").textContent = msg;
}

/* ---------------- start ---------------- */
async function boot() {
  if (!storedKey()) return showLogin();
  try { state.me = (await api("/v1/me")).data; } catch (e) { return showLogin(e.message === "unauthorized" ? "API key tidak valid." : e.message); }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); $("#logout").classList.remove("hidden");
  $("#who").textContent = `${state.me.name} (${state.me.role})`;
  if (state.me.tenant) state.tenants = [{ code: state.me.tenant, name: state.me.tenant }];
  else { try { state.tenants = (await api("/v1/admin/tenants")).data.filter((t) => t.enabled); } catch { state.tenants = []; } }
  const pick = state.tenants.find((t) => t.code === "uji-coba") || state.tenants[0];
  $("#tenant").innerHTML = (state.me.tenant ? "" : `<option value="">(profile bawaan, hanya uji cepat)</option>`) +
    state.tenants.map((t) => `<option value="${esc(t.code)}">${esc(t.name)} (${esc(t.code)})</option>`).join("");
  if (pick) $("#tenant").value = pick.code;
  $("#awb").value = newAwb();
  setMode(CAN_SAVE.includes(state.me.role) && $("#tenant").value ? "sim" : "quick");
  await loadProfiles();
  cameraHint();
}

function newAwb() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `UJI${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

async function loadProfiles() {
  const t = $("#tenant").value;
  try {
    const r = (await api("/v1/meta/profiles" + (t ? `?tenant=${encodeURIComponent(t)}` : ""))).data;
    state.profiles = r.profiles; state.reviewRequired = !!r.review_required;
    $("#ptype").innerHTML = r.profiles.map((p) => `<option value="${esc(p.name)}">${esc(p.name)} (mode ${esc(p.mode)})</option>`).join("");
    $("#ptype").value = r.profiles.some((p) => p.name === r.default_profile) ? r.default_profile : r.profiles[0].name;
  } catch (e) { toast(e.message, true); }
}

function setMode(m) {
  const canSim = CAN_SAVE.includes(state.me.role) && !!$("#tenant").value;
  if (m === "sim" && !canSim) m = "quick";
  state.mode = m;
  document.querySelectorAll("#mode-seg button").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === m);
    if (b.dataset.mode === "sim") b.disabled = !canSim;
  });
  $("#sim-fields").classList.toggle("hidden", m !== "sim");
  $("#mode-desc").innerHTML = m === "sim"
    ? `Sama seperti aplikasi asli: foto <b>disimpan</b> (upload → antrean → worker → SILO), kurir menunggu maks. ${SYNC_TIMEOUT_MS / 1000} detik. Foto muncul di Web UI dan evidence resi uji.`
    : `Hanya dianalisis, <b>tidak disimpan</b> ke mana pun. Cocok untuk mencoba banyak foto dengan cepat.` +
      (canSim ? "" : ` <br>${!$("#tenant").value ? "Pilih tenant untuk simulasi." : "Role viewer hanya bisa uji cepat."}`);
}

function cameraHint() {
  const live = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext;
  $("#btn-live").disabled = !live;
  $("#cam-hint").innerHTML = `<b>📷 Ambil foto</b> membuka aplikasi kamera bawaan HP, sama seperti aplikasi kurir (data EXIF ikut terkirim).` +
    (live ? ` <b>Kamera langsung</b> memotret dari browser (tanpa EXIF, hasil wajar).`
      : ` <b>Kamera langsung</b> butuh HTTPS atau localhost; buka halaman ini lewat <code>https://</code> untuk memakainya (lihat panduan uji coba).`);
}

/* ---------------- pengambilan foto ---------------- */
$("#btn-native").onclick = () => { $("#in-native").value = ""; $("#in-native").click(); };
$("#btn-gallery").onclick = () => { $("#in-gallery").value = ""; $("#in-gallery").click(); };
$("#in-native").onchange = (e) => e.target.files[0] && submit(e.target.files[0], "camera");
$("#in-gallery").onchange = (e) => e.target.files[0] && submit(e.target.files[0], "gallery");
$("#btn-live").onclick = openCamera;
$("#cam-close").onclick = closeCamera;
$("#cam-flip").onclick = () => { state.facing = state.facing === "environment" ? "user" : "environment"; openCamera(); };
$("#cam-torch").onclick = async () => {
  const track = state.stream && state.stream.getVideoTracks()[0];
  if (!track) return;
  state.torch = !state.torch;
  try { await track.applyConstraints({ advanced: [{ torch: state.torch }] }); } catch { toast("Lampu tidak didukung", true); }
};
$("#cam-shot").onclick = async () => {
  const track = state.stream && state.stream.getVideoTracks()[0];
  if (!track) return;
  let blob = null;
  if ("ImageCapture" in window) { // resolusi penuh sensor jika didukung (Chrome Android)
    try { blob = await new ImageCapture(track).takePhoto(); } catch { blob = null; }
  }
  if (!blob) {
    const v = $("#video"), c = $("#canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.95));
  }
  closeCamera();
  if (blob) submit(new File([blob], `kamera-${Date.now()}.jpg`, { type: blob.type || "image/jpeg" }), "camera");
};

async function openCamera() {
  stopStream();
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: false, video: { facingMode: { ideal: state.facing }, width: { ideal: 4096 }, height: { ideal: 3072 } },
    });
  } catch (e) { return toast("Kamera tidak bisa dibuka: " + e.message, true); }
  const v = $("#video");
  v.srcObject = state.stream;
  $("#cam").classList.remove("hidden");
  const track = state.stream.getVideoTracks()[0];
  const caps = track.getCapabilities ? track.getCapabilities() : {};
  $("#cam-torch").classList.toggle("hidden", !caps.torch);
  v.onloadedmetadata = () => { $("#cam-info").textContent = `${v.videoWidth}×${v.videoHeight} · ketuk tombol putih untuk memotret`; };
}
function stopStream() { if (state.stream) state.stream.getTracks().forEach((t) => t.stop()); state.stream = null; state.torch = false; }
function closeCamera() { stopStream(); $("#cam").classList.add("hidden"); }

function position() {
  if (!$("#gps").checked || !navigator.geolocation) return Promise.resolve(null);
  return new Promise((res) => navigator.geolocation.getCurrentPosition(
    (p) => res({ lat: p.coords.latitude, lng: p.coords.longitude }),
    () => { toast("Lokasi tidak tersedia (butuh izin & HTTPS), dilanjutkan tanpa GPS", true); res(null); },
    { timeout: 5000, maximumAge: 60000 }));
}

/* ---------------- kirim ke engine ---------------- */
async function submit(file, source) {
  const run = { id: ++state.seq, file, source, origUrl: URL.createObjectURL(file), mode: state.mode, tenant: $("#tenant").value,
    ptype: $("#ptype").value, viewMode: $("#view-mode").value };
  $("#result").innerHTML = `<h2><span class="num">3</span> Hasil</h2><div class="busy"><div class="spinner"></div>Mengirim ${kb(file.size)} ke engine...</div>`;
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    if (run.mode === "quick") await runQuick(run); else await runSim(run, source);
    if (run.mode === "sim" && !run.r.retake_required) $("#awb").value = newAwb(); // foto diterima: resi berikutnya
    state.history.unshift(run);
    renderHistory();
    render(run);
  } catch (e) {
    if (e.message === "unauthorized") return;
    $("#result").innerHTML = `<h2><span class="num">3</span> Hasil</h2><div class="msg reject"><b>Gagal</b>${esc(e.message)}
      <span class="small">Di aplikasi asli: kesalahan engine tidak boleh menghentikan kurir (fail-open), proses tetap lanjut.</span></div>`;
  }
}

async function runQuick(run) {
  const fd = new FormData(); fd.append("file", run.file);
  const q = new URLSearchParams({ photo_type: run.ptype, include_output: "true", lang: $("#lang").value });
  if (run.tenant) q.set("tenant", run.tenant);
  const t0 = performance.now();
  const r = (await api("/v1/analyze/preview?" + q, { method: "POST", body: fd })).data;
  run.waitMs = performance.now() - t0;
  run.r = r;
  run.outUrl = r.output_image || null;
  run.compression = r.compression; run.enhancement = r.enhancement; run.image = r.image; run.exif = r.exif;
  run.serverMs = r.timings_ms && r.timings_ms.total;
}

async function runSim(run, source) {
  const pos = await position();
  const fd = new FormData();
  fd.append("file", run.file);
  const fields = { awb: $("#awb").value.trim(), photo_type: run.ptype, courier_id: $("#courier").value.trim(), source,
    external_id: `coba-${Date.now()}`, ...(source === "camera" ? { captured_at: new Date().toISOString() } : {}),
    ...(pos ? { lat: pos.lat, lng: pos.lng } : {}) };
  for (const [k, v] of Object.entries(fields)) if (v !== "" && v != null) fd.append(k, v);
  run.awb = fields.awb;
  const q = new URLSearchParams({ tenant: run.tenant, sync: "true", timeout_ms: String(SYNC_TIMEOUT_MS), lang: $("#lang").value });
  const t0 = performance.now();
  const first = await api("/v1/analyze?" + q, { method: "POST", body: fd });
  run.waitMs = performance.now() - t0;
  run.timedOut = first.status === 202;
  let p = first.data;
  const deadline = Date.now() + 60000;
  while (p.status === "pending" && Date.now() < deadline) { // hasil menyusul (aplikasi asli: lewat webhook)
    $("#result").querySelector(".busy").innerHTML = `<div class="spinner"></div>Engine belum selesai dalam ${SYNC_TIMEOUT_MS / 1000} dtk. Aplikasi asli sudah lanjut (fail-open); menunggu hasil untuk ditampilkan...`;
    await sleep(800);
    p = (await api(`/v1/photos/${p.id}`)).data;
  }
  if (p.status === "pending") throw new Error("Hasil belum keluar setelah 60 detik. Cek worker di menu Sistem pada Web UI.");
  const d = (await api(`/v1/photos/${p.id}?lang=${$("#lang").value}`)).data;
  run.r = d; run.photoId = d.id;
  run.compression = d.compression; run.enhancement = d.enhancement; run.image = d.image; run.exif = d.exif;
  run.serverMs = d.duration_ms;
  try { // lewat engine, karena HP belum tentu bisa menjangkau host SILO
    const res = await api(`/v1/photos/${d.id}/file?variant=processed`, { raw: true });
    run.outUrl = URL.createObjectURL(await res.blob());
  } catch { run.outUrl = null; }
}

/* ---------------- tampilan ---------------- */
function effectiveAction(r, viewMode) {
  if (!viewMode) return r.action;
  if (r.verdict === "PASS") return "log";
  return { shadow: "log", warn: "notify", enforce: r.verdict === "REJECT" ? "reject" : "notify" }[viewMode];
}

// Pesan kurir untuk mode tampilan yang dipilih (aturan sama dengan field courier_message di engine).
function courierText(r, action) {
  if (!["notify", "reject"].includes(action)) return null;
  if (action === r.action && r.courier_message !== undefined) return r.courier_message;
  const first = (r.reasons || []).find((x) => x.severity !== "info");
  if (!first) return r.retake_required ? r.retake_message : null;
  const text = `${first.message}. ${first.suggestion}`;
  return r.retake_required ? `${$("#lang").value === "en" ? "Retake required." : "Wajib foto ulang."} ${text}` : text;
}

function courierScreen(run) {
  const r = run.r;
  const action = effectiveAction(r, run.viewMode);
  const allow = run.timedOut || action !== "reject";
  const text = run.timedOut ? null : courierText(r, action);
  const modeLabel = run.viewMode || (state.profiles.find((p) => p.name === (r.profile || run.ptype)) || {}).mode || r.mode || "-";
  let msg;
  if (run.timedOut) {
    msg = `<div class="msg none"><b>Foto terkirim</b>Engine belum menjawab dalam ${SYNC_TIMEOUT_MS / 1000} detik, jadi aplikasi pengiriman lanjut tanpa pesan (fail-open). Hasil di bawah datang menyusul.</div>`;
  } else if (!allow) {
    msg = `<div class="msg reject"><b>⚠ Foto ditolak</b>${esc(text || "")}
      <div class="btns" style="grid-template-columns:1fr"><button data-act="retake">Foto ulang</button></div></div>`;
  } else if (action === "notify" && r.retake_required) {
    msg = `<div class="msg notify"><b>⚠ Kualitas foto kurang</b>${esc(text || "")}
      <div class="btns"><button data-act="retake">Foto ulang</button><button class="alt" data-act="send">Tetap kirim</button></div></div>`;
  } else if (action === "notify") {
    msg = `<div class="msg notify"><b>Foto diterima</b>${esc(text || "")}
      <div class="btns"><button data-act="send">Lanjut</button><button class="alt" data-act="retake">Foto ulang</button></div></div>`;
  } else {
    msg = `<div class="msg ok"><b>✓ Foto diterima</b></div>` +
      (r.verdict !== "PASS" ? `<p class="note">Mode shadow: engine mencatat ${esc(r.verdict)}, tetapi kurir tidak melihat pesan apa pun.</p>` : "");
  }
  const status = allow
    ? `<div class="msg ok" style="padding:8px 12px"><span>Status pengiriman: <b>diupdate</b> oleh aplikasi pengiriman</span></div>`
    : `<div class="msg reject" style="padding:8px 12px"><span>Status pengiriman: <b>TIDAK diupdate</b> sampai foto ulang diterima</span></div>`;
  return `<div class="phone"><div class="bar"><span>Aplikasi Kurir</span><span class="small">${esc(run.awb || "uji cepat")}</span></div>
    <div class="screen"><img src="${run.origUrl}" alt="foto">${msg}${status}
    <p class="note">Waktu tunggu kurir: <b>${ms(run.waitMs)}</b> · mode ${esc(modeLabel)}</p></div></div>`;
}

function scoreBars(scores) {
  const color = (v) => (v >= 60 ? "var(--pass)" : v >= 35 ? "var(--warn)" : "var(--reject)");
  return Object.entries(scores || {}).filter(([k]) => k !== "authenticity_risk").map(([k, v]) =>
    `<div class="score-row"><span class="small">${esc(k)}</span><div class="bar"><span style="width:${Math.max(0, Math.min(100, v))}%;background:${color(v)}"></span></div><b class="small">${Math.round(v)}</b></div>`).join("");
}

function render(run) {
  const r = run.r, c = run.compression || {}, en = run.enhancement || {}, img = run.image || {}, ex = run.exif || {};
  const reasons = (r.reasons || []).map((x) => `<div class="reason ${esc(x.severity)}"><b>${esc(x.message || x.code)}</b> <span class="muted small">${esc(x.code)} · ${esc(x.severity)}${x.score != null ? ` · skor ${Math.round(x.score)}` : ""}</span>${x.suggestion ? `<div class="small">${esc(x.suggestion)}</div>` : ""}</div>`).join("") || `<p class="muted">Tidak ada catatan.</p>`;
  const outCaption = run.outUrl
    ? `Hasil engine (yang disimpan &amp; dipakai evidence): ${kb(c.output_bytes)}${c.width ? `, ${c.width}×${c.height}` : ""}${en.applied ? " · diperbaiki otomatis" : ""}`
    : (c.passthrough ? "Foto sudah kecil, disimpan apa adanya tanpa kompresi." : "Versi hasil tidak tersedia.");
  const evid = r.evidence_eligible == null ? null : r.evidence_eligible;
  $("#result").innerHTML = `<h2><span class="num">3</span> Hasil ${badge(r.verdict)}</h2>
    <h3>Yang dilihat kurir</h3>${courierScreen(run)}
    <h3>Detail untuk tim</h3>
    <div class="facts">
      <div class="fact"><div class="l">Verdict</div><div class="v">${esc(r.verdict)}</div></div>
      <div class="fact"><div class="l">Boleh update status</div><div class="v">${run.timedOut || effectiveAction(r, run.viewMode) !== "reject" ? "Ya" : "Tidak"}</div></div>
      <div class="fact"><div class="l">Wajib foto ulang</div><div class="v">${r.retake_required ? "Ya" : "Tidak"}</div></div>
      <div class="fact"><div class="l">Aksi (config)</div><div class="v">${esc(r.action)}</div></div>
      <div class="fact"><div class="l">Waktu tunggu kurir</div><div class="v">${ms(run.waitMs)}</div></div>
      <div class="fact"><div class="l">Proses engine</div><div class="v">${ms(run.serverMs)}</div></div>
      <div class="fact"><div class="l">Ukuran</div><div class="v">${kb(img.bytes || run.file.size)} → ${kb(c.output_bytes)}</div></div>
      ${evid == null ? "" : `<div class="fact"><div class="l">Masuk evidence</div><div class="v">${evid ? "Ya" : "Tidak"}</div></div>`}
    </div>
    ${r.retake_required ? `<div class="reason reject"><b>Wajib foto ulang</b><div class="small">Kualitas tidak memenuhi syarat. Engine tidak memanipulasi gambar, jadi foto harus diambil ulang.</div></div>` : ""}
    ${en.applied ? `<div class="reason info"><b>Diperbaiki otomatis (tanpa AI)</b><div class="small">${esc((en.operations || []).join(", "))}. Sebelum: ${esc(en.before && en.before.verdict)} → sesudah: ${esc(r.verdict)}. Terverifikasi: hanya kurva pencahayaan global, tidak ada teks/angka yang berubah.</div></div>`
      : en.attempted ? `<div class="reason"><b>Perbaikan otomatis dicoba, tidak dipakai</b> <span class="small muted">(${esc(en.reason || "-")})</span></div>` : ""}
    <div class="compare">
      <figure><img src="${run.origUrl}" alt="asli"><figcaption>Foto asli dari HP: ${kb(img.bytes || run.file.size)}${img.width ? `, ${img.width}×${img.height}` : ""}</figcaption></figure>
      <figure>${run.outUrl ? `<img src="${run.outUrl}" alt="hasil">` : ""}<figcaption>${outCaption}</figcaption></figure>
    </div>
    <h3>Skor (0-100)</h3>${scoreBars(r.scores)}
    <h3>Catatan</h3>${reasons}
    <h3>Kompresi</h3><p class="small">${c.passthrough ? "Tidak perlu dikompres (sudah di bawah target)."
      : c.quality == null ? "Sudah di bawah target; hasil perbaikan disimpan sebagai file JPEG baru (foto asli tetap utuh)."
      : `Quality ${esc(c.quality)}, SSIM ${esc(c.ssim)}, ${c.target_met ? "target tercapai" : "target belum tercapai"}.`}</p>
    <h3>EXIF</h3><p class="small">${ex.make || ex.model ? `${esc(ex.make || "")} ${esc(ex.model || "")}` : "Tidak ada data kamera"}${ex.datetime_original ? ` · diambil ${esc(new Date(ex.datetime_original).toLocaleString("id-ID"))}` : ""}${ex.gps_lat != null ? " · ada GPS" : ""}${!ex.make ? " <span class='muted'>(wajar untuk kamera langsung di browser, screenshot, atau file hasil edit)</span>" : ""}</p>
    ${run.photoId ? `<div class="actions"><a class="btn" href="./#/photos?awb=${encodeURIComponent(run.awb || "")}&id=${encodeURIComponent(run.photoId)}" target="_blank">Buka di Web UI</a>
      <button class="secondary" id="btn-pdf">Evidence PDF resi ini</button></div>` : ""}
    <details style="margin-top:12px"><summary class="muted">JSON lengkap (respons API)</summary><pre class="json">${esc(JSON.stringify({ ...r, output_image: r.output_image ? "(data URL)" : r.output_image }, null, 2))}</pre></details>`;
  $("#result").querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => {
    if (b.dataset.act === "retake") return $("#btn-native").click();
    toast("Di aplikasi asli: foto diterima, aplikasi pengiriman mengupdate status pengiriman.");
  }));
  const pdf = $("#btn-pdf");
  if (pdf) pdf.onclick = async () => {
    try {
      const res = await api(`/v1/evidence/${encodeURIComponent(run.awb)}/pdf`, { raw: true });
      window.open(URL.createObjectURL(await res.blob()), "_blank");
    } catch (e) { toast(r.evidence_eligible ? e.message : "Foto ini tidak masuk evidence (REJECT), jadi PDF resi ini kosong / tidak tersedia.", true); }
  };
}

function renderHistory() {
  $("#hist-card").classList.remove("hidden");
  $("#history").innerHTML = state.history.map((h) => `<button data-run="${h.id}"><img src="${h.origUrl}" alt="">${badge(h.r.verdict)}<br>${h.mode === "sim" ? "simulasi" : "cepat"}</button>`).join("");
  $("#history").querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => render(state.history.find((h) => h.id === +b.dataset.run))));
}

/* ---------------- event form ---------------- */
$("#login-form").addEventListener("submit", (e) => { e.preventDefault(); saveKey($("#login-key").value.trim()); boot(); });
$("#logout").onclick = () => { saveKey(null); showLogin(); };
$("#tenant").onchange = async () => { setMode(state.mode); await loadProfiles(); };
document.querySelectorAll("#mode-seg button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
window.addEventListener("pagehide", stopStream);
boot();
