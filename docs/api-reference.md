# Referensi API v1

Referensi lengkap endpoint Photo Quality Engine. Versi interaktif (bisa langsung dicoba dari browser) ada di
`<base-url>/docs` (Swagger) dan `<base-url>/redoc`. Skema mesin: `<base-url>/openapi.json`.

Cara memakai API dari aplikasi (alur, contoh kode, fail-open): [integration-guide.md](integration-guide.md), atau
versi interaktif tanpa login di `<base-url>/ui/guide.html`.

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
| `POST /v1/analyze/preview` | ✓ | ✓ | ✓ | ✓ | | |
| `GET /v1/photos`, `GET /v1/photos/{id}`, `GET /v1/photos/{id}/file` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | | |
| `POST /v1/photos/{id}/review`, `/reprocess` | ✓ | ✓ | | | | |
| `GET /v1/photos/{id}/reviews` | ✓ | ✓ | ✓ | | | |
| `GET /v1/evidence/...`, `POST /v1/evidence/batch` | ✓ | ✓ | | ✓ (tenant sendiri) | ✓ (customer_id sendiri) | |
| `GET /v1/stats` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | | |
| `GET /v1/admin/tenants`, `/tenants/{code}`, `/history`, `/profiles/builtin`, `/jobs`, `/webhooks`, `/sources` | ✓ | ✓ | ✓ | | | |
| `PUT/POST /v1/admin/tenants...`, `/api-keys`, `/jobs/.../retry`, `/webhooks/retry-dead`, `/audit` | ✓ | | | | | |
| `GET/PUT /v1/admin/settings/storage`, `POST /v1/admin/settings/storage/test` | ✓ | | | | | |
| `GET /v1/verify/{sha256}`, `/v1/meta/reason-codes`, `/v1/meta/fields`, `/healthz`, `/readyz`, `/metrics`, `/ui/guide.html`, `/ui/coba.html` (halaman; API-nya tetap butuh key) | | | | | | ✓ |
| `GET /v1/me`, `GET /v1/meta/profiles` | ✓ | ✓ | ✓ | ✓ | ✓ | |
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

**Query:** `tenant` (opsional, pakai profile tenant), `photo_type` (nama profile), `lang`, `include_output`
(`true` = sertakan foto hasil engine, yang sudah diperbaiki/dikompres, sebagai data URL di field `output_image`;
`null` jika foto disimpan apa adanya). Dipakai halaman Uji Coba Kamera untuk membandingkan sebelum/sesudah.
**Body:** `multipart/form-data` field `file`.

**Respons:**

```json
{
  "verdict": "WARN", "action": "log", "profile": "default", "mode": "shadow",
  "retake_required": false, "retake_message": null,
  "enhancement": {"attempted": false, "applied": false},
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

`retake_required`, `retake_message`, dan `enhancement` sama artinya dengan di [objek foto](#objek-foto). Jika
perbaikan otomatis dipakai, `scores`/`verdict` adalah hasil **setelah** perbaikan, `enhancement.before` berisi hasil
sebelumnya, dan `timings_ms.enhance` terisi.

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
| `needs_review` | `true` | Hanya WARN/REJECT/error yang belum direview (antrean ops). Tidak bergantung pada `review_required` tenant |
| `reason` | `BLURRY` | Foto yang punya kode alasan ini |
| `date_from`, `date_to` | `2026-10-01` | Tanggal diterima (UTC), inklusif |
| `limit`, `offset` | `50`, `0` | Maks. `limit` 200 |

**Respons:** `{"total": 123, "limit": 50, "offset": 0, "items": [<objek foto ringkas>]}`

### `GET /v1/photos/{id}/file?variant=processed`

Mengambil file foto **lewat engine**, untuk browser/HP yang tidak bisa menjangkau host SILO (presigned URL).
`variant`: `processed` (bawaan; versi kompres/hasil perbaikan, atau original jika tidak perlu dikompres),
`original`, `thumbnail`. Respons berisi gambar (`Content-Type` sesuai file). `404 FILE_NOT_FOUND` jika versi itu
tidak ada. Aplikasi produksi sebaiknya tetap memakai `urls.*` (presigned) agar tidak membebani engine.

### `GET /v1/photos/{id}`

Detail lengkap: [objek foto](#objek-foto) + field detail (`enhancement`, `compression`, `exif`, `metrics`, `image`,
`location`, `input_fields`, `config_version`, `duration_ms`, `phash`, `ingest_source`, `capture_source`).

`enhancement` (perbaikan pencahayaan otomatis, lihat
[integration-guide.md §7.1](integration-guide.md#71-perbaikan-otomatis-tanpa-ai)):

| Field | Keterangan |
|---|---|
| `attempted` | bool. Perbaikan dicoba (ada alasan `TOO_DARK`/`TOO_BRIGHT`/`LOW_CONTRAST`/`GLARE` dan `enhance.enabled`) |
| `applied` | bool. Hasil perbaikan dipakai untuk verdict dan versi processed |
| `operations` | contoh `["levels:12-140", "gamma:0.62"]` |
| `verified` | `global_monotonic_curve` jika lolos verifikasi eksak |
| `before` | `{verdict, scores, codes}` sebelum perbaikan |
| `reason` | Jika tidak dipakai: `nothing_to_fix` / `verification_failed` / `no_improvement` |

### `POST /v1/photos/{id}/review`

Keputusan ops. Body: `{"action": "approve" | "request_retake" | "mark_fraud" | "reset", "note": "opsional"}`.

| `action` | `review.status` | Efek ke evidence pelanggan |
|---|---|---|
| `approve` | `approved` | Foto WARN/REJECT ikut tampil di evidence, `retake_required` menjadi `false` |
| `request_retake` | `retake_requested` | Tidak tampil, `retake_required` menjadi `true` |
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
Foto yang muncul = foto dengan `evidence_eligible: true`: dengan `review_required: false` (bawaan tenant) `PASS` dan
`WARN`; dengan `review_required: true` hanya `PASS` atau yang di-approve ops. `REJECT` hanya muncul jika di-approve ops.
Gambar yang dipakai adalah versi processed (jika pencahayaan diperbaiki otomatis, versi yang sudah diperbaiki);
`sha256`/`verify_url` selalu merujuk ke file original.

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

### `GET /v1/meta/profiles?tenant=`

Daftar jenis foto (profile) dan mode-nya. Tanpa `tenant` memakai tenant milik API key; key tanpa tenant mendapat
profile bawaan.

```json
{"tenant": "uji-coba", "default_profile": "pod_recipient", "review_required": false,
 "profiles": [{"name": "pod_recipient", "mode": "enforce"}, {"name": "pod_document", "mode": "enforce"}]}
