# Panduan Integrasi Photo Quality Engine

Dokumen ini untuk **programmer aplikasi** (Android, iOS, Flutter, web, backend Laravel/Node) yang ingin
menghubungkan aplikasinya ke Photo Quality Engine. Ikuti dari atas ke bawah. Semua contoh kode bisa langsung disalin.

> Butuh gambaran sistemnya dulu? Baca [README](../README.md). Ingin mencoba engine di PC? Baca [getting-started.md](getting-started.md).
>
> **Versi ringkas di browser:** buka `https://<server-engine>/ui/guide.html` (tanpa login). Isinya sama dengan
> dokumen ini dalam bentuk interaktif: contoh kode curl/PHP/TypeScript/Python/Kotlin dengan URL server yang sudah
> terisi otomatis dan tombol salin, plus **diagram alur** (integrasi sampai go-live, alur penggunaan saat runtime,
> keputusan di aplikasi pengiriman, proses di dalam engine). Link-nya juga ada di menu Web UI (**Panduan Integrasi ↗**) dan di halaman login.
>
> **Coba dulu sebelum menulis kode:** buka `https://<server-engine>/ui/coba.html` dari HP. Halaman itu berperan sebagai
> aplikasi kurir (ambil foto → kirim → tampil pesan untuk kurir + detail analisis), lihat
> [getting-started.md §4b](getting-started.md#4b-uji-coba-dari-hp-tanpa-membangun-aplikasi).

## Daftar isi

0. [Cara tercepat: scan dulu, baru simpan](#0-cara-tercepat-scan-dulu-baru-simpan)
1. [Aturan emas (wajib dibaca)](#1-aturan-emas-wajib-dibaca)
2. [Pilih cara integrasi](#2-pilih-cara-integrasi)
3. [Persiapan: tenant & API key](#3-persiapan-tenant--api-key)
4. [Level 0: tanpa mengubah aplikasi](#4-level-0-tanpa-mengubah-aplikasi)
5. [Level 1: tambahkan metadata saat upload ke SILO](#5-level-1-tambahkan-metadata-saat-upload-ke-silo)
6. [Level 2: panggil API engine](#6-level-2-panggil-api-engine)
7. [Menampilkan hasil ke kurir](#7-menampilkan-hasil-ke-kurir) (termasuk `allow_status_update`, `retake_required`, dan optimasi tanpa manipulasi)
8. [Menerima webhook](#8-menerima-webhook)
9. [Evidence untuk pelanggan (penagihan)](#9-evidence-untuk-pelanggan-penagihan)
10. [Kode error](#10-kode-error)
11. [Checklist sebelum go-live](#11-checklist-sebelum-go-live)
12. [FAQ integrasi](#12-faq-integrasi)

---

## 0. Cara tercepat: scan dulu, baru simpan

Aplikasi pengiriman mengirim foto dari kurir **langsung ke engine** (bukan ke SILO dulu):
`POST /v1/analyze?sync=true&timeout_ms=3000` dengan file foto (multipart), atau `checkFile(...)` di client. Engine
memeriksa foto lebih dulu, lalu **hanya foto yang diterima** dioptimasi, dikompres, dan disimpan ke SILO. Foto yang
ditolak tidak disimpan sebagai foto pengiriman; pesannya langsung dikirim balik untuk kurir. Satu request, tunggu
maks. 3 detik, tanpa polling dan tanpa webhook.

| Hasil | Yang dilakukan aplikasi pengiriman |
|---|---|
| `allow_status_update: true` | Foto diterima dan **sudah disimpan engine di SILO**: simpan `stored.url` di database Anda, update status pengiriman, tampilkan `courier_message` jika tidak `null` |
| `allow_status_update: false` | Foto ditolak, **tidak disimpan** sebagai foto pengiriman (`stored: null`). Kirim balik `courier_message` ke kurir, **jangan update status**, kurir foto ulang |
| Engine error / timeout / `202` | Simpan foto ke SILO sendiri seperti sekarang (kode lama tetap dipakai sebagai cadangan), lalu update status (fail-open). Engine tetap memeriksa foto itu belakangan (bucket notification/polling) untuk laporan & evidence |

Status pengiriman **milik aplikasi pengiriman**. Engine tidak pernah mengubahnya; engine hanya memberi keputusan
dan link foto yang sudah disimpan.

**Kapan foto dikirim ke engine, dan kapan divalidasi?**

| Urutan | Siapa | Apa yang terjadi |
|---|---|---|
| 1 | Kurir | Ambil foto POD, tekan **Kirim** |
| 2 · kirim | Aplikasi pengiriman → engine | **Foto langsung dikirim ke engine** (belum ke SILO, status belum diupdate): `POST /v1/analyze?sync=true` dengan file foto. Satu request, tunggu maks. 3 detik |
| 3 · scan | Engine | **Validasi gambar** (biasanya 0,2–2 detik): ketajaman, pecah, resolusi, cahaya, EXIF, duplikat, OCR → verdict |
| 4a · diterima | Engine → SILO | Optimasi + kompres < 1 MB (tanpa manipulasi), lalu **disimpan ke SILO** (versi hasil di `photos-processed`, asli diarsipkan untuk verifikasi keaslian). Engine menjawab `allow_status_update: true` + `stored.url` |
| 4b · ditolak | Engine → aplikasi → kurir | Tidak disimpan sebagai foto pengiriman, tanpa link. Engine menjawab `allow_status_update: false` + `courier_message`; aplikasi mengirim balik pesan itu ke kurir untuk foto ulang (kembali ke 1) |
| 5 | Aplikasi pengiriman | Diterima → simpan `stored.url` + update status. Ditolak → status **tidak** diupdate. Engine tidak menjawab dalam 3 detik → simpan foto ke SILO sendiri (kode lama) + update status (fail-open) |

```mermaid
sequenceDiagram
    autonumber
    actor K as Kurir
    participant BE as Aplikasi Pengiriman
    participant E as Engine
    participant S as SILO
    K->>BE: Foto + resi (tekan Kirim)
    BE->>E: ① Kirim file ke engine: POST /v1/analyze?sync=true
    note over E: ② Scan / validasi gambar ≤ 3 dtk
    alt diterima
        E->>S: Simpan versi optimasi (+ arsip asli)
        E-->>BE: allow_status_update true + stored.url
        BE->>BE: Simpan stored.url + update status pengiriman
    else ditolak (REJECT, mode enforce)
        E-->>BE: allow_status_update false + courier_message (tidak disimpan)
        BE-->>K: Kirim balik pesan, wajib foto ulang (status tidak diupdate)
    else engine tidak menjawab ≤ 3 dtk
        BE->>S: Simpan foto sendiri (kode lama)
        BE->>BE: Update status pengiriman (fail-open)
    end
```

Foto asli dari kurir disimpan di bucket internal engine (`photos-upload`) sebagai arsip untuk verifikasi keaslian (hash,
EXIF), agar foto tidak hilang jika engine mati di tengah proses, dan agar ops bisa meninjau foto yang ditolak. Bucket itu
tidak pernah diberikan sebagai link foto pengiriman: `stored` selalu menunjuk ke versi hasil di `photos-processed`
(foto kecil yang tidak perlu dikompres pun disalin ke sana).

**PHP / Laravel** (salin [`clients/php/PhotoQualityClient.php`](../clients/php/PhotoQualityClient.php); `checkFile` tidak pernah throw):

```php
$pqe = new PhotoQualityClient(env('PQE_URL'), env('PQE_API_KEY'), 5000, 'id');
$path = $request->file('photo')->getRealPath();
$d = $pqe->checkFile($path, ['awb' => $awb, 'photo_type' => 'pod_recipient', 'courier_id' => $courierId, 'source' => 'camera']);

if (!$d['allow_status_update']) {                       // DITOLAK: tidak disimpan, kirim balik ke kurir
    return response()->json(['foto_ulang' => true, 'pesan' => $d['message']], 422);
}
$link = $d['stored']['url'] ?? Storage::disk('s3')->putFile('pod', $request->file('photo'));  // null = engine tidak menjawab
$shipment->update(['photo_url' => $link, 'status' => 'DELIVERED']);
return response()->json(['ok' => true, 'pesan' => $d['message']]);   // pesan bisa null
```

**TypeScript / Node** (salin [`clients/typescript/pqe-client.ts`](../clients/typescript/pqe-client.ts); `checkFile` tidak pernah throw):

```ts
import { PhotoQualityClient } from "./pqe-client.ts";
const pqe = new PhotoQualityClient({ baseUrl: process.env.PQE_URL!, apiKey: process.env.PQE_API_KEY!, lang: "id" });

const d = await pqe.checkFile(photoBlob, "foto.jpg", { awb, photo_type: "pod_recipient", courier_id: courierId, source: "camera" });
if (!d.allowStatusUpdate) return res.status(422).json({ fotoUlang: true, pesan: d.message }); // ditolak, tidak disimpan
const link = d.stored?.url ?? (await uploadKeSiloSendiri(photoBlob));  // null = engine tidak menjawab (fail-open)
await shipment.update({ photoUrl: link, status: "DELIVERED" });
res.json({ ok: true, pesan: d.message });
```

**curl:**

```bash
curl -s -m 8 -X POST "$PQE_URL/v1/analyze?sync=true&timeout_ms=3000" \
  -H "X-API-Key: $PQE_API_KEY" -H "Accept-Language: id" \
  -F "file=@foto.jpg" -F "awb=AWB123" -F "photo_type=pod_recipient" -F "source=camera"
# diterima -> {"allow_status_update": true,  "stored": {"bucket": "photos-processed", "key": "...", "url": "https://s3.pcpexpress.com/photos-processed/...jpg"}, ...}
# ditolak  -> {"allow_status_update": false, "stored": null, "courier_message": "Wajib foto ulang. Foto buram. ...", ...}
```

Keputusan dari `checkFile` / `checkObject`:

| TypeScript | PHP | Arti |
|---|---|---|
| `allowStatusUpdate` | `allow_status_update` | `false` = foto ditolak: **jangan** update status, minta foto ulang |
| `message` | `message` | Tampilkan ke kurir jika tidak `null` |
| `stored` | `stored` | `{bucket, key, url}` lokasi permanen foto yang diterima di SILO: simpan `url` di database Anda. `null` = ditolak, atau engine tidak menjawab (simpan sendiri) |
| `retakeRequired` | `retake_required` | Kualitas tidak memenuhi syarat (verdict `REJECT`) |
| `decided` | `decided` | `false` = belum ada keputusan (engine mati/lambat/error, atau lewat 3 detik) → fail-open: simpan foto sendiri, status boleh diupdate |
| `photoId`, `verdict` | `photo_id`, `verdict` | Untuk disimpan/dilacak |

Foto sudah terlanjur disimpan ke SILO oleh aplikasi? Pakai `checkObject(objectKey, ...)` (kirim key objek, bukan file).
Keputusannya sama, tetapi foto yang ditolak sudah terlanjur ada di bucket aplikasi. Kurir hanya ditahan jika foto
`REJECT` **dan** mode tenant `enforce` (atau ops menandai "minta foto ulang"); di mode `shadow`/`warn` foto tidak pernah
menahan status. Menahan status hanya bisa di level 2 (sinkron); level 0/1 menganalisis setelahnya. Contoh Python ada
di panduan browser `/ui/guide.html#cepat`.

---

## 1. Aturan emas (wajib dibaca)

1. **Status pengiriman milik aplikasi pengiriman, dan engine tidak boleh menahan kurir karena dirinya sendiri.**
   Engine tidak pernah mengubah status; engine hanya menjawab `allow_status_update`. Satu-satunya yang menahan update
   status adalah `allow_status_update: false` (foto ditolak di mode `enforce`): status baru diupdate setelah foto ulang diterima.
2. **Selalu pakai timeout dan fail-open.** Kalau engine lambat, error, atau mati, anggap `allow_status_update: true`
   lalu lanjutkan proses. Batas waktu yang disarankan: **3 detik** untuk mode sinkron, 5 detik untuk request lain.
   Client resmi di [`clients/`](../clients) sudah fail-open secara bawaan.
3. **API key adalah rahasia server.** Jangan menaruh API key di aplikasi Android/iOS/Flutter atau JavaScript
   di browser (bisa dibongkar). Aplikasi mobile memanggil **backend Anda sendiri**, lalu backend yang memanggil engine.
4. **Pakai kode alasan (`code`) untuk logika, pakai `message`/`suggestion` untuk teks.** Kode stabil.
   Teks bisa berubah dan sudah diterjemahkan (ID/EN).
5. **Kirim foto asli dari kamera.** Jangan kompres atau resize di aplikasi. Engine yang mengurus kompresi
   tanpa merusak keterbacaan. Kompresi agresif di aplikasi adalah penyebab utama foto "pecah".
6. **Runtime harus versi yang sudah di-patch.** Node.js ≥ 22.23.4 / 24.22.1 / 26.12.1 dan PHP ≥ 8.2.35 / 8.3.36 /
   8.4.27 / 8.5.12 (security release 21 Oktober 2026). Detail di [clients/README.md](../clients/README.md).

---

## 2. Pilih cara integrasi

```mermaid
flowchart TD
    A[Aplikasi Anda] --> C{Bisa mengubah kode<br/>upload foto?}
    C -- Tidak --> L0[Level 0<br/>cukup daftarkan tenant]
    C -- Ya --> D{Perlu keputusan langsung<br/>untuk kurir / menahan status?}
    D -- Tidak --> L1[Level 1<br/>tambah metadata saat upload ke SILO]
    D -- Ya --> E{Foto boleh dikirim ke engine<br/>sebelum disimpan?}
    E -- Ya, disarankan --> L2B[Level 2B: scan dulu<br/>kirim file ke POST /v1/analyze<br/>engine menyimpan foto yang diterima]
    E -- Tidak, sudah terlanjur di SILO --> L2A[Level 2A<br/>simpan ke SILO lalu panggil<br/>POST /v1/analyze dengan object_key]
```

| Level | Yang dikerjakan aplikasi | Hasil | Kapan dipilih |
|---|---|---|---|
| **0** | Tidak ada. Upload ke SILO seperti biasa | Laporan & review ops, evidence pelanggan | Aplikasi lama yang belum bisa diubah |
| **1** | Tambah metadata (`awb`, `photo-type`, ...) saat upload | Sama dengan level 0, tapi data resi/kurir akurat | Aplikasi yang sedang dikembangkan (disarankan minimal) |
| **2B** (disarankan) | Kirim file foto ke engine, **scan dulu, baru simpan** ([cara tercepat](#0-cara-tercepat-scan-dulu-baru-simpan)) | Keputusan per foto langsung (`courier_message`, `allow_status_update`); hanya foto yang diterima disimpan engine ke SILO (`stored.url`) | Semua aplikasi yang bisa diubah: paling sedikit kode, foto ditolak tidak pernah masuk penyimpanan foto pengiriman |
| **2A** | Level 1 + simpan ke SILO, lalu panggil API dengan `object_key` | Keputusan sama dengan 2B, tetapi foto yang ditolak sudah terlanjur ada di bucket aplikasi | Aplikasi yang alur uploadnya ke SILO belum bisa diubah urutannya |

Semua level bisa naik bertahap tanpa mengubah engine. Menahan update status saat foto ditolak hanya bisa di level 2,
karena aplikasi butuh jawaban engine **sebelum** update status. Level 0/1 menganalisis setelahnya (laporan, evidence, kalibrasi).

---

## 3. Persiapan: tenant & API key

Minta ke admin engine (atau lakukan sendiri jika Anda admin, lewat Web UI menu **Tenant** dan **API key**):

| Yang diminta | Contoh | Keterangan |
|---|---|---|
| Kode tenant | `courier-app` | Satu tenant per aplikasi |
| Base URL engine | `https://photo-quality.pcpexpress.com` (produksi), `http://localhost:8000` (lokal) | Sesuai lingkungan |
| API key role `app` | `pqe_...` | Terikat ke tenant Anda. Simpan di env backend (`PQE_API_KEY`) |
| Secret webhook (opsional) | string acak | Jika ingin menerima webhook |

Informasi yang perlu Anda berikan ke admin agar tenant dikonfigurasi dengan benar:

- Nama bucket dan prefix (folder) tempat aplikasi menyimpan foto, misal `ekas-pcptransport`, prefix `pod/`.
- Contoh 3-5 object key nyata, misal `pod/2026/10/22/AWB123_K045_1729580000.jpg`.
- Di mana nomor resi, jenis foto, dan kurir bisa ditemukan (metadata objek, nama file, atau tabel DB).
- Jenis foto yang dihasilkan aplikasi: `pod_recipient` (foto penerima), `pod_document` (resi/dokumen),
  `package`, `goods`, `location`, atau jenis lain.

Cek API key Anda:

```bash
curl -s -H "X-API-Key: $PQE_API_KEY" https://photo-quality.pcpexpress.com/v1/me
# {"name":"aplikasi-kurir-prod","role":"app","tenant":"courier-app","customer_id":null}
```

Dokumentasi API interaktif (Swagger, bisa langsung dicoba) tersedia di `<base-url>/docs`.

---

## 4. Level 0: tanpa mengubah aplikasi

Tidak ada kode yang perlu ditulis. Admin menambahkan *source* di config tenant agar engine memantau bucket Anda:

```yaml
sources:
  - type: s3_poll
    bucket: ekas-pcptransport
    prefix: "pod/"
    date_prefix_format: "%Y/%m/%d/"   # jika key mengandung tanggal: jauh lebih ringan
    poll_interval_sec: 15
mapping:
  awb: ["key:regex:(?P<awb>[A-Z0-9]{8,20})_"]   # ambil resi dari nama file
  photo_type: ["key:segment:1", "default:pod_recipient"]
```

Engine akan menemukan foto baru dalam beberapa detik (atau langsung, jika bucket notification SILO aktif),
menganalisis, mengompres, lalu menampilkannya di Web UI. Referensi lengkap aturan mapping:
[configuration.md](configuration.md).

Jika data resi tidak ada di nama file tetapi ada di database aplikasi, admin bisa memakai source `db_poll`
(membaca tabel link foto secara read-only). Lihat [configuration.md](configuration.md).

---

## 5. Level 1: tambahkan metadata saat upload ke SILO

Tambahkan **metadata objek** (`x-amz-meta-*`) saat upload. Engine membacanya otomatis.
Tidak menambah request baru dan tidak memperlambat upload.

| Metadata | Contoh | Wajib? | Keterangan |
|---|---|---|---|
| `awb` | `AWB123` | Disarankan | Nomor resi |
| `photo-type` | `pod_recipient` | Disarankan | Menentukan profile pemeriksaan |
| `courier-id` | `K045` | Disarankan | Untuk laporan per kurir |
| `customer-id` | `CUST-001` | Disarankan | Untuk evidence per pelanggan |
| `branch` | `JKT01` | Opsional | Untuk laporan per cabang |
| `captured-at` | `2026-10-22T08:13:20+07:00` | Opsional | Dicocokkan dengan waktu EXIF |
| `lat` / `lng` | `-6.2001` / `106.8166` | Opsional | Dicocokkan dengan GPS EXIF |
| `source` | `camera` / `gallery` | Opsional | Asal foto di HP |
| `external-id` | `foto-998877` | Opsional | ID foto di aplikasi Anda |

**Konvensi key yang disarankan** (tidak wajib, tapi memudahkan):
`{app}/{photo_type}/{yyyy}/{mm}/{dd}/{awb}_{courier_id}_{timestamp}.jpg`

### Laravel (backend upload langsung ke SILO)

```php
use Illuminate\Support\Facades\Storage;

$path = sprintf('pod/%s/%s_%s_%d.jpg', now()->format('Y/m/d'), $awb, $courierId, now()->timestamp);

Storage::disk('s3')->put($path, file_get_contents($request->file('photo')->getRealPath()), [
    'visibility'  => 'private',
    'ContentType' => 'image/jpeg',
    'Metadata'    => [
        'awb'         => $awb,
        'photo-type'  => 'pod_recipient',
        'courier-id'  => $courierId,
        'customer-id' => $customerId,
        'captured-at' => now()->toIso8601String(),
        'source'      => 'camera',
    ],
]);
```

### Laravel: presigned upload untuk aplikasi mobile (disarankan)

Backend membuat URL upload sementara. Aplikasi mobile meng-upload langsung ke SILO tanpa memegang kredensial.
Metadata ikut ditandatangani, jadi aplikasi **wajib** mengirim header yang dikembalikan backend.

```php
// routes/api.php -> POST /api/pod-photo/upload-url  (Laravel 11+, disk s3; cek dokumentasi versi Laravel Anda)
['url' => $url, 'headers' => $headers] = Storage::disk('s3')->temporaryUploadUrl(
    $path,
    now()->addMinutes(10),
    [
        'ContentType' => 'image/jpeg',
        'Metadata'    => ['awb' => $awb, 'photo-type' => 'pod_recipient', 'courier-id' => $courierId],
    ]
);

return response()->json(['url' => $url, 'headers' => $headers, 'object_key' => $path]);
```

> Jika library HTTP di aplikasi menolak header `Host` dari daftar `headers`, lewati header itu. Header lain
> (`Content-Type`, `x-amz-meta-*`) wajib dikirim persis sama.

### Node.js / TypeScript (AWS SDK v3)

```ts
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";

const s3 = new S3Client({
  endpoint: process.env.AWS_ENDPOINT,            // https://s3.pcpexpress.com
  region: process.env.AWS_DEFAULT_REGION ?? "us-east-1",
  forcePathStyle: true,                           // wajib untuk SILO
  credentials: { accessKeyId: process.env.AWS_ACCESS_KEY_ID!, secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY! },
});

await s3.send(new PutObjectCommand({
  Bucket: process.env.AWS_BUCKET,                 // ekas-pcptransport
  Key: key,
  Body: photoBuffer,
  ContentType: "image/jpeg",
  Metadata: { awb, "photo-type": "pod_recipient", "courier-id": courierId, source: "camera" },
}));
```

### Flutter / Dart (upload ke presigned URL dari backend)

```dart
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;

Future<String?> uploadPodPhoto(File photo, String awb) async {
  // 1. Minta URL upload ke backend Anda (bukan ke engine)
  final r = await http.post(Uri.parse('$backendUrl/api/pod-photo/upload-url'),
      headers: {'Authorization': 'Bearer $userToken', 'Content-Type': 'application/json'},
      body: jsonEncode({'awb': awb})).timeout(const Duration(seconds: 10));
  final upload = jsonDecode(r.body) as Map<String, dynamic>;

  // 2. Upload file ASLI dari kamera (jangan dikompres) dengan header yang diberikan backend
  final headers = Map<String, String>.from(
      (upload['headers'] as Map).map((k, v) => MapEntry(k.toString(), v is List ? v.first.toString() : v.toString())))
    ..remove('Host');
  final put = await http.put(Uri.parse(upload['url']), headers: headers, body: await photo.readAsBytes())
      .timeout(const Duration(seconds: 60));
  return put.statusCode == 200 ? upload['object_key'] as String : null;
}
```

Saat mengambil foto dengan `image_picker`, **jangan** mengisi `imageQuality`, `maxWidth`, atau `maxHeight`
dengan nilai kecil. Itu penyebab utama foto pecah. Biarkan default (foto asli).

### Kotlin / Android (OkHttp)

```kotlin
val body = photoFile.asRequestBody("image/jpeg".toMediaType())
val request = Request.Builder()
    .url(upload.url)
    .put(body)
    .apply { upload.headers.filterKeys { it != "Host" }.forEach { (k, v) -> header(k, v) } }
    .build()
okHttpClient.newCall(request).execute().use { resp ->
    if (!resp.isSuccessful) { /* simpan ke antrean retry lokal, JANGAN blok update status */ }
}
```

### Swift / iOS (URLSession)

```swift
var request = URLRequest(url: upload.url)
request.httpMethod = "PUT"
for (key, value) in upload.headers where key != "Host" { request.setValue(value, forHTTPHeaderField: key) }
let (_, response) = try await URLSession.shared.upload(for: request, from: photoData)
if (response as? HTTPURLResponse)?.statusCode != 200 { /* antre ulang, jangan blok proses kurir */ }
```

Setelah upload berhasil, tidak ada lagi yang perlu dilakukan untuk Level 1. Engine memprosesnya sendiri.

---

## 6. Level 2: panggil API engine

### 6.1 Endpoint utama

`POST /v1/analyze`, dengan dua bentuk:

| Bentuk | Content-Type | Isi | Kapan |
|---|---|---|---|
| **Referensi objek (2A)** | `application/json` | `{"object_key": "...", "awb": "...", ...}` | Foto sudah di SILO. Paling hemat kuota kurir |
| **Upload file (2B)** | `multipart/form-data` | field `file` + metadata sebagai form field | Aplikasi tidak menyimpan ke SILO sendiri |

Parameter query:

| Parameter | Default | Keterangan |
|---|---|---|
| `sync` | `false` | `true` = tunggu hasil sampai `timeout_ms`. Lewat batas: tetap `202` (fail-open) |
| `timeout_ms` | `3000` | 100-30000. Disarankan ≤ 3000 |
| `lang` | sesuai `Accept-Language` | `id` atau `en` |
| `tenant` | tenant API key | Hanya untuk key admin/ops |

Respons:

| HTTP | Arti | Yang dilakukan aplikasi |
|---|---|---|
| `200` | Hasil sudah ada (`status: done` atau `error`) | Baca `allow_status_update` dan `courier_message` |
| `202` | Diterima, masih diproses | Lanjutkan proses (`allow_status_update` bernilai `true`). Ambil hasil nanti via `poll_url` atau webhook |
| `4xx` | Request salah (lihat [kode error](#10-kode-error)) | Perbaiki request, jangan diulang terus |
| `5xx` / timeout | Engine bermasalah | **Fail-open**: lanjutkan proses seperti biasa |

### 6.2 Contoh dengan curl

```bash
# 2A: foto sudah ada di SILO
curl -s -X POST "$PQE_URL/v1/analyze?sync=true&timeout_ms=3000" \
  -H "X-API-Key: $PQE_API_KEY" -H "Content-Type: application/json" -H "Accept-Language: id" \
  -d '{"object_key":"pod/2026/10/22/AWB123_K045_1729580000.jpg","awb":"AWB123","photo_type":"pod_recipient","courier_id":"K045"}'

# 2B: kirim file
curl -s -X POST "$PQE_URL/v1/analyze?sync=true" -H "X-API-Key: $PQE_API_KEY" \
  -F "file=@foto.jpg" -F "awb=AWB123" -F "photo_type=pod_recipient" -F "courier_id=K045" -F "source=camera"

# ambil hasil (mode async)
curl -s -H "X-API-Key: $PQE_API_KEY" "$PQE_URL/v1/photos/<id>"
```

### 6.3 Contoh respons

```json
{
  "id": "e350b8bdf97e4be6afd39df178bd922c",
  "tenant": "courier-app",
  "status": "done",
  "verdict": "REJECT",
  "verdict_text": "Foto tidak layak",
  "action": "reject",
  "allow_status_update": false,
  "courier_message": "Wajib foto ulang. Foto buram. Pegang HP dengan stabil, ketuk layar untuk fokus, lalu foto ulang.",
  "retake_required": true,
  "retake_message": "Kualitas foto tidak memenuhi syarat. Silakan ambil foto ulang.",
  "photo_type": "pod_recipient",
  "profile": "pod_recipient",
  "awb": "AWB123",
  "courier_id": "K045",
  "customer_id": null,
  "branch": null,
  "external_id": null,
  "captured_at": null,
  "received_at": "2026-10-22T16:36:49.747312+00:00",
  "analyzed_at": "2026-10-22T16:36:50.059097+00:00",
  "scores": { "sharpness": 0.0, "resolution": 100.0, "exposure": 100.0, "authenticity": 100.0, "authenticity_risk": 0.0 },
  "reasons": [
    {
      "code": "BLURRY", "check": "sharpness", "severity": "reject", "score": 0.0, "detail": {},
      "message": "Foto buram",
      "suggestion": "Pegang HP dengan stabil, ketuk layar untuk fokus, lalu foto ulang."
    },
    {
      "code": "EXIF_MISSING", "check": "exif", "severity": "info", "score": null, "detail": {},
      "message": "Metadata kamera tidak tersedia",
      "suggestion": "Tidak perlu tindakan. Ini hanya informasi."
    }
  ],
  "review": { "status": "none", "by": null, "at": null, "note": null },
  "evidence_eligible": false,
  "stored": null,
  "object": { "bucket": "ekas-pcptransport", "key": "pod/2026/10/22/AWB123_K045_1729580000.jpg" },
  "sha256": "2a7572fdcb5afcfcd8beb90057115f6f0e643f33ab081b7a5de202e3516e82ce",
  "error": null,
  "urls": {
    "original": "https://s3.pcpexpress.com/ekas-pcptransport/pod/...jpg?X-Amz-Signature=...",
    "processed": "https://s3.pcpexpress.com/photos-processed/courier-app/2026/10/22/e350....jpg?X-Amz-Signature=...",
    "thumbnail": "https://s3.pcpexpress.com/photos-thumb/courier-app/2026/10/22/e350....jpg?X-Amz-Signature=...",
    "expires_in": 900
  }
}
```

Arti field penting:

| Field | Nilai | Arti |
|---|---|---|
| `status` | `pending` / `done` / `error` | `error` = file rusak atau gagal diproses (lihat `error.code`) |
| `verdict` | `PASS` / `WARN` / `REJECT` | Penilaian kualitas |
| `allow_status_update` | `true` / `false` | **Paling penting.** `false` = foto ditolak (`action: reject`, yaitu `REJECT` di mode `enforce`, atau ops meminta foto ulang / menandai kecurangan): aplikasi pengiriman **tidak boleh** update status pengiriman sampai foto ulang diterima. `pending` atau gagal karena sistem (`status: error` selain file rusak) = `true` (fail-open). File rusak (`UNREADABLE_FILE`) diperlakukan seperti `REJECT`. Lihat [§7](#7-menampilkan-hasil-ke-kurir) |
| `courier_message` | teks / `null` | Satu kalimat siap tampil untuk kurir (ID/EN sesuai `Accept-Language`): `<message>. <suggestion>` dari alasan pertama, diawali `Wajib foto ulang.` jika `retake_required`. `null` = kurir tidak perlu melihat apa pun (`action: log`) |
| `action` | `log` / `notify` / `reject` | Ketegasan saat ini, sudah memperhitungkan mode tenant (`shadow` → `log`, `warn` → `notify`, `enforce` → `reject` untuk `REJECT`) |
| `retake_required` | `true` / `false` | `true` = kualitas tidak memenuhi syarat (verdict `REJECT`, termasuk file rusak), atau ops meminta foto ulang |
| `retake_message` | teks / `null` | Pesan foto ulang (ID/EN). Hanya terisi jika `retake_required: true` |
| `stored` | objek / `null` | Lokasi **permanen** foto pengiriman di SILO: `{"bucket", "key", "url"}` (`url` path-style, contoh `https://s3.pcpexpress.com/photos-processed/...jpg`, tanpa tanda tangan). Simpan `url` ini di database aplikasi. Terisi hanya jika `allow_status_update: true`: versi hasil di `photos-processed` (foto upload yang tidak perlu dikompres pun disalin ke sana), atau foto asli untuk foto dari bucket aplikasi yang tidak perlu dikompres / analisis belum selesai. `null` = foto ditolak |
| `evidence_eligible` | `true` / `false` | Apakah foto ini tampil di evidence pelanggan (lihat [§9](#9-evidence-untuk-pelanggan-penagihan)) |
| `reasons[].severity` | `reject` / `warn` / `info` | `info` hanya informasi, jangan ditampilkan sebagai kesalahan |
| `scores` | 0-100 | Makin tinggi makin baik. `authenticity_risk` makin tinggi makin mencurigakan |
| `urls.processed` | URL sementara | Versi teroptimasi (<1 MB), isi gambar tidak dimanipulasi. Foto kecil: sama dengan original. Berlaku `expires_in` detik |
| `urls.original` | URL sementara | File asli dari kamera, **tidak pernah diubah**. `sha256` selalu merujuk ke file ini |

Detail foto (`GET /v1/photos/{id}`) dan endpoint uji `POST /v1/analyze/preview` juga berisi objek `enhancement`
(koreksi pencahayaan opsional; bawaan `{"attempted": false, "applied": false}`). Penjelasannya di [§7.1](#71-optimasi-tanpa-manipulasi).

### 6.4 Laravel (backend) dengan fail-open

Cara paling sederhana: salin [`clients/php/PhotoQualityClient.php`](../clients/php/PhotoQualityClient.php) dan pakai
`checkFile` (scan dulu, baru simpan; lihat [§0](#0-cara-tercepat-scan-dulu-baru-simpan)). Contoh di bawah adalah alternatif
level 2A (foto sudah tersimpan di SILO) dengan seluruh hasil lewat `analyzeObject`:

```php
// config/services.php
'pqe' => ['url' => env('PQE_URL'), 'key' => env('PQE_API_KEY')],

// Controller setelah foto tersimpan di SILO
$pqe = new PhotoQualityClient(config('services.pqe.url'), config('services.pqe.key'), 5000, 'id');
$r = $pqe->analyzeObject($path, [
    'awb' => $awb, 'photo_type' => 'pod_recipient', 'courier_id' => $courierId,
    'captured_at' => now()->toIso8601String(), 'external_id' => (string) $photo->id,
], true, 3000);

$allow = true;    // fail-open: engine mati/lambat/error -> status tetap diupdate
$message = null;
if ($r['ok'] && ($r['data']['status'] ?? null) === 'done') {
    $allow = $r['data']['allow_status_update'];   // false = foto ditolak
    $message = $r['data']['courier_message'];     // null = tidak perlu menampilkan apa pun
}
if (!$allow) {
    return response()->json(['ok' => false, 'foto_ulang' => true, 'pesan' => $message], 422);  // status TIDAK diupdate
}
$shipment->updateStatus('DELIVERED');
return response()->json(['ok' => true, 'pesan' => $message]);
```

Tanpa client, dengan `Http` bawaan Laravel:

```php
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\Http;

try {
    $res = Http::withHeaders(['X-API-Key' => config('services.pqe.key'), 'Accept-Language' => 'id'])
        ->connectTimeout(2)->timeout(5)
        ->post(config('services.pqe.url') . '/v1/analyze?sync=true&timeout_ms=3000', [
            'object_key' => $path, 'awb' => $awb, 'photo_type' => 'pod_recipient',
        ]);
    $quality = $res->status() === 200 ? $res->json() : null;
} catch (ConnectionException $e) {
    $quality = null; // engine tidak tersedia: lanjutkan proses, JANGAN gagalkan request kurir
}
$allow = $quality['allow_status_update'] ?? true;   // null/tidak ada hasil -> fail-open
```

### 6.5 Node.js / TypeScript (backend)

Salin [`clients/typescript/pqe-client.ts`](../clients/typescript/pqe-client.ts) (tanpa dependency). Paling ringkas:
`checkObject` / `checkFile` (lihat [§0](#0-cara-tercepat-scan-dulu-baru-simpan)). Jika butuh seluruh hasil:

```ts
import { PhotoQualityClient } from "./pqe-client.ts";

const pqe = new PhotoQualityClient({ baseUrl: process.env.PQE_URL!, apiKey: process.env.PQE_API_KEY!, lang: "id" });

const r = await pqe.analyzeObject(objectKey, { awb, photo_type: "pod_recipient", courier_id: courierId }, { sync: true, timeoutMs: 3000 });
if (r.ok && r.photo?.status === "done") {
  // r.photo.allow_status_update (false = JANGAN update status), r.photo.courier_message, r.photo.reasons
} else {
  // r.unavailable === true / pending -> engine lambat/mati. Lanjutkan proses (fail-open).
}
```

### 6.6 Python

```python
import requests

def analyze(object_key: str, awb: str) -> dict | None:
    try:
        r = requests.post(
            f"{PQE_URL}/v1/analyze", params={"sync": "true", "timeout_ms": 3000},
            headers={"X-API-Key": PQE_API_KEY, "Accept-Language": "id"},
            json={"object_key": object_key, "awb": awb, "photo_type": "pod_recipient"},
            timeout=(2, 5),
        )
        return r.json() if r.ok else None
    except requests.RequestException:
        return None  # fail-open
```

### 6.7 Mode async + polling

Jika tidak perlu hasil langsung, panggil tanpa `sync` (respons `202` + `poll_url`), lalu ambil hasilnya nanti:

```ts
const sent = await pqe.analyzeObject(objectKey, { awb });
if (sent.ok) {
  const done = await pqe.waitForResult(sent.photo.id, { timeoutMs: 15000 });  // polling tiap 1 detik
}
```

Lebih efisien lagi: pakai [webhook](#8-menerima-webhook) supaya tidak perlu polling.

### 6.8 Mencari hasil berdasarkan resi

```bash
curl -s -H "X-API-Key: $PQE_API_KEY" "$PQE_URL/v1/photos?awb=AWB123"
curl -s -H "X-API-Key: $PQE_API_KEY" "$PQE_URL/v1/photos?external_id=foto-998877"
```

---

## 7. Menampilkan hasil ke kurir

Status pengiriman **milik aplikasi pengiriman**; engine hanya memberi keputusan. Dua field menentukan semuanya:

- **`allow_status_update`** menjawab *"boleh update status pengiriman sekarang?"*. `false` hanya jika foto ditolak:
  `REJECT` di mode `enforce` (`action: reject`), atau ops meminta foto ulang / menandai kecurangan. Selama `false`,
  aplikasi pengiriman **tidak boleh** mengupdate status; kurir mengambil foto ulang dan aplikasi mengirim lagi.
  Engine mati, lambat, error, atau masih `pending` = `true` (fail-open).
- **`courier_message`** adalah kalimat siap tampil untuk kurir, atau `null` jika kurir tidak perlu melihat apa pun.

Nilai keduanya sudah memperhitungkan mode yang diatur admin (`shadow` → `warn` → `enforce`), jadi aplikasi tidak perlu
diubah saat mode dinaikkan. `retake_required` (kualitas tidak memenuhi syarat) dan `action` tetap tersedia jika
aplikasi butuh detail.

| Kondisi | Perilaku aplikasi yang disarankan |
|---|---|
| `allow_status_update: false` (`action: reject`) | Tampilkan `courier_message` + tombol **Foto ulang**. **Status pengiriman tidak diupdate** sampai foto ulang diterima |
| `action: notify` + `retake_required: true` (mode `warn`) | Tampilkan `courier_message`, beri pilihan **"Foto ulang"** atau **"Tetap kirim"**. Status boleh diupdate |
| `action: notify` (verdict `WARN`) | Opsional: tampilkan `courier_message` sebagai saran ringan. Foto diterima dan masuk evidence (bawaan), status diupdate |
| `action: log` (verdict `PASS`, atau mode `shadow`) | Tidak menampilkan apa-apa (`courier_message: null`). Status diupdate |
| *(tidak ada hasil / engine tidak tersedia / `202`)* | Tidak menampilkan apa-apa. Update status seperti biasa (fail-open) |

Contoh tampilan (`courier_message` apa adanya; bahasa mengikuti `Accept-Language`):

```
⚠ Wajib foto ulang. Foto buram. Pegang HP dengan stabil, ketuk layar untuk fokus, lalu foto ulang.
[ Foto ulang ]                      (status pengiriman belum berubah)
```

Di mode `warn` tambahkan tombol **Tetap kirim** (status boleh diupdate karena `allow_status_update: true`).

Flutter (contoh widget, data dari backend Anda):

```dart
Widget qualityBanner(Map<String, dynamic>? q) {
  final msg = q?['courier_message'] as String?;
  if (msg == null) return const SizedBox.shrink();
  final blocked = q!['allow_status_update'] == false;
  return Card(
    color: blocked ? Colors.red.shade50 : Colors.orange.shade50,
    child: ListTile(
      title: Text(msg),
      trailing: blocked ? const Text('Foto ulang') : null,
    ),
  );
}
```

Daftar semua kode alasan dan pesannya: `GET /v1/meta/reason-codes?lang=id` atau [api-reference.md](api-reference.md#kode-alasan).

### 7.1 Optimasi tanpa manipulasi

Engine **tidak memanipulasi isi gambar**: tidak ada AI, filter, penajaman, crop, atau koreksi warna/cahaya. Yang
dilakukan hanya optimasi dan kompresi dinamis, sehingga keabsahan dokumen bukti penagihan terjaga:

- **Foto kecil tidak disentuh** (sudah di bawah target dan ukurannya wajar): disimpan apa adanya, `urls.processed`
  menunjuk ke original.
- Foto besar **diperkecil proporsional** bila perlu, lalu dicari kualitas JPEG tertinggi yang muat < 1 MB. Pengaman
  SSIM memastikan teks tetap terbaca; jika target tidak tercapai tanpa merusak keterbacaan, keterbacaan diutamakan
  (kode info `COMPRESSION_TARGET_NOT_MET`).
- Rotasi mengikuti orientasi kamera (EXIF) agar foto tidak miring saat ditampilkan.
- **Original selalu disimpan utuh** di SILO; `sha256`/kode verifikasi selalu merujuk ke original.
- Foto yang kualitasnya tidak memenuhi syarat (buram, pecah, terlalu kecil, terlalu gelap/terang/pudar) **tidak
  "dibenarkan"**: engine mengembalikan `retake_required: true` dan kurir diminta foto ulang.

#### Koreksi pencahayaan opsional (nonaktif secara bawaan)

Admin bisa mengizinkan koreksi pencahayaan per profile (`enhance.enabled: true`, lihat
[configuration.md](configuration.md#perbaikan-pencahayaan-otomatis-enhance)). Jika aktif, foto `TOO_DARK`,
`TOO_BRIGHT`, `LOW_CONTRAST`, atau `GLARE` dikoreksi dengan satu kurva pencahayaan global (levels + gamma) yang sama
untuk seluruh piksel dan ketiga kanal warna, lalu diverifikasi eksak (setiap nilai masukan → tepat satu nilai keluaran,
urutan tidak terbalik). Karena kurva memberi nilai yang sama untuk nilai yang sama di mana pun posisinya, koreksi ini
tidak bisa menambah atau mengubah teks/angka. Hasil hanya dipakai jika verdict membaik; skor ketajaman dan resolusi
tetap dari foto asli.

Objek `enhancement` di detail foto (`GET /v1/photos/{id}`) dan di `POST /v1/analyze/preview`. Bawaan (nonaktif):
`{"attempted": false, "applied": false}`. Jika aktif dan dipakai:

```json
"enhancement": {
  "attempted": true,
  "applied": true,
  "operations": ["levels:12-140", "gamma:0.62"],
  "verified": "global_monotonic_curve",
  "before": { "verdict": "REJECT", "scores": { "exposure": 0.0 }, "codes": ["TOO_DARK"] }
}
```

| Field | Arti |
|---|---|
| `attempted` | Koreksi dicoba (hanya jika `enhance.enabled: true` dan ada alasan `TOO_DARK`/`TOO_BRIGHT`/`LOW_CONTRAST`/`GLARE`) |
| `applied` | Hasil koreksi dipakai untuk versi processed dan verdict |
| `operations` | Kurva yang dipakai: `levels:<min>-<max>` dan/atau `gamma:<nilai>` |
| `before` | Verdict, skor, dan kode alasan **sebelum** koreksi |
| `reason` | Jika tidak dipakai: `nothing_to_fix` (kurva tidak perlu), `verification_failed` (verifikasi gagal, dibuang), `no_improvement` (verdict tidak membaik) |

---

## 8. Menerima webhook

Engine bisa mengirim hasil ke backend Anda, sehingga tidak perlu polling. Minta admin menambahkan *sink* di config tenant:

```yaml
sinks:
  - type: webhook
    url: https://backend-anda.internal/hooks/photo-quality
    secret_env: COURIER_WEBHOOK_SECRET      # nama env di server ENGINE yang berisi secret
    events: [analysis.completed, review.updated]
```

### Format

```
POST /hooks/photo-quality
Content-Type: application/json
X-PQE-Event: analysis.completed
X-PQE-Delivery: 1234
X-PQE-Timestamp: 1761151010
X-PQE-Signature: sha256=5f2c...

{"event":"analysis.completed","photo":{ ...objek foto, sama dengan respons API tanpa field urls (termasuk allow_status_update, courier_message, stored, retake_required & retake_message)... }}
```

| Event | Kapan |
|---|---|
| `analysis.completed` | Foto selesai dianalisis (termasuk `status: error` untuk file rusak) |
| `review.updated` | Ops mengubah keputusan review (approve, minta foto ulang, kecurangan) |

Aturan penerima:

- Balas **2xx secepatnya** (di bawah 5 detik). Proses berat lakukan di background/queue Anda.
- **Verifikasi tanda tangan** dengan body mentah, dan tolak timestamp yang lebih tua dari 5 menit.
- **Idempotent**: webhook bisa terkirim lebih dari sekali. Pakai `X-PQE-Delivery` atau `photo.id` untuk dedup.
- Jika gagal (non-2xx/timeout), engine mencoba ulang dengan jeda bertambah (30 dtk, 1, 2, 4, 8, 16, 32 menit),
  maksimal 8 kali.

### Verifikasi tanda tangan

Laravel:

```php
Route::post('/hooks/photo-quality', function (Illuminate\Http\Request $request) {
    $ok = PhotoQualityClient::verifyWebhook(
        env('PQE_WEBHOOK_SECRET'), $request->header('X-PQE-Timestamp'),
        $request->getContent(), $request->header('X-PQE-Signature')
    );
    abort_unless($ok, 401);
    dispatch(new HandlePhotoQuality($request->json()->all()));   // proses di queue
    return response()->noContent();
});
```

Node.js (Express, body harus mentah):

```ts
import express from "express";
import { verifyWebhook } from "./pqe-client.ts";

app.post("/hooks/photo-quality", express.raw({ type: "application/json" }), async (req, res) => {
  const ok = await verifyWebhook(process.env.PQE_WEBHOOK_SECRET!, req.get("X-PQE-Timestamp"), req.body, req.get("X-PQE-Signature"));
  if (!ok) return res.sendStatus(401);
  res.sendStatus(204);
  const event = JSON.parse(req.body.toString("utf8"));   // proses setelah membalas
});
```

Python:

```python
import hashlib, hmac, time

def verify(secret: str, ts: str, raw_body: bytes, signature: str) -> bool:
    if not ts or abs(time.time() - int(ts)) > 300:
        return False
    mac = hmac.new(secret.encode(), ts.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={mac}", signature or "")
```

---

## 9. Evidence untuk pelanggan (penagihan)

Evidence hanya berisi foto yang **layak**. Aturannya tergantung setelan tenant `review_required`
(lihat [configuration.md](configuration.md#21-field-tingkat-atas)):

| `review_required` | Yang masuk evidence |
|---|---|
| `false` (**bawaan**) | `PASS` dan `WARN` langsung masuk tanpa menunggu ops. `REJECT` tidak pernah masuk kecuali di-*approve* ops |
| `true` | Hanya `PASS`, atau `WARN`/`REJECT` yang sudah di-*approve* ops |

Foto yang ditandai ops "minta foto ulang" atau "kecurangan" tidak pernah masuk. Field `evidence_eligible` di objek
foto menunjukkan hasilnya. Skor internal dan risiko keaslian **tidak** ikut ditampilkan ke pelanggan.

| Endpoint | Hasil |
|---|---|
| `GET /v1/evidence/{awb}` | JSON: daftar foto bukti + URL gambar (berlaku 7 hari) + hash verifikasi |
| `GET /v1/evidence/{awb}/pdf` | PDF bukti pengiriman per resi |
| `POST /v1/evidence/batch` body `{"awbs": [...]}` | Satu PDF untuk banyak resi (misal lampiran invoice) |
| `GET /v1/verify/{sha256}` | Publik, tanpa API key: cek apakah file identik dengan yang diterima sistem |

Dua cara menampilkan ke pelanggan:

1. **Lewat portal/sistem invoice Anda (disarankan):** backend portal memanggil endpoint di atas dengan API key
   role `app`, lalu menampilkan hasilnya. Pelanggan tidak perlu akun baru.
2. **API key per pelanggan:** admin membuat API key role `customer` dengan `customer_id`. Key ini hanya bisa
   membaca evidence untuk foto dengan `customer_id` yang sama (pastikan aplikasi mengirim metadata `customer-id`).

```bash
curl -s -H "X-API-Key: $PQE_API_KEY" "$PQE_URL/v1/evidence/AWB123"
curl -s -H "X-API-Key: $PQE_API_KEY" -o pod-AWB123.pdf "$PQE_URL/v1/evidence/AWB123/pdf"
curl -s -X POST -H "X-API-Key: $PQE_API_KEY" -H "Content-Type: application/json" \
  -d '{"awbs":["AWB123","AWB124"]}' -o invoice-oktober.pdf "$PQE_URL/v1/evidence/batch"
```

---

## 10. Kode error

Semua error berbentuk:

```json
{"error": {"code": "TENANT_REQUIRED", "message": "parameter tenant wajib untuk API key yang tidak terikat tenant"}}
```

| HTTP | `code` | Penyebab | Solusi |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | API key kosong/salah/dicabut | Cek header `X-API-Key` dan minta key baru |
| 403 | `FORBIDDEN` | Role tidak boleh, atau tenant lain | Pakai key yang sesuai tenant/role |
| 400 | `TENANT_REQUIRED` | Key admin/ops tanpa `?tenant=` | Tambahkan `?tenant=<kode>` |
| 404 | `TENANT_NOT_FOUND` | Kode tenant salah | Cek dengan admin |
| 409 | `TENANT_DISABLED` | Tenant sedang dinonaktifkan | Hubungi admin |
| 404 | `OBJECT_NOT_FOUND` | `object_key`/bucket tidak ada di SILO | Pastikan upload selesai dan key benar |
| 404 | `PHOTO_NOT_FOUND` | ID foto salah / milik tenant lain | Cek ID |
| 413 | `FILE_TOO_LARGE` | File > `PQE_MAX_UPLOAD_MB` (default 25 MB) | Kirim file asli kamera; jangan file video/raw |
| 422 | `NO_INPUT` / `EMPTY_FILE` / `INVALID_BODY` / `VALIDATION_ERROR` | Body tidak sesuai | Lihat `message` |
| 422 | `UNREADABLE_FILE` | File bukan gambar (endpoint preview) | Kirim JPEG/PNG/HEIC/WebP |
| 500 | `INTERNAL_ERROR` | Bug/gangguan engine | **Fail-open**, laporkan ke tim engine |

Untuk foto yang gagal diproses setelah diterima, `status` = `error` dan `error.code` berisi salah satu dari:
`UNREADABLE_FILE`, `OBJECT_NOT_FOUND`, `PROCESSING_FAILED`, `TENANT_NOT_FOUND`.

---

## 11. Checklist sebelum go-live

- [ ] Engine mati/lambat **tidak menahan** update status pengiriman (uji dengan engine dimatikan: foto disimpan ke SILO dengan kode lama, status tetap diupdate)
- [ ] Foto diterima: `stored.url` disimpan di database aplikasi sebagai link foto pengiriman; foto ditolak tidak disimpan sebagai foto pengiriman
- [ ] `allow_status_update: false` → status pengiriman **tidak** diupdate, kurir diminta foto ulang (uji dengan foto buram di mode `enforce`, bisa lewat halaman Uji Coba Kamera)
- [ ] Semua panggilan ke engine punya timeout (≤ 3 dtk sync, ≤ 5 dtk lainnya) dan fail-open
- [ ] API key hanya ada di server (env), tidak di aplikasi mobile/browser, tidak di git
- [ ] Foto dikirim asli dari kamera (tanpa `imageQuality`/`maxWidth` kecil)
- [ ] Metadata `awb`, `photo-type`, `courier-id`, `customer-id` terkirim (cek di Web UI → detail foto)
- [ ] Teks ke kurir memakai `courier_message` apa adanya (saat tidak `null`); logika memakai `allow_status_update` (detail: `retake_required`/`code`/`action`)
- [ ] Webhook (jika dipakai): tanda tangan diverifikasi, balas 2xx cepat, idempotent
- [ ] Runtime Node/PHP sudah versi patch 21 Okt 2026 (lihat [clients/README.md](../clients/README.md))
- [ ] Tenant berjalan di mode `shadow` minimal 1-2 minggu sebelum `warn`/`enforce`
- [ ] Sudah dicoba end-to-end di lingkungan lokal/staging ([getting-started.md](getting-started.md))

---

## 12. FAQ integrasi

**Apakah engine membuat upload kurir lebih lambat?**
Tidak. Di Level 0/1 engine bekerja setelah upload, terpisah dari aplikasi. Di Level 2 Anda memanggilnya dengan
timeout 3 detik dan fail-open: jika lewat batas, `allow_status_update` dianggap `true`, aplikasi menyimpan foto sendiri, dan kurir lanjut.
Di level 2B foto hanya dikirim sekali (ke engine), jadi tidak ada upload ganda. Analisis umumnya 0,2-1,5 detik per foto (foto resi dengan OCR bisa sampai ~3 detik di CPU biasa).

**Bolehkah aplikasi mobile memanggil engine langsung?**
Tidak disarankan karena API key akan bocor. Panggil lewat backend Anda. Jika terpaksa dari web (misal portal
internal), admin harus mengisi `PQE_CORS_ORIGINS` dan memakai key dengan role seminimal mungkin.

**Foto dianalisis dua kali kalau saya pakai Level 1 dan Level 2 sekaligus?**
Tidak. Engine idempotent per objek (bucket + key). Panggilan API mengembalikan hasil yang sama.

**Bagaimana kalau foto ditimpa (key sama, isi berbeda)?**
Engine mendeteksi perubahan ETag dan menganalisis ulang.

**HEIC dari iPhone didukung?** Ya. JPEG, PNG, HEIC/HEIF, dan WebP didukung. Hasil kompres berupa JPEG.

**Apakah engine mengubah isi foto?**
Tidak. Engine hanya mengoptimasi ukuran dan mengompres (lihat [§7.1](#71-optimasi-tanpa-manipulasi)); tidak ada AI,
filter, atau koreksi cahaya secara bawaan. File original tetap tersimpan utuh di SILO dan kode verifikasi (SHA-256)
di PDF evidence merujuk ke original. Foto gelap/buram tidak "dibenarkan", kurir diminta foto ulang.

**Foto dikirim ke engine dulu atau ke SILO dulu?**
Ke engine dulu (level 2B, disarankan): engine memeriksa foto, lalu hanya foto yang diterima disimpan ke SILO dan
linknya dikembalikan di `stored.url`. Foto yang ditolak tidak disimpan sebagai foto pengiriman dan pesannya dikirim
balik ke kurir. Foto asli tetap diarsipkan engine di bucket internal `photos-upload` (untuk verifikasi keaslian dan
review ops), tetapi tidak pernah diberikan sebagai link. Jika engine tidak menjawab, aplikasi menyimpan foto ke SILO
sendiri seperti sekarang. Aplikasi yang sudah terlanjur menyimpan ke SILO dulu bisa memakai `checkObject` (level 2A).

**Apakah engine mengubah status pengiriman?**
Tidak. Status pengiriman milik aplikasi pengiriman. Engine hanya menjawab `allow_status_update`; aplikasi yang
memutuskan update status (dan wajib menahannya jika `false`).

**Foto `WARN` perlu menunggu persetujuan ops?**
Tidak, dengan setelan bawaan (`review_required: false`) foto `WARN` langsung masuk evidence. Hanya tenant yang
menyalakan `review_required: true` yang menunggu ops.

**Kenapa ada `EXIF_MISSING` di hampir semua foto?**
Banyak aplikasi/platform menghapus EXIF. Ini hanya `info` dan tidak memengaruhi verdict.

**Saya ingin aturan berbeda untuk foto resi dan foto penerima.**
Kirim `photo_type` yang berbeda (`pod_document` vs `pod_recipient`). Setiap jenis punya profile sendiri yang
bisa diatur admin tanpa mengubah aplikasi.

**Bagaimana tahu threshold sudah cocok?**
Admin menjalankan mode `shadow`, meninjau hasil di Web UI, lalu menyesuaikan profile. Lihat
[configuration.md](configuration.md#6-kalibrasi-threshold).

**Siapa yang dihubungi jika ada masalah?**
Cek [troubleshooting.md](troubleshooting.md) dulu. Hampir semua masalah umum ada di sana beserta solusinya.
