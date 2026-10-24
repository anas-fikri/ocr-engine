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
| Versi engine | Setiap respons berisi header `X-PQE-Version` (contoh `0.1.0`), juga di `GET /healthz` dan `GET /v1/me`. Berguna untuk memastikan versi setelah upgrade/rollback |
| Penyimpanan | Engine **tidak pernah menulis ke SILO**. Foto disimpan aplikasi; engine hanya membaca bucket aplikasi ([ADR 0006](adr/0006-engine-hanya-memproses-gambar.md)) |

## Role & hak akses

| Endpoint | admin | ops | viewer | app | customer | publik |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `POST /v1/analyze` | ✓ | ✓ | | ✓ (tenant sendiri) | | |
| `POST /v1/analyze/preview` | ✓ | ✓ | ✓ | ✓ | | |
| `GET /v1/photos`, `GET /v1/photos/{id}`, `GET /v1/photos/{id}/file` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | | |
| `POST /v1/photos/{id}/stored` | ✓ | ✓ | | ✓ (tenant sendiri) | | |
| `POST /v1/photos/{id}/review`, `/reprocess` | ✓ | ✓ | | | | |
| `GET /v1/photos/{id}/reviews` | ✓ | ✓ | ✓ | | | |
| `GET /v1/evidence/...`, `POST /v1/evidence/batch` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | ✓ (customer_id sendiri) | |
| `GET /v1/stats` | ✓ | ✓ | ✓ | ✓ (tenant sendiri) | | |
| `GET /v1/admin/tenants`, `/tenants/{code}`, `/history`, `/profiles/builtin`, `/jobs`, `/webhooks`, `/sources` | ✓ | ✓ | ✓ | | | |
| `PUT/POST /v1/admin/tenants...`, `/api-keys`, `/jobs/.../retry`, `/webhooks/retry-dead`, `/audit` | ✓ | | | | | |
| `GET/PUT /v1/admin/settings/storage`, `POST /v1/admin/settings/storage/test` | ✓ | | | | | |
| `GET/PUT /v1/admin/settings/backup`, `POST /v1/admin/settings/backup/test`, `POST /v1/admin/backup/run`, `GET /v1/admin/preflight` | ✓ | | | | | |
| `GET /v1/verify/{sha256}`, `/v1/meta/reason-codes`, `/v1/meta/fields`, `/healthz`, `/readyz`, `/metrics`, `/ui/guide.html`, `/ui/coba.html` (halaman; API-nya tetap butuh key) | | | | | | ✓ |
| `GET /v1/me`, `GET /v1/meta/profiles` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `POST /v1/events/s3` | token `PQE_S3_EVENT_TOKEN` (dipakai SILO) | | | | | |

API key `admin`/`ops`/`viewer` tanpa tenant bisa melihat semua tenant. Untuk endpoint yang butuh satu tenant
(misal `POST /v1/analyze`), tambahkan `?tenant=<kode>`.

---

## Analisis

### `POST /v1/analyze`

Menganalisis foto. Engine **hanya memproses**: tidak menyimpan file ke SILO. Bentuk A idempotent per objek
(bucket + key): memanggil ulang untuk objek yang sama mengembalikan foto yang sama.

**Query:** `tenant`, `sync` (bool, default `false`, hanya Bentuk A), `timeout_ms` (100-30000, default `PQE_SYNC_TIMEOUT_MS` = 3000), `lang`.

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

Engine hanya membaca objek itu dari SILO. Default async (`202` + `poll_url`); `sync=true` menunggu hasil sampai `timeout_ms`.

**Bentuk B: upload file** (`multipart/form-data`, **disarankan**): field `file` (JPEG/PNG/HEIC/WebP, maks.
`PQE_MAX_UPLOAD_MB`) ditambah field metadata yang sama seperti di atas (semua opsional), plus:

| Field | Keterangan |
|---|---|
| `object_key` | Opsional. Key SILO tempat **aplikasi** akan menyimpan foto jika diterima. Dicatat sebagai `object` (hanya untuk foto yang diterima) supaya Web UI, evidence, dan backup NAS bisa membaca fotonya |
| `bucket` | Opsional, bucket untuk `object_key`. Default `AWS_BUCKET` |

