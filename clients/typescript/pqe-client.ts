/**
 * Client Photo Quality Engine untuk TypeScript / JavaScript (Node 18+, browser, React Native, Deno, Bun).
 * Tanpa dependency. Salin file ini ke project Anda.
 *
 * PRINSIP: engine TIDAK BOLEH menghambat proses kurir. Semua method di sini "fail-open":
 * jika engine lambat/mati, method mengembalikan { ok: false, unavailable: true } dan TIDAK melempar error.
 * Lanjutkan proses bisnis seperti biasa.
 *
 * Engine HANYA memproses gambar. Sebelum dan sesudahnya (termasuk menyimpan ke SILO) dikerjakan aplikasi Anda.
 *
 * Cara utama (satu panggilan, satu keputusan):
 *   const pqe = new PhotoQualityClient({ baseUrl: "https://photo-quality.pcpexpress.com", apiKey: process.env.PQE_API_KEY! });
 *   const key = `pod/2026/10/24/AWB123.jpg`; // key SILO yang akan dipakai aplikasi
 *   const d = await pqe.checkFile(fotoBlob, "foto.jpg", { awb: "AWB123", photo_type: "pod_recipient" }, { objectKey: key });
 *   if (d.message) kirimKeKurir(d.message);
 *   if (!d.allowStatusUpdate) return; // foto ditolak: jangan disimpan, kurir foto ulang, status JANGAN diupdate
 *   await simpanKeSilo(key, d.image?.data ?? fotoBlob); // image null = engine tidak menjawab -> simpan foto asli
 *   updateStatusPengiriman(key);
 */

export type Verdict = "PASS" | "WARN" | "REJECT";

export interface PhotoFields {
  awb?: string;
  photo_type?: string; // pod_recipient | pod_document | invoice | accident | package | goods | location | other
  courier_id?: string;
  customer_id?: string;
  branch?: string;
  captured_at?: string; // ISO-8601, contoh new Date().toISOString()
  lat?: number;
  lng?: number;
  source?: "camera" | "gallery";
  external_id?: string; // ID foto di aplikasi Anda
  reference?: string; // nomor lain yang harus ada di foto: nomor invoice, nomor laporan, ...
  dest_lat?: number; // koordinat alamat tujuan (foto jauh dari sini ditandai)
  dest_lng?: number;
}

export interface Reason {
  code: string; // stabil, aman dipakai di logika aplikasi
  check: string;
  severity: "reject" | "warn" | "info";
  score: number | null;
  message: string; // sudah diterjemahkan (id/en)
  suggestion: string; // saran untuk kurir
}

export interface Photo {
  id: string;
  status: "pending" | "done" | "error";
  verdict: Verdict | null;
  verdict_text: string | null;
  action: "log" | "notify" | "reject" | null; // apa yang sebaiknya dilakukan aplikasi (sesuai mode)
  awb: string | null;
  photo_type: string | null;
  scores: Record<string, number>;
  reasons: Reason[];
  allow_status_update?: boolean; // false = foto ditolak: aplikasi pengiriman JANGAN update status, minta foto ulang
  courier_message?: string | null; // pesan siap tampil untuk kurir (ID/EN), null = tidak perlu menampilkan apa pun
  output_image?: OutputImage | null; // hanya dari upload file: foto yang disimpan aplikasi (null = ditolak)
  object?: { bucket: string; key: string } | null; // lokasi foto di SILO aplikasi (jika dilaporkan)
  retake_required?: boolean; // true = kualitas tidak memenuhi syarat (verdict REJECT)
  retake_message?: string | null; // pesan foto ulang (ID/EN), terisi jika retake_required
  evidence_eligible?: boolean;
  urls?: { original: string | null; processed: string | null; thumbnail: string | null; expires_in: number };
  poll_url?: string;
  [k: string]: unknown;
}

export type Result<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; unavailable: boolean; error: string };

export type PhotoResult = Result<Photo> & { photo?: Photo };

/** Foto hasil engine di respons API (base64). */
export interface OutputImage { content_type: string; bytes: number; optimized: boolean; sha256: string; base64: string }

/** Foto yang disimpan aplikasi ke SILO. optimized false = foto asli sudah memenuhi syarat, dikembalikan apa adanya. */
export interface ImageToStore { data: Uint8Array; contentType: string; optimized: boolean; sha256: string }