```

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
| `GET /v1/admin/webhooks?status=dead` | - | Daftar pengiriman webhook (`pending`/`done`/`dead`) beserta `last_error` |
| `POST /v1/admin/webhooks/retry-dead` | - | Kirim ulang semua webhook dead |
| `GET /v1/admin/sources` | - | Status source ingest (terakhir jalan, error, checkpoint) |
| `GET /v1/admin/audit?limit=100` | - | Audit log |
| `GET /v1/admin/settings/storage` | - | Konfigurasi SILO efektif + asal tiap nilai. Lihat [Pengaturan SILO](#pengaturan-silo) |
| `PUT /v1/admin/settings/storage` | JSON | Simpan konfigurasi SILO (berlaku tanpa restart, maks. 10 detik) |
| `POST /v1/admin/settings/storage/test` | JSON | Uji koneksi dengan nilai yang dikirim, tanpa menyimpan |

Contoh mengubah config:

```bash
curl -X PUT "$PQE_URL/v1/admin/tenants" -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/x-yaml" --data-binary @config/tenants/courier-app.yaml
```

### Pengaturan SILO

Penjelasan lengkap (prioritas, enkripsi, jebakan `127.0.0.1`): [configuration.md §1.6](configuration.md#16-pengaturan-silo-lewat-web-ui).

**`GET /v1/admin/settings/storage`**

```json
{
  "version": 3,
  "secret_key_configured": true,
  "fields": {
    "endpoint":          {"value": "https://s3.pcpexpress.com", "source": "database"},
    "public_endpoint":   {"value": null, "source": "default"},
    "region":            {"value": "us-east-1", "source": "env"},
    "access_key":        {"value": "pqe…01", "source": "database"},
    "secret_key":        {"value": {"set": true}, "source": "database"},
    "addressing_style":  {"value": "path", "source": "database"},
    "signature_version": {"value": "s3v4", "source": "env"},
    "app_bucket":        {"value": "ekas-pcptransport", "source": "env"},
    "processed_bucket":  {"value": "photos-processed", "source": "env"},
    "thumb_bucket":      {"value": "photos-thumb", "source": "env"},
    "upload_bucket":     {"value": "photos-upload", "source": "env"}
  }
}
```

`source`: `database` (disimpan lewat halaman/API ini), `env`, atau `default`. Secret key tidak pernah dikembalikan
(hanya `{"set": true/false}`); access key disamarkan. `secret_key_configured` = `PQE_SECRET_KEY` sudah diisi di server.

**`PUT /v1/admin/settings/storage`** dan **`POST /v1/admin/settings/storage/test`** menerima body yang sama. Semua
field opsional; field kosong/tidak dikirim = tidak diubah.

```json
{
  "endpoint": "https://s3.pcpexpress.com",
  "public_endpoint": "https://s3.pcpexpress.com",
  "region": "us-east-1",
  "access_key": "<access key>",
  "secret_key": "<secret key>",
  "addressing_style": "path",
  "signature_version": "s3v4",
  "app_bucket": "ekas-pcptransport",
  "processed_bucket": "photos-processed",
  "thumb_bucket": "photos-thumb",
  "upload_bucket": "photos-upload",
  "mc_config": {"url": "https://s3.pcpexpress.com", "accessKey": "...", "secretKey": "...", "api": "s3v4", "path": "auto"},
  "clear": ["public_endpoint"]
}
```

| Field | Keterangan |
|---|---|
| `addressing_style` | `auto` / `path` / `virtual` |
| `signature_version` | `s3v4` / `s3` |
| `endpoint`, `public_endpoint` | Wajib diawali `http://` atau `https://` |
| `mc_config` | JSON format `mc`/MinIO client. Dikonversi ke field di atas (`path` `on`→`path`, `off`/`dns`→`virtual`, lainnya→`auto`; `api` `s3v2`/`s3`→`s3`, lainnya→`s3v4`). Field yang ditulis eksplisit menang atas `mc_config` |
| `clear` | (PUT saja) daftar field yang dikembalikan ke nilai env |