File selalu dianalisis langsung di request itu (maks. `timeout_ms`; `sync` tidak berpengaruh). Engine tidak menyimpan
file-nya. Respons menambahkan `output_image`:

```json
"output_image": {"content_type": "image/jpeg", "bytes": 412345, "optimized": true,
                 "sha256": "9f2c...", "base64": "/9j/4AAQSkZJRg..."}
```

`output_image` = foto yang disimpan aplikasi ke SILO (hasil optimasi < 1 MB, atau foto asli apa adanya jika sudah
memenuhi syarat: `optimized: false`). `null` jika foto ditolak (`allow_status_update: false`). Respons maks. sekitar
1,4 MB (base64). Lewat `timeout_ms`: `202`, `status: pending`, `allow_status_update: true`, `output_image: null`
(aplikasi menyimpan foto asli; analisis tetap selesai di belakang).

**Respons:** `200` (hasil ada) atau `202` (masih diproses, ada `poll_url`). Isi: [objek foto](#objek-foto).

Cara tercepat untuk aplikasi pengiriman: Bentuk B dengan `timeout_ms=3000` dan `object_key`, lalu baca
`allow_status_update` (false = jangan simpan, jangan update status pengiriman, minta foto ulang), `courier_message`
(tampilkan ke kurir jika tidak null), dan `output_image` (simpan ke SILO di `object_key`). `202`/error/timeout =
fail-open: aplikasi menyimpan foto asli. Client: `checkFile` (utama) / `checkObject` (foto sudah di SILO) di
[clients/](../clients/README.md).

### `POST /v1/analyze/preview`

Menganalisis file **tanpa menyimpan apa pun** (tidak ada di DB maupun SILO). Untuk kalibrasi dan onboarding.

**Query:** `tenant` (opsional, pakai profile tenant), `photo_type` (nama profile), `lang`, `include_output`
(`true` = sertakan foto hasil engine, yang sudah dioptimasi/dikompres, sebagai data URL di field `output_image`;
`null` jika foto disimpan apa adanya). Dipakai halaman Uji Coba Kamera untuk membandingkan sebelum/sesudah.
**Body:** `multipart/form-data` field `file`.

**Respons:**

```json
{
  "verdict": "WARN", "action": "log", "profile": "default", "mode": "shadow",
  "allow_status_update": true, "courier_message": null,
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

`allow_status_update`, `courier_message`, `retake_required`, `retake_message`, dan `enhancement` sama artinya dengan
di [objek foto](#objek-foto) (di preview, `allow_status_update` = `action != "reject"`). Secara bawaan `enhancement`
selalu `{"attempted": false, "applied": false}` (engine tidak memanipulasi gambar). Jika profile mengaktifkan
`enhance.enabled` dan koreksi dipakai, `scores`/`verdict` adalah hasil **setelah** koreksi, `enhancement.before`
berisi hasil sebelumnya, dan `timings_ms.enhance` terisi.

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

Mengambil file foto **lewat engine**, untuk browser/HP yang tidak bisa menjangkau host SILO (presigned URL). Engine
membacanya dari lokasi `object` di SILO aplikasi (engine tidak menyimpan foto). `variant`: `processed` (bawaan) dan
`original` sama-sama file di lokasi `object` (foto lama dari versi sebelumnya yang punya salinan di bucket engine tetap
memakai salinan itu); `thumbnail` dibuat saat diminta (JPEG). `404 FILE_NOT_FOUND` jika foto tidak disimpan di SILO
(ditolak, atau aplikasi belum melaporkan lokasinya) atau file sudah tidak ada. Aplikasi produksi sebaiknya tetap
memakai `urls.*` (presigned) agar tidak membebani engine.

### `POST /v1/photos/{id}/stored`

Opsional: melaporkan key SILO tempat aplikasi menyimpan foto, jika `object_key` belum dikirim saat `POST /v1/analyze`.
Dipakai Web UI, evidence, dan backup NAS untuk **membaca** foto. Engine tidak memeriksa atau mengubah file di SILO.

Body: `{"object_key": "pod/2026/10/22/AWB123.jpg", "bucket": "ekas-pcptransport"}` (`bucket` opsional, default
`AWS_BUCKET`). Respons: [objek foto](#objek-foto) dengan `object` terisi. Foto lain yang tercatat di lokasi yang sama
dilepas dari lokasi itu (catatan ganda dari polling dengan hash sama dihapus).

### `GET /v1/photos/{id}`

Detail lengkap: [objek foto](#objek-foto) + field detail (`enhancement`, `compression`, `exif`, `metrics`, `image`,
`location`, `input_fields`, `config_version`, `duration_ms`, `phash`, `ingest_source`, `capture_source`).

`enhancement` (koreksi pencahayaan **opsional**, nonaktif secara bawaan sehingga nilainya
`{"attempted": false, "applied": false}`; lihat [integration-guide.md §7.1](integration-guide.md#71-optimasi-tanpa-manipulasi)):

| Field | Keterangan |
|---|---|
| `attempted` | bool. Koreksi dicoba (profile `enhance.enabled: true` dan ada alasan `TOO_DARK`/`TOO_BRIGHT`/`LOW_CONTRAST`/`GLARE`) |
| `applied` | bool. Hasil perbaikan dipakai untuk verdict dan versi processed |
| `operations` | contoh `["levels:12-140", "gamma:0.62"]` |
| `verified` | `global_monotonic_curve` jika lolos verifikasi eksak |
| `before` | `{verdict, scores, codes}` sebelum perbaikan |
| `reason` | Jika tidak dipakai: `nothing_to_fix` / `verification_failed` / `no_improvement` |

### `POST /v1/photos/{id}/review`

Keputusan ops. Body: `{"action": "approve" | "request_retake" | "mark_fraud" | "reset", "note": "opsional"}`.

| `action` | `review.status` | Efek ke evidence pelanggan |
|---|---|---|
| `approve` | `approved` | Foto WARN/REJECT ikut tampil di evidence, `retake_required` menjadi `false`, `allow_status_update` menjadi `true` |
| `request_retake` | `retake_requested` | Tidak tampil, `retake_required` menjadi `true`, `allow_status_update` menjadi `false` |
| `mark_fraud` | `fraud` | Tidak tampil, `allow_status_update` menjadi `false` |
| `reset` | `none` | Kembali ke aturan verdict (hanya PASS yang tampil) |

Memicu webhook `review.updated` dan tercatat di audit log.

### `POST /v1/photos/{id}/reprocess`

Antrekan analisis ulang (misal setelah threshold diubah); engine membaca ulang file dari lokasi `object`. Respons
`{"id": "...", "status": "pending"}`. `409 NOT_STORED` untuk foto yang tidak disimpan di SILO (`object: null`).

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

Key `viewer` (misal finance) dan staf yang terikat tenant hanya melihat evidence tenant-nya; key tanpa tenant melihat semua.
Resi tanpa foto layak tidak muncul di PDF; jika tidak ada satu pun, PDF berisi satu halaman keterangan.

`image_url` adalah presigned URL dengan masa berlaku `ENGINE_PRESIGN_TTL_CUSTOMER` (default 7 hari).
Foto yang muncul = foto dengan `evidence_eligible: true`: dengan `review_required: false` (bawaan tenant) `PASS` dan
`WARN`; dengan `review_required: true` hanya `PASS` atau yang di-approve ops. `REJECT` hanya muncul jika di-approve ops.
Hanya foto yang **disimpan aplikasi** dan lokasinya diketahui engine (`object` terisi, lewat `object_key` atau
`POST /v1/photos/{id}/stored`) yang muncul; gambarnya dibaca dari SILO aplikasi. `sha256`/`verify_url` merujuk ke
file yang dianalisis engine.

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

`{"name": "...", "role": "app", "tenant": "courier-app", "customer_id": null, "version": "0.1.0"}`

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
| `GET /v1/admin/settings/backup` | - | Konfigurasi & status backup NAS. Lihat [Backup NAS](#backup-nas) |
| `PUT /v1/admin/settings/backup` | JSON | Simpan konfigurasi backup NAS (berlaku di putaran worker berikutnya) |
| `POST /v1/admin/settings/backup/test` | JSON | Uji tujuan NAS dengan nilai yang dikirim, tanpa menyimpan |
| `POST /v1/admin/backup/run` | JSON `{"kind": "photos"\|"database"\|"all"}` | Minta worker menjalankan backup sekarang (±10 detik) |
| `GET /v1/admin/preflight?check_silo=true` | - | Cek kesiapan production, sama dengan `pqe preflight`: `{"ready", "counts": {"ok", "warn", "fail"}, "checks": [{"group", "check", "status", "detail", "fix"}]}`. `ready` = tidak ada `fail`. Dengan `check_silo=true` (bawaan) engine mencoba membaca bucket aplikasi (hanya baca; cek SILO: "Baca bucket aplikasi '<bucket>'" dan "Engine hanya membaca SILO") |

Contoh mengubah config:

```bash
curl -X PUT "$PQE_URL/v1/admin/tenants" -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/x-yaml" --data-binary @config/tenants/courier-app.yaml
```

### Pengaturan SILO

Penjelasan lengkap (prioritas, enkripsi, jebakan `127.0.0.1`): [configuration.md §1.6](configuration.md#16-koneksi-silo-lewat-web-ui).
Di Web UI halaman ini bernama **Koneksi SILO**. Engine hanya **membaca** SILO (foto yang disimpan aplikasi), jadi
service account cukup izin `s3:GetObject` + `s3:ListBucket` di bucket aplikasi. Field `processed_bucket`,
`thumb_bucket`, `upload_bucket` masih diterima untuk data versi lama, tetapi tidak diperlukan untuk instalasi baru.

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

Respons `test` (tidak menyimpan dan tidak menulis apa pun ke SILO; field kosong memakai nilai efektif saat ini):

```json
{
  "ok": false,
  "endpoint": "http://127.0.0.1:9000",
  "checks": [
    {"check": "Baca bucket aplikasi 'ekas-pcptransport'", "ok": false, "detail": "EndpointConnectionError: Could not connect to the endpoint URL: ..."},
    {"check": "Baca isi foto", "ok": false, "detail": "..."}
  ]
}
```

Arti error yang sering muncul: [troubleshooting.md](troubleshooting.md#koneksi-silo-test-koneksi-gagal).

### Backup NAS

Cara kerja, mount NAS, dan restore: [operations.md bagian 6](operations.md#62-backup-nas-otomatis-web-ui--backup-nas).
Field: [configuration.md §1.7](configuration.md#17-backup-nas-lewat-web-ui).

`PUT /v1/admin/settings/backup` (semua field opsional; yang tidak dikirim tidak berubah; `s3_secret_key` kosong = tidak diubah):

```json
{
  "enabled": true,
  "target": "folder",
  "folder_path": "/backup",
  "include_photos": true,
  "include_database": true,
  "include_upload_originals": true,
  "include_processed": true,
  "include_thumbs": false,
  "photo_interval_minutes": 15,
  "db_interval_hours": 24,
  "db_keep": 14
}
```

| Field | Keterangan |
|---|---|
| `include_photos` | Foto yang disimpan aplikasi di SILO (lokasi `object`), dibaca dari bucket aplikasi lalu disalin ke NAS |
| `include_database` | Dump database engine |
| `include_upload_originals`, `include_processed`, `include_thumbs` | Data versi lama saja: salinan di bucket engine sebelum engine berhenti menulis ke SILO |

`include_app_originals` sudah dihapus (diganti `include_photos`); jika masih dikirim, field itu diabaikan.

Tujuan NAS S3: `"target": "s3"` + `s3_endpoint`, `s3_bucket`, `s3_prefix`, `s3_region`, `s3_access_key`, `s3_secret_key`,
`s3_addressing_style` (`path`/`auto`/`virtual`).

Respons `GET`/`PUT`/`run` (rahasia tidak pernah dikembalikan):

```json
{
  "config": {"enabled": true, "target": "folder", "folder_path": "/backup", "s3_secret_key": {"set": false}, "...": "..."},
  "state": {
    "last_photo_run": "2026-10-23T23:38:01+00:00", "last_photo_result": {"photos": 71, "files": 71},
    "last_db_run": "2026-10-23T23:38:01+00:00",
    "last_db_result": {"file": "pqe-20261023-233801.dump", "bytes": 48840, "rotated": 0},
    "files_total": 71, "last_error": null, "last_run_at": "2026-10-23T23:38:01+00:00", "requested": null
  },
  "pending": 0,
  "backed_up": 71,
  "free_space": {"total": 270582939648, "free": 21152727040},
  "secret_key_configured": true
}
```

`pending` = foto selesai dianalisis yang disimpan aplikasi (`object` terisi) dan belum tersalin; `backed_up` = foto yang sudah tersalin; `free_space` hanya untuk
tujuan folder. `state.requested` terisi saat permintaan `run` menunggu worker.

Respons `test`:

```json
{
  "ok": true,
  "target": "folder /backup",
  "checks": [
    {"check": "Tujuan backup (folder /backup)", "ok": true, "detail": "folder ada"},
    {"check": "Tulis ke NAS", "ok": true, "detail": "tulis, baca, hapus berhasil"},
    {"check": "Ruang kosong NAS", "ok": true, "detail": "19.7 GB kosong dari 252.0 GB"},
    {"check": "Baca bucket foto aplikasi 'ekas-pcptransport'", "ok": true, "detail": "bisa diakses"},
    {"check": "Dump database", "ok": true, "detail": "dump postgresql siap"}
  ]
}
```

Error: `422 INVALID_SETTINGS` (angka di luar rentang, `folder_path` bukan path absolut, endpoint tanpa `http(s)://`, atau
tujuan `s3` aktif tanpa endpoint/bucket), `422 VALIDATION_ERROR` (nilai `target`/`s3_addressing_style`/`kind` di luar
pilihan), `409 SECRET_KEY_MISSING` (mengirim `s3_secret_key` tetapi `PQE_SECRET_KEY` belum diisi). Audit log:
`settings.backup.update`, `backup.run_requested`.

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
| `action` | `log` / `notify` / `reject` / null | Saran perilaku aplikasi sesuai mode profile (`shadow` → `log`, `warn` → `notify`, `enforce` → `reject` untuk `REJECT`; `PASS` → `log`) |
| `allow_status_update` | bool | Boleh aplikasi pengiriman mengupdate status pengiriman (status milik aplikasi pengiriman, bukan engine). `false` hanya jika `action = reject` (verdict `REJECT` di mode `enforce`) atau ops memilih "minta foto ulang"/"tandai kecurangan". `true` jika ops meng-*approve*, selama `pending`, dan saat gagal karena sistem (fail-open). File rusak (`UNREADABLE_FILE`) diperlakukan seperti `REJECT` (ditahan di mode `enforce`). Ada juga di webhook |
| `courier_message` | string / null | Satu kalimat siap tampil untuk kurir (sesuai bahasa): `<message>. <suggestion>` dari alasan pertama non-info, diawali `Wajib foto ulang.` / `Retake required.` jika `retake_required`. `null` jika `action` bukan `notify`/`reject` (atau ops meng-*approve*) |
| `retake_required` | bool | `true` = kualitas tidak memenuhi syarat (verdict `REJECT`, termasuk file rusak), atau ops memilih "minta foto ulang". `false` jika ops meng-*approve*. Tidak bergantung pada mode. Ada juga di webhook |
| `retake_message` | string / null | Pesan foto ulang (sesuai bahasa) jika `retake_required: true`: "Kualitas foto tidak memenuhi syarat. Silakan ambil foto ulang." |
| `photo_type`, `profile` | string | Jenis foto & profile yang dipakai |
| `awb`, `courier_id`, `customer_id`, `branch`, `external_id` | string / null | Hasil mapping metadata |
| `captured_at`, `received_at`, `analyzed_at` | ISO-8601 / null | |
| `scores` | objek | `sharpness`, `resolution`, `exposure`, `ocr`, `authenticity` (0-100, tinggi = baik) + `authenticity_risk` (0-100, tinggi = mencurigakan). Hanya check yang aktif |
| `reasons[]` | array | `{code, check, severity, score, detail, message, suggestion}`, urut `reject` → `warn` → `info` |
| `review` | objek | `{status, by, at, note}` |
| `evidence_eligible` | bool | Apakah foto tampil di evidence pelanggan. Bawaan (`review_required: false`): `PASS`/`WARN` = `true`. Lihat [Evidence](#evidence--verifikasi) |
| `object` | objek / null | `{bucket, key}` lokasi foto di SILO aplikasi: objek yang dianalisis (Bentuk A, polling), atau `object_key` yang dikirim/dilaporkan aplikasi (Bentuk B, hanya untuk foto yang diterima). `null` = tidak disimpan (ditolak) atau lokasi belum dilaporkan; foto seperti ini tidak punya file di Web UI, tidak masuk evidence/backup NAS, dan tidak bisa dianalisis ulang. Ada juga di webhook |
| `output_image` | objek / null | **Hanya di respons upload file** `POST /v1/analyze` (tidak disimpan, tidak ada di `GET` maupun webhook): `{content_type, bytes, optimized, sha256, base64}` foto yang disimpan aplikasi ke SILO. `null` = ditolak atau masih `pending` |
| `sha256` | string | Hash file yang dianalisis engine (untuk verifikasi) |
| `error` | objek / null | `{code, message}` jika `status = error` |
| `urls` | objek | Presigned URL ke lokasi `object`: `original` dan `processed` (sama; `null` jika `object` null), `thumbnail` (`null` untuk foto baru; pakai `GET /v1/photos/{id}/file?variant=thumbnail`), `expires_in` (detik). Foto lama yang punya salinan di bucket engine memakai salinan itu untuk `processed`/`thumbnail`. Tidak ada di webhook |
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
Secara bawaan engine tidak memperbaiki foto: `TOO_DARK`, `TOO_BRIGHT`, `GLARE`, dan `LOW_CONTRAST` langsung dinilai
apa adanya (bisa `REJECT` → foto ulang). Hanya jika profile mengaktifkan `enhance.enabled`, kode-kode itu memicu
percobaan koreksi pencahayaan; jika berhasil, kode itu tidak lagi muncul (atau turun severity-nya) dan kode aslinya
tercatat di `enhancement.before.codes`.

## Kode error tambahan (admin & file foto)

| HTTP | `code` | Endpoint | Penyebab |
|---|---|---|---|
| 422 | `INVALID_CONFIG` / `INVALID_YAML` | `/v1/admin/tenants...` | Config tenant tidak valid |
| 422 | `INVALID_SETTINGS` | `/v1/admin/settings/storage...` | Field tidak dikenal, `addressing_style`/`signature_version` salah, endpoint tanpa `http(s)://` |
| 422 | `INVALID_SETTINGS` | `/v1/admin/settings/backup...` | Angka di luar rentang, `folder_path` bukan path absolut, endpoint NAS tanpa `http(s)://`, tujuan `s3` tanpa endpoint/bucket |
| 409 | `SECRET_KEY_MISSING` | `PUT /v1/admin/settings/storage`, `PUT /v1/admin/settings/backup` | `PQE_SECRET_KEY` belum diisi di server, secret key SILO/NAS tidak bisa disimpan terenkripsi |
| 404 | `FILE_NOT_FOUND` | `GET /v1/photos/{id}/file` | Foto tidak disimpan di SILO (ditolak / lokasi belum dilaporkan) atau file sudah tidak ada di SILO |
| 409 | `NOT_STORED` | `POST /v1/photos/{id}/reprocess` | Foto tidak disimpan di SILO (`object: null`), tidak ada file untuk dianalisis ulang |

## Webhook

Format, header, tanda tangan, dan aturan retry: [integration-guide.md#8-menerima-webhook](integration-guide.md#8-menerima-webhook).
Payload berisi objek foto yang sama dengan respons API (tanpa `urls`), termasuk `allow_status_update`,
`courier_message`, `object`, `retake_required`, dan `retake_message` (tanpa `output_image`):

```json
{"event": "analysis.completed", "photo": {"id": "e350...", "status": "done", "verdict": "REJECT", "action": "reject",
 "allow_status_update": false, "courier_message": "Wajib foto ulang. Foto buram. Pegang HP dengan stabil, ...", "object": null,
 "retake_required": true, "retake_message": "Kualitas foto tidak memenuhi syarat. Silakan ambil foto ulang.", "...": "..."}}
```
