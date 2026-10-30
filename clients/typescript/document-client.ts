/**
 * Client modul OCR dokumen (invoice dan jenis dokumen lain). Tanpa dependency: fetch + Web Crypto (Node 22+, Deno,
 * Bun). Panggil HANYA dari server: api_secret tidak boleh ada di browser/aplikasi HP.
 *
 *   const ocr = new DocumentOcrClient({ baseUrl: process.env.PQE_URL!, apiKey: process.env.PQE_API_KEY!,
 *                                       apiSecret: process.env.PQE_API_SECRET! });
 *   const job = await ocr.submit(pdfBytes, "invoice.pdf", "invoice", { templateHint: npwpVendor });
 *   const res = await ocr.waitFor(job.body.job_id);       // atau getJob() dari queue worker Anda
 *   if (res.ok && res.body.status === "succeeded") console.log(res.body.fields);
 *
 * Referensi: docs/api-reference.md#dokumen, docs/integration-guide.md#13-integrasi-modul-dokumen
 */

export const POLL_SCHEDULE = [3, 5, 10, 15, 30]; // detik, lalu tiap 60

export interface DocumentOcrOptions {
  baseUrl: string;
  apiKey: string;
  apiSecret: string;
  timeoutPostMs?: number;
  timeoutGetMs?: number;
  sleep?: (seconds: number) => Promise<void>;
}

export interface ApiResult<T = any> {
  ok: boolean;
  status: number;
  body: T;
}

export interface Correction {
  field: string;
  extracted?: unknown;
  corrected: unknown;
  idempotency_key?: string;
}

const enc = new TextEncoder();

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(data: Uint8Array | string): Promise<string> {
  const bytes = typeof data === "string" ? enc.encode(data) : data;
  return hex(await crypto.subtle.digest("SHA-256", bytes));
}

/** X-Signature = hex(HMAC-SHA256(secret, ts \n METHOD \n path \n hex(sha256(body)))). path tanpa query. */
export async function sign(secret: string, timestamp: string, method: string, path: string,
                           body: Uint8Array | string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false,
                                            ["sign"]);
  const msg = `${timestamp}\n${method.toUpperCase()}\n${path}\n${await sha256Hex(body)}`;
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
}

