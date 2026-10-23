/**
 * Client Photo Quality Engine untuk TypeScript / JavaScript (Node 18+, browser, React Native, Deno, Bun).
 * Tanpa dependency. Salin file ini ke project Anda.
 *
 * PRINSIP: engine TIDAK BOLEH menghambat proses kurir. Semua method di sini "fail-open":
 * jika engine lambat/mati, method mengembalikan { ok: false, unavailable: true } dan TIDAK melempar error.
 * Lanjutkan proses bisnis seperti biasa.
 *
 * Contoh:
 *   const pqe = new PhotoQualityClient({ baseUrl: "https://photo-quality.pcpexpress.com", apiKey: process.env.PQE_API_KEY! });
 *   const r = await pqe.analyzeObject("pod/2026/10/22/AWB123.jpg", { awb: "AWB123", photo_type: "pod_recipient" }, { sync: true });
 *   if (r.ok && r.photo.verdict === "REJECT") tampilkanPesan(r.photo.reasons);
 */

export type Verdict = "PASS" | "WARN" | "REJECT";

export interface PhotoFields {
  awb?: string;
  photo_type?: string; // pod_recipient | pod_document | package | goods | location | ...
  courier_id?: string;
  customer_id?: string;
  branch?: string;
  captured_at?: string; // ISO-8601, contoh new Date().toISOString()
  lat?: number;
  lng?: number;
  source?: "camera" | "gallery";
  external_id?: string; // ID foto di aplikasi Anda
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
  retake_required?: boolean; // true = kualitas tidak bisa dimaksimalkan: minta kurir foto ulang (gabungkan dengan action)
  retake_message?: string | null; // pesan siap tampil (ID/EN), terisi jika retake_required
  evidence_eligible?: boolean;
  urls?: { original: string | null; processed: string | null; thumbnail: string | null; expires_in: number };
  poll_url?: string;
  [k: string]: unknown;
}

export type Result<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; unavailable: boolean; error: string };

export type PhotoResult = Result<Photo> & { photo?: Photo };

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

  /** Kirim file langsung ke engine (engine yang menyimpan ke SILO). */
  async analyzeFile(
    file: Blob,
    filename: string,
    fields: PhotoFields = {},
    o: { sync?: boolean; timeoutMs?: number } = {},
  ): Promise<PhotoResult> {
    const fd = new FormData();
    fd.append("file", file, filename);
    for (const [k, v] of Object.entries(fields)) if (v !== undefined && v !== null) fd.append(k, String(v));
    const q = this.query({ sync: o.sync ? "true" : undefined, timeout_ms: o.timeoutMs });
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