/** Keputusan siap pakai untuk aplikasi pengiriman (hasil checkObject / checkFile). */
export interface Decision {
  allowStatusUpdate: boolean; // false = foto ditolak: JANGAN update status pengiriman, minta foto ulang
  message: string | null; // tampilkan ke kurir jika tidak null
  retakeRequired: boolean;
  decided: boolean; // false = belum ada keputusan (engine mati/lambat/error) -> fail-open, status boleh diupdate
  photoId: string | null;
  verdict: Verdict | null;
  image: ImageToStore | null; // foto diterima: simpan INI ke SILO. null = ditolak, atau engine tidak menjawab (simpan foto asli)
  error?: string;
}

/** Ubah hasil API menjadi Decision. Tanpa hasil (engine mati, timeout, error) -> fail-open. */
export function toDecision(r: PhotoResult): Decision {
  if (!r.ok) return { allowStatusUpdate: true, message: null, retakeRequired: false, decided: false, photoId: null, verdict: null, image: null, error: r.error };
  const p = r.data;
  if (p.status === "pending") return { allowStatusUpdate: true, message: null, retakeRequired: false, decided: false, photoId: p.id, verdict: null, image: null };
  return {
    allowStatusUpdate: p.allow_status_update ?? p.action !== "reject",
    message: p.courier_message ?? null,
    retakeRequired: !!p.retake_required,
    decided: true,
    photoId: p.id,
    verdict: p.verdict,
    image: decodeImage(p.output_image),
  };
}

function decodeImage(o: OutputImage | null | undefined): ImageToStore | null {
  if (!o) return null;
  const bin = atob(o.base64);
  const data = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) data[i] = bin.charCodeAt(i);
  return { data, contentType: o.content_type, optimized: o.optimized, sha256: o.sha256 };
}

export interface ClientOptions {
  baseUrl: string;
  apiKey: string;
  timeoutMs?: number; // batas waktu HTTP, default 5000
  lang?: "id" | "en"; // bahasa pesan; default mengikuti engine
  tenant?: string; // hanya untuk API key yang tidak terikat tenant
}

export class PhotoQualityClient {
  private readonly base: string;
  private readonly opts: ClientOptions;

  constructor(opts: ClientOptions) {
    this.opts = opts;
    this.base = opts.baseUrl.replace(/\/+$/, "");
  }

  /** Alternatif jika foto sudah disimpan aplikasi ke SILO: engine hanya membacanya. Kirim key-nya, tunggu keputusan maks. timeoutMs (default 3000). Tidak pernah throw. */
  async checkObject(objectKey: string, fields: PhotoFields = {}, o: { timeoutMs?: number; bucket?: string } = {}): Promise<Decision> {
    return toDecision(await this.analyzeObject(objectKey, fields, { sync: true, timeoutMs: o.timeoutMs ?? 3000, bucket: o.bucket }));
  }

  /**
   * CARA UTAMA. Kirim file ke engine; engine memeriksa dan mengoptimasi, TIDAK menyimpan apa pun.
   * Diterima -> `image` = foto yang disimpan aplikasi ke SILO. Ditolak -> `allowStatusUpdate: false` + `message`.
   * `decided: false` (engine mati/lambat) -> simpan foto asli seperti sekarang, lalu lanjut.
   * `objectKey` (opsional): key SILO yang akan dipakai aplikasi, supaya Web UI/evidence engine bisa menampilkan fotonya.
   */
  async checkFile(file: Blob, filename: string, fields: PhotoFields = {}, o: { timeoutMs?: number; objectKey?: string; bucket?: string } = {}): Promise<Decision> {
    return toDecision(await this.analyzeFile(file, filename, fields, { timeoutMs: o.timeoutMs ?? 3000, objectKey: o.objectKey, bucket: o.bucket }));
  }

