# ADR 0004: Deteksi rule-based (logika klasik) sebelum machine learning

- **Status:** Diterima (22 Okt 2026)
- **Terkait:** [sdlc-plan.md](../sdlc-plan.md) 1.4 prinsip 5, 5.3

## Konteks

Belum ada sampel foto POD berlabel. Keputusan engine (terutama REJECT) berdampak ke kurir dan penagihan, jadi harus bisa **dijelaskan** ke ops, kurir, dan pelanggan. Pengguna meminta "gunakan logic jika memungkinkan".

## Keputusan

Fase awal memakai metode klasik yang deterministik dan bisa dijelaskan:

- Ketajaman: variance of Laplacian per tile (dengan normalisasi kontras).
- Resolusi/pecah: ukuran piksel, estimasi quality JPEG dari tabel kuantisasi, blocking 8×8, indikasi mosaic.
- Exposure: statistik histogram.
- Keaslian: EXIF, SHA-256 + perceptual hash untuk duplikat, konsistensi waktu/GPS.
- Keterbacaan teks: confidence OCR Tesseract (opsional).

Setiap check menghasilkan skor 0-100 + kode alasan + metrik mentah. Threshold ada di config per profile/tenant.

## Konsekuensi

- (+) Hasil bisa dijelaskan ("skor ketajaman 18 < batas 25") dan diatur tanpa melatih model.
- (+) Ringan (sekitar 1-3 detik CPU per foto 12 MP, tanpa GPU) dan deterministik (mudah dites).
- (−) Ada batas akurasi alami (misal foto bokeh atau dokumen dengan area polos besar). Dimitigasi dengan mode `shadow`/`warn`, review ops, dan sinyal yang lemah (mosaic) tidak bisa langsung REJECT.
- (−) Threshold bawaan dikalibrasi dari foto publik + sampel sintetis, wajib dikalibrasi ulang dengan foto POD asli.
- Label review ops (approve/retake/fraud) dikumpulkan sejak hari pertama. Itu menjadi dataset jika kelak ML diperlukan. Analyzer berbentuk plugin, jadi model ML bisa ditambahkan sebagai analyzer baru tanpa mengubah alur.

## Alternatif yang ditolak

- **Model ML/deep learning sejak awal:** butuh dataset berlabel yang belum ada, sulit dijelaskan, butuh sumber daya lebih (GPU), dan sulit dikalibrasi per jenis foto.
- **Layanan pihak ketiga (cloud vision API):** biaya per foto, data pribadi (wajah penerima) keluar dari infrastruktur perusahaan, dan bergantung pada jaringan eksternal.
