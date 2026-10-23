/**
 * Test client TypeScript. Jalankan dengan Node yang sudah di-patch (lihat README.md di folder ini):
 *   node --experimental-strip-types clients/typescript/test.ts
 * Uji online (opsional): set PQE_URL, PQE_API_KEY (role app), PQE_SAMPLE (path foto).
 */
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { PhotoQualityClient, toDecision, verifyWebhook } from "./pqe-client.ts";

// Versi minimal yang memuat security release 21 Okt 2026 (CVE-2026-111497 dkk).
const MIN_PATCHED: Record<number, [number, number]> = { 22: [23, 4], 24: [22, 1], 26: [12, 1] };

let failed = 0;
function check(name: string, cond: boolean, extra = ""): void {
  console.log(`${cond ? "ok  " : "FAIL"} ${name}${extra ? " - " + extra : ""}`);
  if (!cond) failed++;
}

const [major, minor, patch] = process.versions.node.split(".").map(Number);
const min = MIN_PATCHED[major];
check(
  `Node ${process.versions.node} sudah di-patch`,
  !!min && (minor > min[0] || (minor === min[0] && patch >= min[1])),
  min ? `minimal v${major}.${min[0]}.${min[1]}` : "gunakan Node 22, 24, atau 26 terbaru",
);

const ts = String(Math.floor(Date.now() / 1000));
const body = JSON.stringify({ event: "analysis.completed", photo: { id: "x" } });
const sig = "sha256=" + createHmac("sha256", "rahasia").update(`${ts}.${body}`).digest("hex");
check("webhook valid diterima", await verifyWebhook("rahasia", ts, body, sig));
check("webhook diubah ditolak", !(await verifyWebhook("rahasia", ts, body + " ", sig)));
check("webhook secret salah ditolak", !(await verifyWebhook("salah", ts, body, sig)));
check("webhook kedaluwarsa ditolak", !(await verifyWebhook("rahasia", String(Number(ts) - 3600), body, sig)));

const down = new PhotoQualityClient({ baseUrl: "http://127.0.0.1:9", apiKey: "x", timeoutMs: 800 });
const r = await down.analyzeObject("a.jpg", { awb: "X" }, { sync: true, timeoutMs: 500 });
check("engine mati -> fail-open tanpa exception", !r.ok && r.unavailable === true);
const dDown = await down.checkObject("a.jpg", { awb: "X" }, { timeoutMs: 500 });
check("checkObject engine mati -> status boleh diupdate", dDown.allowStatusUpdate && !dDown.decided && dDown.message === null);

const photo = (extra: Record<string, unknown>) => ({ ok: true as const, status: 200, data: { id: "p1", status: "done", verdict: "REJECT", action: "reject", reasons: [], scores: {}, awb: null, photo_type: null, verdict_text: null, ...extra } as any });
const dReject = toDecision(photo({ allow_status_update: false, courier_message: "Wajib foto ulang. Foto buram.", retake_required: true }));
check("REJECT enforce -> status ditahan + pesan", !dReject.allowStatusUpdate && dReject.decided && dReject.message === "Wajib foto ulang. Foto buram." && dReject.retakeRequired);
const dPass = toDecision(photo({ verdict: "PASS", action: "log", allow_status_update: true, courier_message: null, stored: { bucket: "b", key: "k.jpg", url: "https://s3/b/k.jpg" } }));
check("PASS -> status boleh, tanpa pesan, ada link", dPass.allowStatusUpdate && dPass.message === null && dPass.stored?.url === "https://s3/b/k.jpg");
check("REJECT -> tanpa link", dReject.stored === null);
check("engine mati -> tanpa link (simpan sendiri)", dDown.stored === null);
const dPending = toDecision({ ok: true, status: 202, data: { id: "p2", status: "pending" } as any });
check("lewat 3 detik (pending) -> fail-open", dPending.allowStatusUpdate && !dPending.decided && dPending.photoId === "p2");

const { PQE_URL, PQE_API_KEY, PQE_SAMPLE } = process.env;
if (PQE_URL && PQE_API_KEY && PQE_SAMPLE) {
  const pqe = new PhotoQualityClient({ baseUrl: PQE_URL, apiKey: PQE_API_KEY, lang: "id" });
  const file = new Blob([readFileSync(PQE_SAMPLE)], { type: "image/jpeg" });
  const res = await pqe.analyzeFile(file, "sample.jpg", { awb: "TS-TEST", source: "camera" }, { sync: true });
  check("analyzeFile sync", res.ok && !!res.photo?.verdict, res.ok ? `${res.photo?.verdict} ${res.photo?.reasons[0]?.message ?? ""}` : res.error);
  const d = await pqe.checkFile(file, "sample2.jpg", { awb: "TS-TEST-2", source: "camera" });
  check("checkFile", d.decided && typeof d.allowStatusUpdate === "boolean", JSON.stringify(d));
  if (res.ok) {
    const got = await pqe.getPhoto(res.photo!.id);
    check("getPhoto", got.ok && got.photo?.id === res.photo!.id);
  }
} else {
  console.log("skip uji online (set PQE_URL, PQE_API_KEY, PQE_SAMPLE)");
}

if (failed) {
  console.error(`${failed} test gagal`);
  process.exit(1);
}
console.log("semua test lolos");
