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

0. [Cara tercepat: kirim foto, terima keputusan](#0-cara-tercepat-kirim-foto-terima-keputusan)
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
13. [Integrasi modul dokumen](#13-integrasi-modul-dokumen) (OCR invoice dan dokumen lain, untuk aplikasi apa pun)

---

## 0. Cara tercepat: kirim foto, terima keputusan

Engine memproses gambar. Sebelum dan sesudahnya, termasuk **menyimpan foto pengiriman ke SILO** dan status
pengiriman, dikerjakan aplikasi pengiriman ([ADR 0006](adr/0006-engine-hanya-memproses-gambar.md)). Engine hanya
menyimpan **arsipnya sendiri** di bucket `ocr` untuk review, evidence, dan backup
([ADR 0007](adr/0007-arsip-engine-di-bucket-ocr.md)); bucket aplikasi tidak pernah ditulis engine.

Aplikasi mengirim foto dari kurir ke engine: `POST /v1/analyze?timeout_ms=3000` dengan file foto (multipart), atau
`checkFile(...)` di client. Engine memeriksa foto, lalu menjawab dengan keputusan dan, untuk foto yang diterima,
**foto hasil optimasi** (`output_image`, base64, < 1 MB, tanpa manipulasi isi). Engine **tidak menyimpan foto ke
bucket aplikasi**; salinannya hanya masuk arsip engine (`ocr`) di belakang layar. Satu request, tunggu maks. 3 detik,
tanpa polling dan tanpa webhook.

| Hasil | Yang dilakukan aplikasi pengiriman |
|---|---|
| `allow_status_update: true` | Foto diterima. **Simpan `output_image` ke SILO** dengan kode Anda (di key yang Anda kirim sebagai `object_key`), update status pengiriman, tampilkan `courier_message` jika tidak `null` |
| `allow_status_update: false` | Foto ditolak (`output_image: null`). **Jangan simpan** sebagai foto pengiriman, kirim balik `courier_message` ke kurir, **jangan update status**, kurir foto ulang |
| Engine error / timeout / `202` | Simpan **foto asli** ke SILO seperti sekarang, lalu update status (fail-open). Engine tetap menyelesaikan analisisnya di belakang untuk laporan |

Status pengiriman **milik aplikasi pengiriman**. Engine tidak pernah mengubahnya dan tidak pernah menulis ke bucket
aplikasi.

**Kapan foto dikirim ke engine, dan kapan divalidasi?**

| Urutan | Siapa | Apa yang terjadi |
|---|---|---|
| 1 | Kurir | Ambil foto POD, tekan **Kirim** |
| 2 · kirim | Aplikasi pengiriman → engine | File foto dikirim ke engine (belum disimpan, status belum diupdate) bersama `object_key` (key SILO yang akan dipakai aplikasi). Satu request, tunggu maks. 3 detik |
| 3 · validasi | Engine | Validasi gambar (biasanya 0,2–2 detik): ketajaman, pecah, resolusi, cahaya, EXIF, duplikat, OCR → verdict |
| 4a · diterima | Engine → aplikasi | Optimasi + kompres < 1 MB (tanpa manipulasi). Engine menjawab `allow_status_update: true` + `output_image` (foto yang disimpan aplikasi) |
| 4b · ditolak | Engine → aplikasi → kurir | Engine menjawab `allow_status_update: false` + `courier_message`, tanpa foto. Aplikasi mengirim balik pesan itu ke kurir untuk foto ulang (kembali ke 1) |
| 5 | Aplikasi pengiriman | Diterima → simpan `output_image` ke SILO di `object_key` + update status. Ditolak → tidak disimpan, status **tidak** diupdate. Engine tidak menjawab dalam 3 detik → simpan foto asli + update status (fail-open) |

```mermaid
sequenceDiagram
    autonumber
    actor K as Kurir
    participant BE as Aplikasi Pengiriman
    participant E as Engine
    participant S as SILO
    K->>BE: Foto + resi (tekan Kirim)
    BE->>E: ① Kirim file + object_key: POST /v1/analyze
    note over E: ② Validasi + optimasi ≤ 3 dtk (arsip ke bucket ocr di belakang layar)
    alt diterima
        E-->>BE: allow_status_update true + output_image
        BE->>S: Simpan output_image di object_key (kode aplikasi)
        BE->>BE: Update status pengiriman
    else ditolak (REJECT, mode enforce)
        E-->>BE: allow_status_update false + courier_message (tanpa foto)
        BE-->>K: Kirim balik pesan, wajib foto ulang (tidak disimpan, status tidak diupdate)
    else engine tidak menjawab ≤ 3 dtk
        BE->>S: Simpan foto asli (kode lama)
        BE->>BE: Update status pengiriman (fail-open)
    end
```

Engine mengarsipkan setiap foto yang dikirim ke bucket `ocr` (`<tenant>/<yyyy>/<mm>/<dd>/<id>.<ext>`, masa simpan
520 hari): foto diterima = foto yang sama dengan `output_image`, foto ditolak = salinan review kualitas tinggi (maks. 1,5 MB)
untuk review & kalibrasi ops. Arsip ditulis setelah respons dikirim, jadi tidak menambah waktu tunggu kurir. Web UI, Evidence PDF, dan Backup NAS
memakai arsip ini.

`object_key` opsional: engine mencatat lokasi foto di bucket aplikasi (hanya untuk foto yang diterima) sebagai
pelengkap, misal untuk mencocokkan data. Jika key baru diketahui setelah menyimpan, laporkan dengan
`POST /v1/photos/{id}/stored` (`reportStored` di client).

Ukuran respons: `output_image.base64` ±33% lebih besar dari file-nya, jadi respons maks. sekitar **1,4 MB**. Foto yang
sudah memenuhi syarat ukuran dikembalikan apa adanya (`optimized: false`).

**PHP / Laravel** (salin [`clients/php/PhotoQualityClient.php`](../clients/php/PhotoQualityClient.php); `checkFile` tidak pernah throw):

```php
$pqe  = new PhotoQualityClient(env('PQE_URL'), env('PQE_API_KEY'), 5000, 'id');
$file = $request->file('photo');
$key  = "pod/" . date('Y/m/d') . "/{$awb}-" . uniqid() . ".jpg";       // key SILO milik aplikasi
$d = $pqe->checkFile($file->getRealPath(), ['awb' => $awb, 'photo_type' => 'pod_recipient',
    'courier_id' => $courierId, 'source' => 'camera'], 3000, $key);

if (!$d['allow_status_update']) {                        // DITOLAK: jangan simpan, kirim balik ke kurir
    return response()->json(['foto_ulang' => true, 'pesan' => $d['message']], 422);
}
// image null = engine tidak menjawab (fail-open) -> simpan foto asli
$isi  = $d['image']['data'] ?? file_get_contents($file->getRealPath());
$tipe = $d['image']['content_type'] ?? $file->getMimeType();
Storage::disk('s3')->put($key, $isi, ['ContentType' => $tipe]);          // disk s3 = SILO
$shipment->update(['photo_key' => $key, 'status' => 'DELIVERED']);
return response()->json(['ok' => true, 'pesan' => $d['message']]);     // pesan bisa null
```

**TypeScript / Node** (salin [`clients/typescript/pqe-client.ts`](../clients/typescript/pqe-client.ts); `checkFile` tidak pernah throw):

```ts
import { PhotoQualityClient } from "./pqe-client.ts";
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
const pqe = new PhotoQualityClient({ baseUrl: process.env.PQE_URL!, apiKey: process.env.PQE_API_KEY!, lang: "id" });
const silo = new S3Client({ endpoint: process.env.SILO_URL, region: "us-east-1", forcePathStyle: true });

const key = `pod/${new Date().toISOString().slice(0, 10)}/${awb}-${Date.now()}.jpg`;
const d = await pqe.checkFile(photoBlob, "foto.jpg", { awb, photo_type: "pod_recipient", courier_id: courierId, source: "camera" }, { objectKey: key });
if (!d.allowStatusUpdate) return res.status(422).json({ fotoUlang: true, pesan: d.message }); // ditolak: jangan simpan
const body = d.image?.data ?? new Uint8Array(await photoBlob.arrayBuffer()); // image null = engine tidak menjawab
await silo.send(new PutObjectCommand({ Bucket: "ekas-pcptransport", Key: key, Body: body, ContentType: d.image?.contentType ?? "image/jpeg" }));
await shipment.update({ photoKey: key, status: "DELIVERED" });
res.json({ ok: true, pesan: d.message });
```

**curl:**

```bash
curl -s -m 8 -X POST "$PQE_URL/v1/analyze?timeout_ms=3000" \
  -H "X-API-Key: $PQE_API_KEY" -H "Accept-Language: id" \
  -F "file=@foto.jpg" -F "awb=AWB123" -F "photo_type=pod_recipient" -F "source=camera" \
  -F "object_key=pod/2026/10/24/AWB123.jpg" > hasil.json
# diterima -> {"allow_status_update": true, "output_image": {"content_type": "image/jpeg", "bytes": 412345, "optimized": true, "sha256": "...", "base64": "/9j/..."}, ...}
# ditolak  -> {"allow_status_update": false, "output_image": null, "courier_message": "Wajib foto ulang. Foto buram. ...", ...}
jq -r '.output_image.base64 // empty' hasil.json | base64 -d > hasil.jpg   # file yang disimpan aplikasi ke SILO
```

Keputusan dari `checkFile` / `checkObject`:

| TypeScript | PHP | Arti |
|---|---|---|
| `allowStatusUpdate` | `allow_status_update` | `false` = foto ditolak: **jangan** simpan, **jangan** update status, minta foto ulang |
| `message` | `message` | Tampilkan ke kurir jika tidak `null` |
| `image` | `image` | Foto yang disimpan aplikasi ke SILO: `{data, contentType, optimized, sha256}` (PHP: `data`, `content_type`, `optimized`, `sha256`). `null` = ditolak, atau engine tidak menjawab (simpan foto asli) |
| `retakeRequired` | `retake_required` | Kualitas tidak memenuhi syarat (verdict `REJECT`) |
| `decided` | `decided` | `false` = belum ada keputusan (engine mati/lambat/error, atau lewat 3 detik) → fail-open: simpan foto asli, status boleh diupdate |
| `photoId`, `verdict` | `photo_id`, `verdict` | Untuk disimpan/dilacak (misal untuk `reportStored`) |

Foto sudah terlanjur disimpan ke SILO oleh aplikasi? Pakai `checkObject(objectKey, ...)` (kirim key objek, bukan file);
engine hanya membacanya. Keputusannya sama, tetapi foto yang ditolak sudah terlanjur ada di bucket aplikasi. Kurir
hanya ditahan jika foto `REJECT` **dan** mode tenant `enforce` (atau ops menandai "minta foto ulang"); di mode
`shadow`/`warn` foto tidak pernah menahan status. Menahan status hanya bisa di level 2 (sinkron); level 0/1 menganalisis
setelahnya. Contoh Python ada di panduan browser `/ui/guide.html#cepat`.

---

## 1. Aturan emas (wajib dibaca)

1. **Status pengiriman milik aplikasi pengiriman, dan engine tidak boleh menahan kurir karena dirinya sendiri.**
   Engine tidak pernah mengubah status; engine hanya menjawab `allow_status_update`. Satu-satunya yang menahan update
   status adalah `allow_status_update: false` (foto ditolak di mode `enforce`): status baru diupdate setelah foto ulang diterima.
   Penyimpanan foto pengiriman juga milik aplikasi: aplikasi yang menyimpan `output_image` ke bucket-nya. Engine tidak
   pernah menulis ke bucket aplikasi (engine hanya mengisi arsipnya sendiri di bucket `ocr`).
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
    E -- Ya, disarankan --> L2B[Level 2B<br/>kirim file ke POST /v1/analyze<br/>aplikasi menyimpan foto yang diterima]
    E -- Tidak, sudah terlanjur di SILO --> L2A[Level 2A<br/>simpan ke SILO lalu panggil<br/>POST /v1/analyze dengan object_key]
```

| Level | Yang dikerjakan aplikasi | Hasil | Kapan dipilih |
|---|---|---|---|
| **0** | Tidak ada. Upload ke SILO seperti biasa | Laporan & review ops, evidence pelanggan | Aplikasi lama yang belum bisa diubah |
| **1** | Tambah metadata (`awb`, `photo-type`, ...) saat upload | Sama dengan level 0, tapi data resi/kurir akurat | Aplikasi yang sedang dikembangkan (disarankan minimal) |
| **2B** (disarankan) | Kirim file foto ke engine, terima keputusan + foto hasil optimasi, lalu **aplikasi** menyimpannya ke SILO ([cara tercepat](#0-cara-tercepat-kirim-foto-terima-keputusan)) | Keputusan per foto langsung (`courier_message`, `allow_status_update`) + `output_image` untuk foto yang diterima | Semua aplikasi yang bisa diubah: foto ditolak tidak pernah disimpan, foto yang disimpan sudah < 1 MB |
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
  `invoice` (invoice/struk/kwitansi), `accident` (bukti kecelakaan/kerusakan; teks tidak wajib), `package`, `goods`,
  `location`, `other` (bukti lain; teks tidak wajib), atau jenis lain yang dibuat admin di config tenant.
- Field tambahan yang bisa dikirim: `reference` (nomor lain yang harus ada di foto, contoh nomor invoice atau nomor
  laporan kecelakaan; dicocokkan dengan barcode/teks) dan `dest_lat`/`dest_lng` (koordinat alamat tujuan; foto yang
  diambil jauh dari sini ditandai untuk review).

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
| **Upload file (2B)** | `multipart/form-data` | field `file` + metadata sebagai form field, opsional `object_key` (key SILO yang akan dipakai aplikasi) dan `bucket` | Foto belum disimpan; aplikasi menyimpan `output_image` setelah diterima. Engine hanya mengarsipkan salinannya di bucket `ocr` |

Parameter query:

| Parameter | Default | Keterangan |
|---|---|---|
| `sync` | `false` | Hanya untuk referensi objek: `true` = tunggu hasil sampai `timeout_ms`. Upload file selalu dianalisis langsung |
| `timeout_ms` | `3000` | 100-30000. Disarankan ≤ 3000 |
| `lang` | sesuai `Accept-Language` | `id` atau `en` |
| `tenant` | tenant API key | Hanya untuk key admin/ops |

Respons:

| HTTP | Arti | Yang dilakukan aplikasi |
|---|---|---|
| `200` | Hasil sudah ada (`status: done` atau `error`) | Baca `allow_status_update` dan `courier_message` |
| `202` | Diterima, masih diproses (lewat `timeout_ms`) | Lanjutkan proses (`allow_status_update` bernilai `true`, `output_image: null`: simpan foto asli). Ambil hasil nanti via `poll_url` atau webhook |
| `4xx` | Request salah (lihat [kode error](#10-kode-error)) | Perbaiki request, jangan diulang terus |
| `5xx` / timeout | Engine bermasalah | **Fail-open**: lanjutkan proses seperti biasa |

### 6.2 Contoh dengan curl

```bash
# 2A: foto sudah ada di SILO
curl -s -X POST "$PQE_URL/v1/analyze?sync=true&timeout_ms=3000" \
  -H "X-API-Key: $PQE_API_KEY" -H "Content-Type: application/json" -H "Accept-Language: id" \
  -d '{"object_key":"pod/2026/10/22/AWB123_K045_1729580000.jpg","awb":"AWB123","photo_type":"pod_recipient","courier_id":"K045"}'

# 2B: kirim file (selalu langsung dianalisis, tanpa sync=true)
curl -s -X POST "$PQE_URL/v1/analyze?timeout_ms=3000" -H "X-API-Key: $PQE_API_KEY" \
  -F "file=@foto.jpg" -F "awb=AWB123" -F "photo_type=pod_recipient" -F "courier_id=K045" -F "source=camera" \
  -F "object_key=pod/2026/10/22/AWB123_K045.jpg"

# 2B, opsional: laporkan key SILO setelah aplikasi menyimpan foto (jika belum dikirim sebagai object_key)
curl -s -X POST "$PQE_URL/v1/photos/<id>/stored" -H "X-API-Key: $PQE_API_KEY" \
  -H "Content-Type: application/json" -d '{"object_key":"pod/2026/10/22/AWB123_K045.jpg"}'

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
  "object": { "bucket": "ekas-pcptransport", "key": "pod/2026/10/22/AWB123_K045_1729580000.jpg" },
  "sha256": "2a7572fdcb5afcfcd8beb90057115f6f0e643f33ab081b7a5de202e3516e82ce",
  "error": null,
  "urls": {
    "original": "https://s3.pcpexpress.com/ekas-pcptransport/pod/...jpg?X-Amz-Signature=...",
    "processed": "https://s3.pcpexpress.com/ekas-pcptransport/pod/...jpg?X-Amz-Signature=...",
    "thumbnail": null,
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
| `output_image` | objek / `null` | Hanya di respons upload file (2B). Foto yang **disimpan aplikasi** ke SILO: `{"content_type", "bytes", "optimized", "sha256", "base64"}`. `optimized: false` = foto asli sudah memenuhi syarat, dikembalikan apa adanya. `null` = foto ditolak, atau engine belum selesai (`202`, simpan foto asli). Tidak ada di `GET /v1/photos/{id}` (salinannya ada di arsip engine, lihat `archive`) |
| `object` | objek / `null` | Lokasi foto di SILO aplikasi `{"bucket", "key"}`: key yang dianalisis (2A/level 0/1), atau `object_key` yang dikirim/dilaporkan aplikasi (2B, hanya untuk foto yang diterima). `null` = tidak disimpan aplikasi / lokasi belum dilaporkan |
| `archive` | objek / `null` | Salinan engine di bucket arsip `{"bucket": "ocr", "key": "<tenant>/<yyyy>/<mm>/<dd>/<id>.<ext>"}`: foto hasil untuk foto diterima, foto asli untuk foto ditolak. `null` = foto polling / `object_key` (tidak diarsipkan ulang), arsip belum selesai ditulis, atau lewat masa simpan (520 hari) |
| `evidence_eligible` | `true` / `false` | Apakah foto ini tampil di evidence pelanggan (lihat [§9](#9-evidence-untuk-pelanggan-penagihan)) |
| `reasons[].severity` | `reject` / `warn` / `info` | `info` hanya informasi, jangan ditampilkan sebagai kesalahan |
| `scores` | 0-100 | Makin tinggi makin baik. `authenticity_risk` makin tinggi makin mencurigakan |
| `urls.original`, `urls.processed` | URL sementara / `null` | `processed` = arsip engine, atau lokasi `object` jika tidak ada arsip; `original` = lokasi `object`, atau arsip jika aplikasi tidak menyimpan/melaporkan. `null` jika keduanya tidak ada. Berlaku `expires_in` detik. `urls.thumbnail` `null`: thumbnail dibuat saat diminta lewat `GET /v1/photos/{id}/file?variant=thumbnail` |
| `sha256` | hex | Hash file yang dianalisis engine (file asli dari kamera untuk 2B; `output_image.sha256` adalah hash foto hasil) |

Detail foto (`GET /v1/photos/{id}`) dan endpoint uji `POST /v1/analyze/preview` juga berisi objek `enhancement`
(koreksi pencahayaan opsional; bawaan `{"attempted": false, "applied": false}`). Penjelasannya di [§7.1](#71-optimasi-tanpa-manipulasi).

### 6.4 Laravel (backend) dengan fail-open

Cara paling sederhana: salin [`clients/php/PhotoQualityClient.php`](../clients/php/PhotoQualityClient.php) dan pakai
`checkFile` (kirim foto, terima keputusan; lihat [§0](#0-cara-tercepat-kirim-foto-terima-keputusan)). Contoh di bawah adalah alternatif
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
`checkObject` / `checkFile` (lihat [§0](#0-cara-tercepat-kirim-foto-terima-keputusan)). Jika butuh seluruh hasil:

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

**Pra-cek di HP (opsional, disarankan).** Sebelum foto dikirim, aplikasi bisa memeriksa buram/gelap/terlalu terang/
terlalu kecil langsung di HP dengan [`clients/typescript/precheck.js`](../clients/typescript/precheck.js) (JavaScript
tanpa dependency, untuk aplikasi web/WebView/React Native WebView; rumus ketajaman sama dengan engine, < 0,1 detik).
Kurir bisa langsung mengulang foto, sehingga lebih sedikit foto yang ditolak engine. Ini hanya saran: tetap kirim ke
engine, dan jangan memblokir kurir hanya dari pra-cek.

```js
import { precheckImage } from "./precheck.js";
const pc = await precheckImage(file);            // file dari <input type="file" accept="image/*" capture>
if (!pc.ok && !confirm(pc.issues.map((i) => i.message).join("\n") + "\n\nFoto ulang?")) { /* tetap kirim */ }
```

Aplikasi native (Kotlin/Swift/Flutter) bisa memakai rumus yang sama (variance of Laplacian per tile 4×4 pada sisi
panjang 1024 px, rata-rata 25% tile tertajam, skala log 4..120); lihat komentar di file tersebut. Contoh hidupnya ada
di halaman Uji Coba Kamera (bagian "Pra-cek di HP" di hasil).

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

- **Foto kecil tidak disentuh** (sudah di bawah target dan ukurannya wajar): dikembalikan apa adanya di `output_image`
  (`optimized: false`).
- Foto besar **diperkecil proporsional** bila perlu, lalu dicari kualitas JPEG tertinggi yang muat < 1 MB. Pengaman
  SSIM memastikan teks tetap terbaca; jika target tidak tercapai tanpa merusak keterbacaan, keterbacaan diutamakan
  (kode info `COMPRESSION_TARGET_NOT_MET`).
- Rotasi mengikuti orientasi kamera (EXIF) agar foto tidak miring saat ditampilkan.
- Engine tidak menyimpan foto ke bucket aplikasi. Arsip engine (`ocr`) berisi foto hasil (diterima) atau foto asli
  (ditolak). `sha256` merujuk ke file yang diterima engine; `output_image.sha256` ke foto hasil. Menyimpan foto asli
  di bucket aplikasi (selain versi optimasi) adalah keputusan aplikasi.
- Foto yang kualitasnya tidak memenuhi syarat (buram, pecah, terlalu kecil, terlalu gelap/terang/pudar) **tidak
  "dibenarkan"**: engine mengembalikan `retake_required: true` dan kurir diminta foto ulang.

#### Koreksi pencahayaan opsional (nonaktif secara bawaan)

Admin bisa mengizinkan koreksi pencahayaan per profile (`enhance.enabled: true`, lihat
[configuration.md](configuration.md#koreksi-pencahayaan-opsional-enhance)). Jika aktif, foto `TOO_DARK`,
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

{"event":"analysis.completed","photo":{ ...objek foto, sama dengan respons API tanpa field urls dan output_image (termasuk allow_status_update, courier_message, object, retake_required & retake_message)... }}
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
foto menunjukkan hasilnya. Foto evidence diambil dari arsip engine (`archive`, bucket `ocr`), atau dari lokasi di bucket
aplikasi (`object`) untuk foto polling / `object_key` dan foto yang arsipnya sudah lewat 520 hari. Skor internal dan risiko keaslian **tidak** ikut ditampilkan ke pelanggan.

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
| 404 | `FILE_NOT_FOUND` | `GET /v1/photos/{id}/file`: tidak ada arsip engine (belum selesai ditulis / lewat 520 hari) dan aplikasi tidak melaporkan lokasi, atau file sudah tidak ada | Tunggu beberapa detik (arsip ditulis di belakang layar), atau laporkan lokasi dengan `POST /v1/photos/{id}/stored` |
| 409 | `NOT_STORED` | Analisis ulang foto tanpa arsip dan tanpa lokasi aplikasi | Tidak bisa; tidak ada file untuk dianalisis ulang |
| 413 | `FILE_TOO_LARGE` | File > `PQE_MAX_UPLOAD_MB` (default 25 MB) | Kirim file asli kamera; jangan file video/raw |
| 422 | `NO_INPUT` / `EMPTY_FILE` / `INVALID_BODY` / `VALIDATION_ERROR` | Body tidak sesuai | Lihat `message` |
| 422 | `UNREADABLE_FILE` | File bukan gambar (endpoint preview) | Kirim JPEG/PNG/HEIC/WebP |
| 500 | `INTERNAL_ERROR` | Bug/gangguan engine | **Fail-open**, laporkan ke tim engine |

Untuk foto yang gagal diproses setelah diterima, `status` = `error` dan `error.code` berisi salah satu dari:
`UNREADABLE_FILE`, `OBJECT_NOT_FOUND`, `PROCESSING_FAILED`, `TENANT_NOT_FOUND`.

---

## 11. Checklist sebelum go-live

- [ ] Engine mati/lambat **tidak menahan** update status pengiriman (uji dengan engine dimatikan: foto asli disimpan ke SILO, status tetap diupdate)
- [ ] Foto diterima: aplikasi menyimpan `output_image` ke SILO (di key yang dikirim sebagai `object_key`); foto ditolak tidak disimpan sebagai foto pengiriman
- [ ] Foto yang dikirim tampil di Web UI → detail foto (dari arsip engine `ocr`); opsional: `object_key` dikirim di request sehingga baris **Lokasi di aplikasi** terisi
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
Di level 2B foto dikirim ke engine lalu versi hasilnya (< 1 MB) disimpan aplikasi ke SILO; dari HP kurir tetap hanya satu upload (HP → backend). Analisis umumnya 0,2-1,5 detik per foto (foto resi dengan OCR bisa sampai ~3 detik di CPU biasa).

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
filter, atau koreksi cahaya secara bawaan. Foto gelap/buram tidak "dibenarkan", kurir diminta foto ulang. Foto
pengiriman di bucket aplikasi adalah pilihan aplikasi (biasanya `output_image`); engine hanya menyimpan arsipnya sendiri
di bucket `ocr`.

**Foto dikirim ke engine dulu atau ke SILO dulu?**
Ke engine dulu (level 2B, disarankan): engine memeriksa dan mengoptimasi foto, lalu menjawab dengan keputusan dan
`output_image`. **Aplikasi** yang menyimpan foto yang diterima ke SILO; foto yang ditolak tidak disimpan dan pesannya
dikirim balik ke kurir. Engine tidak menulis ke bucket aplikasi (hanya ke arsipnya, `ocr`). Jika engine tidak
menjawab, aplikasi menyimpan foto asli
seperti sekarang. Aplikasi yang sudah terlanjur menyimpan ke SILO dulu bisa memakai `checkObject` (level 2A).

**Kenapa foto tidak tampil di Web UI / evidence?**
Foto upload ditampilkan dari arsip engine (`ocr`), yang ditulis beberapa detik setelah respons; cek metrik
`pqe_archive_total{result="failed"}` dan izin tulis key engine di bucket `ocr` jika arsip tidak muncul. Foto polling /
`object_key` dibaca dari bucket aplikasi. Arsip dihapus setelah 520 hari (`archive_retention_days`); setelah itu foto
hanya tampil jika aplikasi melaporkan lokasinya (`object_key` / `POST /v1/photos/{id}/stored`).

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

---

## 13. Integrasi modul dokumen

Selain foto POD, engine membaca **dokumen** (PDF digital, PDF scan, JPEG/PNG) untuk **aplikasi apa pun** dan
mengembalikan teks, blok ber-koordinat, dan field per jenis dokumen dengan confidence. Jenis dokumen = plugin; yang
terpasang sekarang: `invoice` (invoice vendor Indonesia: NPWP, nomor, tanggal, DPP, PPN, PPh, total, item). Setiap
aplikasi = satu client dengan namespace, kredensial, template, dan callback sendiri; client tidak bisa melihat data
client lain. Referensi lengkap: [api-reference.md](api-reference.md#dokumen); desain:
[document-ocr-design.md](document-ocr-design.md). Alurnya **async**: kirim file → `job_id` → polling sampai
`succeeded`/`failed`.

```mermaid
sequenceDiagram
    participant App as Backend aplikasi client
    participant E as Engine
    App->>E: POST /v1/jobs (file, document_type, template_hint opsional) + HMAC
    E-->>App: 202 {"job_id", "status": "queued"}
    loop polling 3, 5, 10, 15, 30 dtk, lalu tiap 60 dtk (maks. 10 menit)
        App->>E: GET /v1/jobs/{id} + HMAC
        E-->>App: queued / running / succeeded (fields) / failed (error.code)
    end
    App->>App: Pengguna memeriksa & mengoreksi field
    App->>E: POST /v1/templates (koreksi, Idempotency-Key) + HMAC
    E-->>App: template penerbit versi baru (dokumen berikutnya dari penerbit ini lebih akurat)
```

### 13.1 Mendaftarkan aplikasi (admin engine)

Satu perintah membuat namespace (tenant) + API key + `api_secret` untuk satu aplikasi. Keduanya hanya tampil
**sekali**; serahkan ke tim aplikasi lewat kanal aman dan simpan di `.env` server aplikasi (bukan git, bukan frontend):

```bash
docker compose -f docker-compose.prod.yml exec engine pqe create-client --code aplikasi-keuangan --name "Aplikasi Keuangan"
```

Opsional `--callback-hosts api.aplikasi.internal` bila aplikasi ingin callback (host lain ditolak). Rotasi secret:
`pqe set-api-secret <key_id>`. Cabut akses: Web UI → API key. Contoh client: ICTNotes v3 (tenant `ictnotes`) adalah
client pertama; aplikasi lain didaftarkan dengan cara yang sama.

### 13.2 Client siap pakai

Pakai [clients/php/DocumentOcrClient.php](../clients/php/DocumentOcrClient.php) atau
[clients/typescript/document-client.ts](../clients/typescript/document-client.ts): tanda tangan, multipart, polling,
koreksi idempoten, dan verifikasi callback sudah ditangani dan dites terhadap engine.

```php
$ocr = new DocumentOcrClient(env('PQE_URL'), env('PQE_API_KEY'), env('PQE_API_SECRET'));
$job = $ocr->submit($pathPdf, 'invoice', $npwpVendor);              // template_hint opsional
$res = $ocr->getJob($job['body']['job_id']);                        // dari queue worker aplikasi; atau waitFor()
if ($res['ok'] && $res['body']['status'] === 'succeeded') { $fields = $res['body']['fields']; }
```

### 13.3 Menandatangani request (bila menulis client sendiri)

```
X-Api-Key:   pqe_...
X-Timestamp: <Unix detik, angka saja>
X-Signature: hex(HMAC-SHA256(api_secret, timestamp + "\n" + METHOD + "\n" + path + "\n" + hex(sha256(body))))
```

`path` = path mentah tanpa query (contoh `/v1/jobs/3f2a...`); `body` = byte yang benar-benar dikirim (multipart:
susun body dulu, tandatangani, kirim body yang sama persis). Jam server sinkron NTP (toleransi ±300 dtk). Retry setelah
timeout: tandatangani ulang. Mengirim ulang request yang sama persis mengembalikan `job_id` yang sama.

**Python:**

```python
import hashlib, hmac, time, httpx

def headers(method, path, body, key, secret):
    ts = str(int(time.time()))
    msg = f"{ts}\n{method}\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()
    return {"X-Api-Key": key, "X-Timestamp": ts, "X-Signature": hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()}

req = httpx.Request("POST", f"{BASE}/v1/jobs", files={"file": ("dok.pdf", open("dok.pdf", "rb").read())},
                    data={"document_type": "invoice"})
body = req.read()
r = httpx.post(f"{BASE}/v1/jobs", content=body, timeout=60,
               headers={"Content-Type": req.headers["content-type"], **headers("POST", "/v1/jobs", body, KEY, SECRET)})
```

**curl (GET, cek manual):**

```bash
TS=$(date +%s); P=/v1/jobs/$JOB_ID; H=$(printf '' | sha256sum | cut -d' ' -f1)
SIG=$(printf '%s\n%s\n%s\n%s' "$TS" GET "$P" "$H" | openssl dgst -sha256 -hmac "$PQE_API_SECRET" -hex | sed 's/^.* //')
curl -s -H "X-Api-Key: $PQE_API_KEY" -H "X-Timestamp: $TS" -H "X-Signature: $SIG" "$PQE_URL$P"
```

Vektor uji: secret `abc123`, timestamp `1793337600`, `GET /v1/jobs/3f2a9c0e5b7d4e1f8a6b2c3d4e5f6a7b`, body kosong →
`452df4a4485a72a34b4335fde50ab551ed1937de296a0fb362f5f87285686595`.

### 13.4 Membaca hasil

- `fields.<nama>.value` + `confidence` (0-1). Field yang tidak ditemukan **tidak ada**; tampilkan kosong untuk diisi
  pengguna. Sarankan pemeriksaan untuk confidence < 0,8 dan semua isi `validation`.
- Format nilai: tanggal `YYYY-MM-DD`; uang string desimal (`"14430000"`, `"1250000.50"`); NPWP & rekening digit.
- `layer`: `pdf_text` atau `ocr`; `layers` juga memuat `template` dan layer plugin (invoice: `efaktur_qr`).
- `failed` → `error.code` (`UNREADABLE_FILE`, `PDF_ENCRYPTED`, `TOO_MANY_PAGES`, `RESOURCE_LIMIT`, ...): minta unggah
  ulang / isi manual. Error permanen tidak akan berhasil dengan polling lebih lama.
- Rekomendasi client: timeout POST 60 dtk, GET 10 dtk; batasi ukuran respons yang diterima ±1 MB (engine menjaga
  ≤ ±900 KB, `truncated: true`). Abaikan field/key yang tidak dikenal (API `/v1` bisa bertambah field).

### 13.5 Mengirim koreksi (belajar template)

Setelah pengguna menyimpan dokumen, kirim field yang dikoreksi dalam format kanonik. `issuer.id` = ID penerbit
(invoice: NPWP vendor). `idempotency_key` per koreksi = kunci unik pilihan aplikasi (client contoh memakai sha256
`job_id + field + JSON(extracted) + JSON(corrected)`); header `Idempotency-Key` membuat request aman dikirim ulang.

```json
{"document_type": "invoice", "issuer": {"id": "012345678901000"}, "job_id": "3f2a...",
 "corrections": [{"field": "invoice_number", "extracted": null, "corrected": "INV/2026/10/0042", "idempotency_key": "..."}]}
```

Engine mencari nilai benar di teks dokumen, menjadikan label di dekatnya sebagai anchor, dan mencatat posisinya.
Dokumen berikutnya dari penerbit yang sama (lewat `template_hint` atau penerbit yang terbaca) memakai template itu.
Nilai yang tidak ada di teks hasil OCR dicatat tetapi tidak mengubah template (`VALUE_NOT_FOUND_IN_DOCUMENT`).

### 13.6 Callback (opsional)

Kirim `callback_url` di `POST /v1/jobs` (host harus didaftarkan admin untuk client Anda). Engine mengirim
`{"event": "job.finished", "job_id", "status", "document_type"}` dengan `X-Timestamp` + `X-Signature` (HMAC
`api_secret` Anda, `path` = path callback). Verifikasi dengan `verifyCallback`, balas 2xx cepat, lalu ambil hasil
dengan `GET /v1/jobs/{id}`. Polling tetap berfungsi tanpa callback.

### 13.7 Checklist integrasi

- [ ] `api_key` dan `api_secret` hanya di `.env` server aplikasi; tidak di log, git, atau frontend
- [ ] Jam server aplikasi sinkron NTP
- [ ] Body yang ditandatangani = body yang dikirim (atau pakai client contoh)
- [ ] Timeout POST 60 dtk, GET 10 dtk; polling berhenti saat `succeeded`/`failed` atau setelah 10 menit
- [ ] Field kosong / confidence rendah / `validation` ditampilkan untuk diperiksa pengguna
- [ ] Koreksi dikirim dengan `idempotency_key` dan `Idempotency-Key`
- [ ] Dokumen asli tetap disimpan aplikasi; engine menghapus file inbox setelah 30 hari
