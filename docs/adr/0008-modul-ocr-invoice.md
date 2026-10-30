# ADR 0008: Engine diperluas dengan modul OCR dokumen (plugin pertama: invoice vendor)

- **Status:** Diterima (30 Okt 2026, keputusan pemilik produk). Diperbarui [ADR 0009](0009-modul-dokumen-generik-multi-client.md):
  modul generik untuk semua aplikasi client, jenis dokumen sebagai plugin.
- **Terkait:** [document-ocr-design.md](../document-ocr-design.md), [ADR 0001](0001-queue-di-postgres.md),
  [ADR 0004](0004-rule-based-sebelum-ml.md), kebutuhan FR-OCR-001..013

## Konteks

Aplikasi internal butuh OCR invoice vendor (nomor, tanggal, NPWP, DPP, PPN, total, item) lewat API async
(`POST /v1/jobs`, `GET /v1/jobs/{id}`, `POST /v1/templates`) dengan API key + HMAC. Photo Quality Engine sudah punya
Tesseract (ind + eng), antrean di PostgreSQL, API key per tenant, metrik, backup, dan deploy Docker di server
10.3.3.243. Membangun server OCR kedua berarti menggandakan semua itu.

## Keputusan

1. **Satu engine, dua fungsi.** Modul dokumen ditambahkan ke engine yang sama sebagai paket `engine/documents/` dengan
   router, tabel, antrean, dan thread worker **sendiri**. Tabel dan perilaku foto POD tidak diubah.
2. **Namespace = tenant API key.** Setiap aplikasi client memakai key role `app` untuk tenant-nya sendiri; semua job,
   file, dan template difilter tenant itu.
3. **HMAC per key.** Key yang dipakai modul dokumen punya `api_secret` (tabel `api_key_secrets`, terenkripsi dengan
   `PQE_SECRET_KEY`). Endpoint foto tetap memakai API key saja (tidak berubah).
4. **File inbox di PostgreSQL** (`document_files`), bukan di SILO: atomik dengan job (alasan yang sama dengan ADR
   0001), dokumen tetap bisa diterima walau SILO bermasalah, ikut backup database, dan volumenya kecil. File dihapus
   setelah `PQE_DOCS_INBOX_RETENTION_DAYS` (bawaan 30). Jika volume naik jauh (> ±5 GB file aktif), pindahkan ke
   bucket `ocr` dengan prefix `documents/` (abstraksi ada di `engine/documents/store.py`).
5. **Dokumen diproses di proses anak** (sandbox) dengan batas memori/CPU/waktu, environment tanpa rahasia, dan
   prioritas CPU rendah. Satu dependency baru: `pypdfium2` (PDFium, BSD-3/Apache-2.0) untuk teks + render PDF.
6. **Tetap rule-based.** Layer 1 teks PDF, layer 2 QR e-Faktur (mati secara bawaan), layer 3 Tesseract, layer 4
   template per issuer yang dipelajari dari koreksi pengguna (anchor + jenis pola tetap + posisi). Tanpa AI generatif
   dan tanpa model ML selain Tesseract.

## Konsekuensi

- (+) Satu server, satu deploy, satu backup, satu dashboard metrik untuk dua fungsi.
- (+) Bom PDF/gambar hanya mematikan proses anak; worker foto dan API tetap hidup.
- (−) OCR dokumen memakan CPU server yang sama dengan analisis sinkron foto kurir. Dimitigasi: bawaan 1 thread
  dokumen, proses anak `nice 10`, bisa dimatikan total (`PQE_DOCS_ENABLED=false`) atau dijalankan di container
  terpisah (`pqe document-worker`).
- (−) Database bertambah besar oleh file inbox selama masa retensi (contoh 100 dokumen/hari × 1 MB × 30 hari ≈ 3 GB).
- (−) Ganti `PQE_SECRET_KEY` = semua `api_secret` harus dibuat ulang (sama dengan rahasia Web UI lainnya).

## Alternatif yang ditolak

- **Server OCR terpisah:** menggandakan auth, antrean, deploy, monitoring, backup untuk volume kecil.
- **File inbox di SILO:** menambah ketergantungan saat menerima dokumen dan tidak atomik dengan job; tetap menjadi
  opsi saat volume besar.
- **Model layout ML (LayoutLM, Donut) / LLM:** dilarang FR-OCR-013, butuh GPU/dataset, sulit dijelaskan.
- **Regex dari klien di template:** risiko ReDoS dan injeksi; diganti jenis pola tetap + `shape`.
