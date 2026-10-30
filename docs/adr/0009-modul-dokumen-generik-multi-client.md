# ADR 0009: Modul OCR dokumen generik untuk semua aplikasi client; jenis dokumen sebagai plugin

- **Status:** Diterima (30 Okt 2026, arahan pemilik produk). Memperbarui [ADR 0008](0008-modul-ocr-invoice.md).
- **Terkait:** [document-ocr-design.md](../document-ocr-design.md), [api-reference.md](../api-reference.md#dokumen),
  [integration-guide.md §13](../integration-guide.md#13-integrasi-modul-dokumen)

## Konteks

ADR 0008 menambahkan OCR invoice ke engine untuk satu aplikasi client pertama, dengan kontrak API yang diusulkan
aplikasi itu. Pemilik produk memutuskan engine OCR harus **tetap terpisah** dari aplikasi mana pun dan **generik**:
aplikasi lain juga akan memakainya. Client pertama hanyalah salah satu client.

## Keputusan

1. **Tidak ada yang spesifik satu client** di kode, tabel, config, endpoint, nama field, atau dokumen API. Nama
   aplikasi client hanya boleh muncul sebagai contoh di panduan integrasi. Modul bernama `engine/documents/`, tabel
   `document_*`, env `PQE_DOCS_*`, metrik `pqe_document_*`.
2. **API umum per client.** Setiap client = satu tenant (namespace) dengan API key + `api_secret` sendiri, job, file,
   dan template sendiri, dan host callback sendiri (`callback_hosts` di config tenant, ditambah allowlist global
   `PQE_DOCS_CALLBACK_HOSTS`). Isolasi antar-client wajib dan dites (job, file, template, koreksi, idempotency, nonce,
   callback). Onboarding satu perintah: `pqe create-client --code <kode> --name "<nama>"`.
3. **Jenis dokumen = plugin** (FR-OCR-012). Core (API, antrean, sandbox, template, retensi, callback) tidak tahu isi
   jenis dokumen. Plugin mendeklarasikan `field_kinds` (field yang bisa dipelajari template + jenis nilainya),
   `detect/extract/validate`, identitas penerbit (`normalize_issuer`, `issuer_of`, `issuer_check`), dan pengayaan
   opsional (`wants_qr`, `enrich`). Mesin aturan generik (`engine/documents/rules.py`) menyediakan jenis nilai dan
   penerapan template. `invoice` (invoice vendor Indonesia: NPWP, DPP, PPN, PPh, total, item; QR e-Faktur) adalah
   plugin pertama, field set-nya generik, bukan mengikuti kebutuhan satu aplikasi. Plugin baru dimuat lewat entry point
   `pqe.document_extractors` atau `PQE_DOCUMENT_PLUGINS` tanpa mengubah core.
4. **Kontrak client pertama = masukan, bukan acuan mutlak.** Bagian yang terlalu spesifik dibuat generik:
   - penerbit template `issuer.id` (format ditentukan plugin; invoice: NPWP). `issuer.npwp` tetap diterima sebagai alias;
   - `idempotency_key` per koreksi = kunci opak pilihan client (16-128 karakter); rumus hash hanya rekomendasi di client
     contoh, engine tidak menghitung ulang;
   - jadwal polling, batas respons 1 MB, dan timeout menjadi rekomendasi umum di panduan integrasi.
   Perbedaan terhadap kontrak awal dicatat di laporan rilis; client pertama yang menyesuaikan adapter-nya.
5. **API berversi** (`/v1/...`): perubahan tidak kompatibel memakai `/v2`; field baru boleh ditambah (client wajib
   mengabaikan field yang tidak dikenal). Client contoh tanpa dependency di `clients/` (PHP `DocumentOcrClient.php`,
   TypeScript `document-client.ts`) dengan test yang memakai vektor tanda tangan yang sama dengan engine.
6. **Operasional tetap satu deployment** dengan fungsi foto POD (ADR 0008 tetap berlaku: sandbox, prioritas CPU rendah,
   bisa dipisah dengan `pqe document-worker`).

## Konsekuensi

- (+) Aplikasi baru bisa memakai OCR dokumen hanya dengan `pqe create-client` + panduan integrasi, tanpa mengubah
  engine dan tanpa konteks aplikasi lain.
- (+) Jenis dokumen baru (kwitansi, surat jalan, faktur pajak, dst.) = satu plugin; template, sandbox, auth, dan
  isolasi otomatis berlaku.
- (−) Client pertama perlu menyesuaikan adapter (mis. `issuer.id`, `template.issuer_id`, nama env & metrik baru).
  Karena belum pernah dideploy, tidak ada migrasi data.
- (−) Plugin pihak ketiga berjalan di proses worker (bukan sandbox); hanya pasang plugin yang sudah direview.

## Alternatif yang ditolak

- **Endpoint per jenis dokumen / per client** (`/v1/invoices`, `/v1/<client>/...`): menggandakan auth, antrean,
  dan template untuk setiap tambahan.
- **Satu skema field universal untuk semua dokumen:** field berbeda per jenis dokumen; plugin yang mendefinisikannya.
