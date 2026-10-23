# Client siap pakai

Satu file per bahasa, tanpa dependency. Salin ke project Anda. Panduan lengkap: [docs/integration-guide.md](../docs/integration-guide.md),
atau versi interaktif di `<server-engine>/ui/guide.html` (tanpa login).

| Bahasa | File | Runtime minimal (wajib versi yang sudah di-patch) |
|---|---|---|
| TypeScript / JavaScript (Node, browser, React Native, Deno, Bun) | [typescript/pqe-client.ts](typescript/pqe-client.ts) | Node **22.23.4** / **24.22.1** (disarankan) / **26.12.1** |
| PHP (Laravel, CodeIgniter, native) | [php/PhotoQualityClient.php](php/PhotoQualityClient.php) | PHP **8.2.35** / **8.3.36** / **8.4.27** (disarankan) / **8.5.12** |

Kotlin (Android), Swift (iOS), Dart (Flutter), dan Python: contoh kode ada di panduan integrasi.

## Cara tercepat: `checkFile` (scan dulu, baru simpan)

Satu panggilan, satu keputusan. File foto dikirim langsung ke engine; engine memeriksa dulu, lalu **hanya foto yang
diterima** disimpan ke SILO dan linknya dikembalikan di `stored`. Foto ditolak tidak disimpan sebagai foto pengiriman.
Tunggu maks. 3 detik, **tidak pernah throw**, dan fail-open: engine mati, lambat, error, atau lewat batas waktu =
`stored` null, aplikasi menyimpan foto ke SILO sendiri (kode lama) dan status boleh diupdate.

```ts
// TypeScript / Node
const pqe = new PhotoQualityClient({ baseUrl: process.env.PQE_URL!, apiKey: process.env.PQE_API_KEY!, lang: "id" });
const d = await pqe.checkFile(photoBlob, "foto.jpg", { awb, photo_type: "pod_recipient", source: "camera" });
if (!d.allowStatusUpdate) return kirimKeKurir(d.message);              // ditolak: tidak disimpan, minta foto ulang
const link = d.stored?.url ?? (await uploadKeSiloSendiri(photoBlob));   // null = engine tidak menjawab (fail-open)
await updateStatusPengiriman(link);                                     // tampilkan d.message jika tidak null
```

```php
// PHP / Laravel
$pqe = new PhotoQualityClient(env('PQE_URL'), env('PQE_API_KEY'), 5000, 'id');
$d = $pqe->checkFile($pathFoto, ['awb' => $awb, 'photo_type' => 'pod_recipient', 'source' => 'camera']);
if (!$d['allow_status_update']) { /* ditolak: tidak disimpan, kirim $d['message'] ke kurir, minta foto ulang */ }
$link = $d['stored']['url'] ?? simpanKeSiloSendiri($pathFoto);   // null = engine tidak menjawab (fail-open)
/* update status pengiriman + simpan $link; tampilkan $d['message'] jika tidak null */
```

Foto sudah terlanjur disimpan ke SILO? Pakai `checkObject(objectKey, fields)` (kirim key objek). Keputusannya sama,
tetapi foto yang ditolak sudah ada di bucket aplikasi.

| TypeScript | PHP | Arti |
|---|---|---|
| `allowStatusUpdate` | `allow_status_update` | `false` = foto ditolak (`REJECT` di mode `enforce`): **jangan** update status pengiriman, minta foto ulang |
| `message` | `message` | Kalimat siap tampil untuk kurir, `null` = tidak perlu menampilkan apa pun |
| `stored` | `stored` | `{bucket, key, url}` lokasi permanen foto yang diterima di SILO (simpan `url` di database Anda). `null` = ditolak, atau engine tidak menjawab (simpan sendiri) |
| `retakeRequired` | `retake_required` | Kualitas tidak memenuhi syarat (verdict `REJECT`) |
| `decided` | `decided` | `false` = belum ada keputusan (engine mati/lambat/error/`202`) → fail-open: simpan foto sendiri, status boleh diupdate |
| `photoId`, `verdict` | `photo_id`, `verdict` | Untuk disimpan/dilacak |
| `error` (opsional) | `error` (opsional) | Pesan error jika engine tidak memberi hasil |

Status pengiriman milik aplikasi pengiriman; engine tidak pernah mengubahnya. Butuh seluruh hasil? Pakai
`analyzeObject` / `analyzeFile` (objek foto lengkap di `r.photo` / `$r['data']`, termasuk `allow_status_update`,
`courier_message`, `stored`, `retake_required`, `reasons`), lalu `toDecision(...)` untuk keputusan yang sama.

## Kenapa versi minimal ini?

Pada **21 Oktober 2026** Node.js dan PHP merilis security update mendesak:

- **Node.js** v22.23.4, v24.22.1, v26.12.1. Tingkat keparahan tertinggi HIGH, termasuk bypass validasi hostname
  sertifikat TLS (CVE-2026-106069, CVE-2026-105483) dan out-of-bounds write di decoder Brotli yang memungkinkan
  remote code execution (CVE-2026-111497). Node 20 dan 25 tidak menerima patch ini: **jangan dipakai**.
  Sumber: https://nodejs.org/en/blog/vulnerability/october-2026-security-releases
- **PHP** 8.5.12, 8.4.27, 8.3.36, 8.2.35. Dua celah kritis remote code execution tanpa autentikasi
  (CVE-2026-109618, CVE-2026-109619, CVSS 9.2). PHP 8.1 ke bawah tidak lagi menerima update.
  PHP 8.2 end-of-life 31 Desember 2026. Sumber: https://www.php.net/

`package.json` (`engines`) dan `composer.json` (`require.php`) sudah mengunci versi minimal ini. Test di bawah juga
**gagal** jika dijalankan dengan runtime yang belum di-patch.

## Menjalankan test

```bash
# TypeScript (Node yang sudah di-patch)
docker run --rm -v "$PWD/clients:/c" -w /c node:24.22.1-alpine \
  node --experimental-strip-types --no-warnings typescript/test.ts

# PHP (PHP yang sudah di-patch)
docker run --rm -v "$PWD/clients:/c" -w /c php:8.4.27-cli php php/test.php
```

Tambahkan uji online ke engine dengan env `PQE_URL`, `PQE_API_KEY` (role `app`), dan `PQE_SAMPLE` (path foto),
plus `--network host` jika engine berjalan di mesin yang sama.

Saat ada security release baru, perbarui angka versi di file ini, `package.json`, `composer.json`,
`.nvmrc`, `test.ts`, `test.php`, dan CI (`.github/workflows/ci.yml`, `.gitlab-ci.yml`).