Respons `PUT`: `{"version": <n>, ...}` + isi yang sama dengan `GET`. Error: `422 INVALID_SETTINGS` (nilai/field tidak
valid), `409 SECRET_KEY_MISSING` (mengirim `secret_key` tetapi `PQE_SECRET_KEY` belum diisi di server). Tercatat di
audit log `settings.storage.update` (nama field saja, tanpa nilai rahasia).

Respons `test` (tidak menyimpan apa pun; field kosong memakai nilai efektif saat ini):

```json
{
  "ok": false,
  "endpoint": "http://127.0.0.1:9000",
  "checks": [
    {"check": "Baca bucket aplikasi 'ekas-pcptransport'", "ok": false, "detail": "EndpointConnectionError: Could not connect to the endpoint URL: ..."},
    {"check": "Tulis bucket hasil kompres 'photos-processed'", "ok": false, "detail": "..."},
    {"check": "Akses bucket thumbnail 'photos-thumb'", "ok": false, "detail": "..."},
    {"check": "Akses bucket upload 'photos-upload'", "ok": false, "detail": "..."}
  ]
}
```

Arti error yang sering muncul: [troubleshooting.md](troubleshooting.md#pengaturan-silo-test-koneksi-gagal).

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
| `retake_required` | bool | `true` = kualitas tidak bisa dimaksimalkan (verdict `REJECT` setelah percobaan perbaikan otomatis, termasuk file rusak), atau ops memilih "minta foto ulang". `false` jika ops meng-*approve*. Tidak bergantung pada mode. Ada juga di webhook |
| `retake_message` | string / null | Pesan siap tampil untuk kurir (sesuai bahasa) jika `retake_required: true` |
| `photo_type`, `profile` | string | Jenis foto & profile yang dipakai |
| `awb`, `courier_id`, `customer_id`, `branch`, `external_id` | string / null | Hasil mapping metadata |
| `captured_at`, `received_at`, `analyzed_at` | ISO-8601 / null | |
| `scores` | objek | `sharpness`, `resolution`, `exposure`, `ocr`, `authenticity` (0-100, tinggi = baik) + `authenticity_risk` (0-100, tinggi = mencurigakan). Hanya check yang aktif |
| `reasons[]` | array | `{code, check, severity, score, detail, message, suggestion}`, urut `reject` → `warn` → `info` |
| `review` | objek | `{status, by, at, note}` |
| `evidence_eligible` | bool | Apakah foto tampil di evidence pelanggan. Bawaan (`review_required: false`): `PASS`/`WARN` = `true`. Lihat [Evidence](#evidence--verifikasi) |
| `object` | objek | `{bucket, key}` lokasi foto original |
| `sha256` | string | Hash file original (untuk verifikasi) |
| `error` | objek / null | `{code, message}` jika `status = error` |
| `urls` | objek | Presigned URL `original` (tidak pernah diubah), `processed` (kompres; versi yang sudah diperbaiki jika `enhancement.applied`), `thumbnail`, `expires_in` (detik). Tidak ada di webhook |
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
`TOO_DARK`, `TOO_BRIGHT`, `GLARE`, dan `LOW_CONTRAST` memicu percobaan perbaikan otomatis; jika berhasil, kode itu
tidak lagi muncul (atau turun severity-nya) dan kode aslinya tercatat di `enhancement.before.codes`.

## Kode error khusus admin

| HTTP | `code` | Endpoint | Penyebab |
|---|---|---|---|
| 422 | `INVALID_CONFIG` / `INVALID_YAML` | `/v1/admin/tenants...` | Config tenant tidak valid |
| 422 | `INVALID_SETTINGS` | `/v1/admin/settings/storage...` | Field tidak dikenal, `addressing_style`/`signature_version` salah, endpoint tanpa `http(s)://` |
| 409 | `SECRET_KEY_MISSING` | `PUT /v1/admin/settings/storage` | `PQE_SECRET_KEY` belum diisi di server, secret key SILO tidak bisa disimpan terenkripsi |

## Webhook

Format, header, tanda tangan, dan aturan retry: [integration-guide.md#8-menerima-webhook](integration-guide.md#8-menerima-webhook).