  /** Opsional: laporkan key SILO setelah foto disimpan (jika belum dikirim sebagai objectKey di checkFile). */
  async reportStored(photoId: string, objectKey: string, bucket?: string): Promise<PhotoResult> {
    return withPhoto(await this.request<Photo>(`/v1/photos/${encodeURIComponent(photoId)}/stored${this.query({})}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ object_key: objectKey, bucket }),
    }));
  }

  /** Foto sudah diupload ke SILO oleh aplikasi; engine cukup diberi key-nya. Paling hemat kuota kurir. */
  async analyzeObject(
    objectKey: string,
    fields: PhotoFields = {},
    o: { sync?: boolean; timeoutMs?: number; bucket?: string } = {},
  ): Promise<PhotoResult> {
    const q = this.query({ sync: o.sync ? "true" : undefined, timeout_ms: o.timeoutMs });
    const res = await this.request<Photo>(`/v1/analyze${q}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ object_key: objectKey, bucket: o.bucket, ...fields }),
    }, (o.timeoutMs ?? 3000) + 2000);
    return withPhoto(res);
  }

  /** Kirim file ke engine untuk dianalisis (selalu langsung, maks. timeoutMs). Engine tidak menyimpan file-nya. */
  async analyzeFile(
    file: Blob,
    filename: string,
    fields: PhotoFields = {},
    o: { timeoutMs?: number; objectKey?: string; bucket?: string } = {},
  ): Promise<PhotoResult> {
    const fd = new FormData();
    fd.append("file", file, filename);
    for (const [k, v] of Object.entries(fields)) if (v !== undefined && v !== null) fd.append(k, String(v));
    if (o.objectKey) fd.append("object_key", o.objectKey);
    if (o.bucket) fd.append("bucket", o.bucket);
    const q = this.query({ timeout_ms: o.timeoutMs });
    const res = await this.request<Photo>(`/v1/analyze${q}`, { method: "POST", body: fd }, (o.timeoutMs ?? 3000) + 15000);
    return withPhoto(res);
  }

  async getPhoto(id: string): Promise<PhotoResult> {
    return withPhoto(await this.request<Photo>(`/v1/photos/${encodeURIComponent(id)}${this.query({})}`));
  }

  /** Tunggu sampai hasil analisis tersedia (untuk mode async). */
  async waitForResult(id: string, o: { timeoutMs?: number; intervalMs?: number } = {}): Promise<PhotoResult> {
    const deadline = Date.now() + (o.timeoutMs ?? 15000);
    let last: PhotoResult = await this.getPhoto(id);
    while (last.ok && last.data.status === "pending" && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, o.intervalMs ?? 1000));
      last = await this.getPhoto(id);
    }
    return last;
  }

  async findByAwb(awb: string): Promise<Result<{ total: number; items: Photo[] }>> {
    return this.request(`/v1/photos${this.query({ awb })}`);
  }

  /** Data bukti pengiriman (hanya foto layak) untuk ditampilkan ke pelanggan. */
  async evidence(awb: string): Promise<Result<{ awb: string; count: number; photos: unknown[] }>> {
    return this.request(`/v1/evidence/${encodeURIComponent(awb)}`);
  }

  private query(params: Record<string, string | number | undefined>): string {
    const p = new URLSearchParams();
    if (this.opts.tenant) p.set("tenant", this.opts.tenant);
    if (this.opts.lang) p.set("lang", this.opts.lang);
    for (const [k, v] of Object.entries(params)) if (v !== undefined) p.set(k, String(v));
    const s = p.toString();
    return s ? `?${s}` : "";
  }

  private async request<T>(path: string, init: RequestInit = {}, timeoutMs?: number): Promise<Result<T>> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs ?? this.opts.timeoutMs ?? 5000);
    try {
      const res = await fetch(this.base + path, {
        ...init,
        signal: ctrl.signal,
        headers: { "X-API-Key": this.opts.apiKey, ...(this.opts.lang ? { "Accept-Language": this.opts.lang } : {}), ...(init.headers || {}) },
      });
      const body = (await res.json().catch(() => null)) as any;
      if (res.ok) return { ok: true, status: res.status, data: body as T };
      return { ok: false, status: res.status, unavailable: res.status >= 500, error: body?.error?.message ?? res.statusText };
    } catch (e) {
      // timeout / jaringan putus -> fail-open
      return { ok: false, status: 0, unavailable: true, error: String((e as Error)?.message ?? e) };
    } finally {
      clearTimeout(timer);
    }
  }
}

function withPhoto(r: Result<Photo>): PhotoResult {
  return r.ok ? { ...r, photo: r.data } : r;
}

/**
 * Verifikasi tanda tangan webhook (Node.js). Panggil dengan body MENTAH (string/Buffer), bukan hasil JSON.parse.
 *   const ok = await verifyWebhook(secret, req.headers["x-pqe-timestamp"], rawBody, req.headers["x-pqe-signature"]);
 */
export async function verifyWebhook(
  secret: string,
  timestamp: string | undefined,
  rawBody: string | Uint8Array,
  signature: string | undefined,
  toleranceSec = 300,
): Promise<boolean> {
  if (!timestamp || !signature) return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > toleranceSec) return false; // cegah replay
  const { createHmac, timingSafeEqual } = await import("node:crypto");
  const mac = createHmac("sha256", secret).update(`${timestamp}.`).update(rawBody).digest("hex");
  const expected = Buffer.from(`sha256=${mac}`);
  const given = Buffer.from(signature);
  return expected.length === given.length && timingSafeEqual(expected, given);
}
