# Referensi API v1

Referensi lengkap endpoint Photo Quality Engine. Versi interaktif (bisa langsung dicoba dari browser) ada di
`<base-url>/docs` (Swagger) dan `<base-url>/redoc`. Skema mesin: `<base-url>/openapi.json`.

Cara memakai API dari aplikasi (alur, contoh kode, fail-open): [integration-guide.md](integration-guide.md).

## Konvensi

| Hal | Aturan |
|---|---|
| Base URL | Produksi sesuai ingress (misal `https://photo-quality.pcpexpress.com`), lokal `http://localhost:8000` |
| Autentikasi | Header `X-API-Key: pqe_...` atau `Authorization: Bearer pqe_...` |
| Bahasa pesan | Header `Accept-Language: id` / `en`, atau query `?lang=id` / `?lang=en`. Default `PQE_DEFAULT_LANG` (id) |
| Format | JSON UTF-8, kecuali upload (`multipart/form-data`), config tenant (`application/x-yaml`), dan PDF |
| Waktu | ISO-8601 dengan zona waktu, disimpan dalam UTC |
| Pagination | `limit` (maks. 200) + `offset`. Respons berisi `total` |
| Error | `{"error": {"code": "KODE", "message": "penjelasan"}}`. Daftar kode: [integration-guide.md#10-kode-error](integration-guide.md#10-kode-error) |
| Versi | Prefix `/v1`. Perubahan yang tidak kompatibel akan memakai `/v2`; field baru bisa ditambahkan kapan saja (abaikan field yang tidak dikenal) |

## Role & hak akses

| Endpoint | admin | ops | viewer | app | customer | publik |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `POST /v1/analyze` | ✓ | ✓ | | ✓ (tenant sendiri) | | |
| `POST /v1/analyze/preview` | ✓ | ✓ | | ✓ | | |
| `GET /v1/photos`, `GET /v1/photos/{id}` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | | |
| `POST /v1/photos/{id}/review`, `/reprocess` | ✓ | ✓ | | | | |
| `GET /v1/photos/{id}/reviews` | ✓ | ✓ | ✓ | | | |
| `GET /v1/evidence/...`, `POST /v1/evidence/batch` | ✓ | ✓ | | ✓ (tenant sendiri) | ✓ (customer_id sendiri) | |
| `GET /v1/stats` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | | |
| `GET /v1/admin/tenants`, `/tenants/{code}`, `/history`, `/profiles/builtin`, `/jobs`, `/sources` | ✓ | ✓ | ✓ | | | |
| `PUT/POST /v1/admin/tenants...`, `/api-keys`, `/jobs/.../retry`, `/audit` | ✓ | | | | | |
| `GET /v1/verify/{sha256}`, `/v1/meta/*`, `/healthz`, `/readyz`, `/metrics` | | | | | | ✓ |
| `GET /v1/me` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `POST /v1/events/s3` | token `PQE_S3_EVENT_TOKEN` (dipakai SILO) | | | | | |

API key `admin`/`ops`/`viewer` tanpa tenant bisa melihat semua tenant. Untuk endpoint yang butuh satu tenant
(misal `POST /v1/analyze`), tambahkan `?tenant=<kode>`.

---

## Analisis

### `POST /v1/analyze`

Mendaftarkan foto untuk dianalisis. Idempotent per objek (bucket + key): memanggil ulang untuk objek yang sama
mengembalikan foto yang sama.

**Query:** `tenant`, `sync` (bool, default `false`), `timeout_ms` (100-30000, default `PQE_SYNC_TIMEOUT_MS` = 3000), `lang`.

**Bentuk A: referensi objek** (`Content-Type: application/json`)

```json
{
  "object_key": "pod/2026/10/22/AWB123_K045_1729580000.jpg",
  "bucket": "ekas-pcptransport",
  "awb": "AWB123",
  "photo_type": "pod_recipient",
  "courier_id": "K045",
  "customer_id": "CUST-001",
  "branch": "JKT01",
  "captured_at": "2026-10-22T08:13:20+07:00",
  "lat": -6.2001,
  "lng": 106.8166,
  "source": "camera",
  "external_id": "foto-998877"
}
```

Hanya `object_key` yang wajib. `bucket` default `AWS_BUCKET`. Field yang dikirim eksplisit selalu menang atas
metadata objek dan aturan mapping tenant.

**Bentuk B: upload file** (`multipart/form-data`): field `file` (JPEG/PNG/HEIC/WebP, maks. `PQE_MAX_UPLOAD_MB`)
ditambah field metadata yang sama seperti di atas (semua opsional). File disimpan engine ke bucket upload tenant
(`upload_bucket` atau `ENGINE_BUCKET_UPLOAD`).

**Respons:** `200` (hasil ada) atau `202` (masih diproses, ada `poll_url`). Isi: [objek foto](#objek-foto).

### `POST /v1/analyze/preview`

Menganalisis file **tanpa menyimpan apa pun** (tidak ada di DB maupun SILO). Untuk kalibrasi dan onboarding.

**Query:** `tenant` (opsional, pakai profile tenant), `photo_type` (nama profile), `lang`.
**Body:** `multipart/form-data` field `file`.

**Respons:**

```json
{
  "verdict": "WARN", "action": "log", "profile": "default", "mode": "shadow",
  "scores": {"sharpness": 29.9, "resolution": 100.0, "exposure": 100.0, "authenticity": 100.0},
  "reasons": [{"code": "BLURRY", "check": "sharpness", "severity": "warn", "score": 29.9, "detail": {},
               "message": "Foto buram", "suggestion": "Pegang HP dengan stabil, ..."}],
  "image": {"format": "JPEG", "width": 2400, "height": 1800, "bytes": 196903, "sha256": "...", "phash": "c3d1..."},
  "metrics": {"sharpness": {"laplacian_var_top": 10.6, "laplacian_var_median": 4.1}, "resolution": {...}, "exposure": {...}},
  "exif": {},
  "compression": {"passthrough": true, "original_bytes": 196903, "output_bytes": 196903, "ratio": 1.0,
                  "width": 2400, "height": 1800, "quality": null, "ssim": null, "target_met": true},
  "timings_ms": {"decode": 32, "sharpness": 29, "resolution": 30, "exposure": 9, "exif": 0, "duplicate": 11, "compress": 0, "total": 160}
}
```

---

## Foto

### `GET /v1/photos`

Mencari foto. Diurutkan dari yang terbaru diterima.

| Query | Contoh | Keterangan |
|---|---|---|
| `tenant` | `courier-app` | |
| `awb`, `courier_id`, `branch`, `customer_id`, `external_id` | `AWB123` | Pencocokan persis |
| `verdict` | `PASS` / `WARN` / `REJECT` | |
| `status` | `pending` / `done` / `error` | |
| `review_status` | `none` / `approved` / `retake_requested` / `fraud` | |
| `needs_review` | `true` | Hanya WARN/REJECT/error yang belum direview (antrean ops) |
| `reason` | `BLURRY` | Foto yang punya kode alasan ini |
| `date_from`, `date_to` | `2026-10-01` | Tanggal diterima (UTC), inklusif |
| `limit`, `offset` | `50`, `0` | Maks. `limit` 200 |

**Respons:** `{"total": 123, "limit": 50, "offset": 0, "items": [<objek foto ringkas>]}`

### `GET /v1/photos/{id}`

Detail lengkap: [objek foto](#objek-foto) + field detail (`compression`, `exif`, `metrics`, `image`, `location`,
`input_fields`, `config_version`, `duration_ms`, `phash`, `ingest_source`, `capture_source`).

### `POST /v1/photos/{id}/review`

Keputusan ops. Body: `{"action": "approve" | "request_retake" | "mark_fraud" | "reset", "note": "opsional"}`.

| `action` | `review.status` | Efek ke evidence pelanggan |
|---|---|---|
| `approve` | `approved` | Foto WARN/REJECT ikut tampil di evidence |
| `request_retake` | `retake_requested` | Tidak tampil |
| `mark_fraud` | `fraud` | Tidak tampil |
| `reset` | `none` | Kembali ke aturan verdict (hanya PASS yang tampil) |

Memicu webhook `review.updated` dan tercatat di audit log.

### `POST /v1/photos/{id}/reprocess`

Antrekan analisis ulang (misal setelah threshold diubah). Respons `{"id": "...", "status": "pending"}`.

### `GET /v1/photos/{id}/reviews`

Riwayat keputusan review: `[{"action", "note", "actor", "at"}]`.

---

## Evidence & verifikasi

| Endpoint | Body / query | Respons |
|---|---|---|
| `GET /v1/evidence/{awb}` | `lang` | `{"awb", "count", "photos": [{"photo_id", "awb", "photo_type", "courier_id", "captured_at", "received_at", "location", "image_url", "sha256", "verify_url"}], "lang"}` |
| `GET /v1/evidence/{awb}/pdf` | `lang` | `application/pdf`, satu halaman per foto layak |
| `POST /v1/evidence/batch` | `{"awbs": ["AWB1", "AWB2"]}` (1-500 resi), `lang` | `application/pdf` |
| `GET /v1/verify/{sha256}` | - | `{"found": true, "received_at": "...", "awb_suffix": "B123", "message": "..."}` atau `{"found": false}` |

`image_url` adalah presigned URL dengan masa berlaku `ENGINE_PRESIGN_TTL_CUSTOMER` (default 7 hari).
Hanya foto `PASS` atau yang di-approve ops yang muncul.

---

## Statistik & metadata

### `GET /v1/stats?tenant=&days=14`

```json
{
  "days": 14, "total": 18,
  "by_verdict": {"PASS": 2, "WARN": 7, "REJECT": 9},
  "by_status": {"done": 18},
  "review_open": 16,
  "daily": [{"date": "2026-10-22", "PASS": 2, "WARN": 7, "REJECT": 9, "pending": 0}],
  "top_couriers": [{"courier_id": "K004", "count": 6}],
  "top_branches": [{"branch": "SBY03", "count": 9}],
  "top_reasons": [{"code": "BLURRY", "count": 5}]
}
```

### `GET /v1/meta/reason-codes?lang=id`

`[{"code": "BLURRY", "message": "Foto buram", "suggestion": "..."}]`

### `GET /v1/meta/fields`

Daftar field metadata yang dikenali: `awb`, `courier_id`, `photo_type`, `customer_id`, `branch`, `captured_at`,
`lat`, `lng`, `source`, `external_id`.

### `GET /v1/me`

`{"name": "...", "role": "app", "tenant": "courier-app", "customer_id": null}`

---

## Admin

Semua butuh role `admin`, kecuali endpoint baca yang juga boleh `ops`/`viewer` (lihat tabel role).

| Method & path | Body | Keterangan |
|---|---|---|
| `GET /v1/admin/tenants` | - | Ringkasan tenant (versi, source, mode per profile, status) |
| `GET /v1/admin/tenants/{code}` | - | YAML + `resolved_profiles` (profile setelah digabung dengan bawaan) |
| `POST /v1/admin/tenants/validate` | YAML (`Content-Type: application/x-yaml`) | Validasi tanpa menyimpan |
| `PUT /v1/admin/tenants` | YAML | Buat/perbarui. Respons `{"code", "version", "created"}` |
| `POST /v1/admin/tenants/{code}/enable` / `disable` | - | Nonaktif = ingest & analyze ditolak, data tetap |
| `GET /v1/admin/tenants/{code}/history` | - | Semua versi config |
| `GET /v1/admin/profiles/builtin` | - | Profile bawaan engine |
| `GET /v1/admin/api-keys` | - | Daftar key (prefix saja, tanpa nilai) |
| `POST /v1/admin/api-keys` | `{"name", "role", "tenant"?, "customer_id"?}` | Nilai `api_key` **hanya muncul sekali** |
| `DELETE /v1/admin/api-keys/{id}` | - | Cabut key (langsung berlaku) |
| `GET /v1/admin/jobs?status=dead` | - | Statistik antrean + daftar job (`queued`/`running`/`done`/`dead`) |
| `POST /v1/admin/jobs/{id}/retry` | - | Ulangi satu job |
| `POST /v1/admin/jobs/retry-dead` | - | Ulangi semua job dead |
| `GET /v1/admin/sources` | - | Status source ingest (terakhir jalan, error, checkpoint) |
| `GET /v1/admin/audit?limit=100` | - | Audit log |

Contoh mengubah config:

```bash
curl -X PUT "$PQE_URL/v1/admin/tenants" -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/x-yaml" --data-binary @config/tenants/courier-app.yaml
```

---

## Operasional

| Endpoint | Keterangan |
|---|---|
| `GET /healthz` | Liveness. `200 {"status": "ok", "version": "..."}` jika proses hidup |
| `GET /readyz` | Readiness. `200` jika DB & SILO bisa diakses, `503` + detail jika tidak |
| `GET /metrics` | Metrik Prometheus (lihat [operations.md](operations.md)) |
| `POST /v1/events/s3` | Penerima bucket notification SILO. Header `Authorization: Bearer <PQE_S3_EVENT_TOKEN>`. Respons `{"accepted", "ignored"}` |

---

## Objek foto

| Field | Tipe | Keterangan |
|---|---|---|
| `id` | string | ID foto di engine |
| `tenant` | string | Kode tenant |
| `status` | `pending` / `done` / `error` | |
| `verdict` | `PASS` / `WARN` / `REJECT` / null | null selama `pending` |
| `verdict_text` | string | Teks verdict sesuai bahasa |
| `action` | `log` / `notify` / `reject` / null | Saran perilaku aplikasi sesuai mode profile |
| `photo_type`, `profile` | string | Jenis foto & profile yang dipakai |
| `awb`, `courier_id`, `customer_id`, `branch`, `external_id` | string / null | Hasil mapping metadata |
| `captured_at`, `received_at`, `analyzed_at` | ISO-8601 / null | |
| `scores` | objek | `sharpness`, `resolution`, `exposure`, `ocr`, `authenticity` (0-100, tinggi = baik) + `authenticity_risk` (0-100, tinggi = mencurigakan). Hanya check yang aktif |
| `reasons[]` | array | `{code, check, severity, score, detail, message, suggestion}`, urut `reject` → `warn` → `info` |
| `review` | objek | `{status, by, at, note}` |
| `evidence_eligible` | bool | Apakah foto tampil di evidence pelanggan |
| `object` | objek | `{bucket, key}` lokasi foto original |
| `sha256` | string | Hash file original (untuk verifikasi) |
| `error` | objek / null | `{code, message}` jika `status = error` |
| `urls` | objek | Presigned URL `original`, `processed` (kompres), `thumbnail`, `expires_in` (detik). Tidak ada di webhook |
| `poll_url` | string | Hanya pada respons `202` |

---

## Kode alasan

`severity` menunjukkan tingkat pada foto tersebut (`reject`/`warn` tergantung skor dan threshold profile,
`info` = informasi saja).

| Kode | Check | Pesan (ID) | Message (EN) |
|---|---|---|---|
| `BLURRY` | sharpness | Foto buram | The photo is blurry |
| `LOW_RESOLUTION` | resolution | Resolusi foto terlalu kecil | The photo resolution is too low |
| `PIXELATED` | resolution | Foto pecah (kotak-kotak) | The photo is pixelated |
| `HEAVY_COMPRESSION` | resolution | Kualitas foto rusak karena kompresi berlebihan | The photo quality is damaged by heavy compression |
| `TOO_DARK` | exposure | Foto terlalu gelap | The photo is too dark |
| `TOO_BRIGHT` | exposure | Foto terlalu terang | The photo is too bright |
| `GLARE` | exposure | Ada pantulan cahaya yang menutupi foto | Light reflection covers part of the photo |
| `LOW_CONTRAST` | exposure | Foto pudar / kontras rendah | The photo is faded / low contrast |
| `NO_TEXT_DETECTED` | ocr | Tidak ada teks yang bisa dibaca | No readable text was found |
| `TEXT_UNREADABLE` | ocr | Teks pada foto sulit dibaca | The text in the photo is hard to read |
| `EDITING_SOFTWARE` | authenticity | Foto terindikasi diedit dengan aplikasi editing | The photo appears to have been edited with an editing app |
| `OLD_PHOTO` | authenticity | Foto diambil jauh sebelum dikirim | The photo was taken long before it was sent |
| `CAPTURE_TIME_MISMATCH` | authenticity | Waktu foto tidak sesuai dengan waktu yang dilaporkan aplikasi | The photo time does not match the time reported by the app |
| `GPS_MISMATCH` | authenticity | Lokasi foto berbeda dengan lokasi pengiriman | The photo location differs from the delivery location |
| `DUPLICATE_EXACT` | authenticity | Foto identik sudah pernah dipakai untuk pengiriman lain | An identical photo was already used for another delivery |
| `DUPLICATE_SIMILAR` | authenticity | Foto sangat mirip dengan foto pengiriman lain | The photo is very similar to another delivery's photo |
| `EXIF_MISSING` | exif (info) | Metadata kamera tidak tersedia | Camera metadata is not available |
| `OCR_UNAVAILABLE` | ocr (info) | Pemeriksaan teks (OCR) tidak aktif di server ini | Text check (OCR) is not enabled on this server |
| `COMPRESSION_TARGET_NOT_MET` | compression (info) | Ukuran file belum bisa diturunkan ke target tanpa merusak keterbacaan | The file size could not reach the target without hurting readability |
| `UNREADABLE_FILE` | decode | File tidak bisa dibuka sebagai gambar | The file cannot be opened as an image |

Saran (`suggestion`) untuk setiap kode bisa dilihat lewat `GET /v1/meta/reason-codes`.
Kode alasan keaslian (`authenticity`) tidak pernah menyebabkan `REJECT` dengan konfigurasi bawaan.

## Webhook

Format, header, tanda tangan, dan aturan retry: [integration-guide.md#8-menerima-webhook](integration-guide.md#8-menerima-webhook).