/** Verifikasi callback dari engine (header X-Timestamp, X-Signature; path = path URL callback Anda). */
export async function verifyCallback(secret: string, timestamp: string, signature: string, path: string,
                                     body: Uint8Array | string, toleranceSec = 300): Promise<boolean> {
  if (!/^\d+$/.test(timestamp) || Math.abs(Date.now() / 1000 - Number(timestamp)) > toleranceSec) return false;
  const expected = await sign(secret, timestamp, "POST", path, body);
  const given = signature.toLowerCase();
  if (expected.length !== given.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ given.charCodeAt(i);
  return diff === 0;
}

/** Kunci idempotensi yang disarankan: sha256(job_id \n field \n JSON(extracted) \n JSON(corrected)). */
export function correctionKey(jobId: string, field: string, extracted: unknown, corrected: unknown): Promise<string> {
  return sha256Hex(`${jobId}\n${field}\n${JSON.stringify(extracted ?? null)}\n${JSON.stringify(corrected ?? null)}`);
}

/** Body multipart + Content-Type. Body inilah yang ditandatangani dan dikirim apa adanya. */
export function multipart(fields: Record<string, string>, filename: string,
                          content: Uint8Array): { body: Uint8Array; contentType: string } {
  const boundary = "pqe" + [...crypto.getRandomValues(new Uint8Array(12))].map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  let head = "";
  for (const [name, value] of Object.entries(fields)) {
    head += `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`;
  }
  const safe = filename.replace(/["\r\n]/g, "_");
  head += `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${safe}"\r\n` +
    "Content-Type: application/octet-stream\r\n\r\n";
  const a = enc.encode(head), b = enc.encode(`\r\n--${boundary}--\r\n`);
  const body = new Uint8Array(a.length + content.length + b.length);
  body.set(a, 0);
  body.set(content, a.length);
  body.set(b, a.length + content.length);
  return { body, contentType: `multipart/form-data; boundary=${boundary}` };
}

export class DocumentOcrClient {
  private o: Required<DocumentOcrOptions>;

  constructor(opts: DocumentOcrOptions) {
    this.o = {
      timeoutPostMs: 60_000,
      timeoutGetMs: 10_000,
      sleep: (s) => new Promise((r) => setTimeout(r, s * 1000)),
      ...opts,
      baseUrl: opts.baseUrl.replace(/\/+$/, ""),
    };
  }

  /** Kirim dokumen. templateHint = ID penerbit jika diketahui (invoice: NPWP vendor). */
  submit(content: Uint8Array, filename: string, documentType: string,
         opts: { templateHint?: string; callbackUrl?: string } = {}): Promise<ApiResult> {
    const fields: Record<string, string> = { document_type: documentType };
    if (opts.templateHint) fields.template_hint = opts.templateHint;
    if (opts.callbackUrl) fields.callback_url = opts.callbackUrl;
    const { body, contentType } = multipart(fields, filename, content);
    return this.request("POST", "/v1/jobs", body, contentType, this.o.timeoutPostMs);
  }

  getJob(jobId: string): Promise<ApiResult> {
    return this.request("GET", `/v1/jobs/${encodeURIComponent(jobId)}`, new Uint8Array(), undefined,
                        this.o.timeoutGetMs);
  }

  /** Polling 3, 5, 10, 15, 30 dtk lalu tiap 60 dtk sampai succeeded/failed atau maxSeconds habis. */
  async waitFor(jobId: string, maxSeconds = 600): Promise<ApiResult> {
    let waited = 0;
    for (let i = 0; ; i++) {
      const delay = POLL_SCHEDULE[i] ?? 60;
      if (waited + delay > maxSeconds) return this.getJob(jobId);
      await this.o.sleep(delay);
      waited += delay;
      const r = await this.getJob(jobId);
      if (r.ok && (r.body.status === "succeeded" || r.body.status === "failed")) return r;
    }
  }

  /** Kirim koreksi pengguna (engine belajar template per penerbit). Aman dikirim ulang (idempoten). */
  async sendCorrections(documentType: string, issuerId: string, jobId: string,
                        corrections: Correction[]): Promise<ApiResult> {
    const items = await Promise.all(corrections.map(async (c) => ({
      ...c,
      idempotency_key: c.idempotency_key ?? await correctionKey(jobId, c.field, c.extracted, c.corrected),
    })));
    const keys = items.map((c) => c.idempotency_key!).sort();
    const body = enc.encode(JSON.stringify({ document_type: documentType, issuer: { id: issuerId }, job_id: jobId,
                                             corrections: items }));
    return this.request("POST", "/v1/templates", body, "application/json", this.o.timeoutGetMs,
                        { "Idempotency-Key": await sha256Hex(keys.join("\n")) });
  }

  private async request(method: string, path: string, body: Uint8Array, contentType: string | undefined,
                        timeoutMs: number, extra: Record<string, string> = {}): Promise<ApiResult> {
    const ts = String(Math.floor(Date.now() / 1000));
    const headers: Record<string, string> = {
      "X-Api-Key": this.o.apiKey,
      "X-Timestamp": ts,
      "X-Signature": await sign(this.o.apiSecret, ts, method, path, body),
      Accept: "application/json",
      ...extra,
    };
    if (contentType) headers["Content-Type"] = contentType;
    try {
      const r = await fetch(this.o.baseUrl + path, {
        method, headers, body: method === "GET" ? undefined : body, redirect: "manual",
        signal: AbortSignal.timeout(timeoutMs),
      });
      const text = await r.text();
      let parsed: any = {};
      try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = {}; }
      return { ok: r.status >= 200 && r.status < 300, status: r.status, body: parsed };
    } catch (e) {
      return { ok: false, status: 0, body: { error: { code: "UNAVAILABLE", message: String(e) } } };
    }
  }
}
