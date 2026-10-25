# ADR 0007: Engine mengarsipkan foto di bucket `ocr`, aplikasi tetap menyimpan sendiri

- **Status:** Diterima (24 Okt 2026). Memperbarui [ADR 0006](0006-engine-hanya-memproses-gambar.md): engine kembali
  menulis ke SILO, tetapi **hanya** ke bucket arsipnya sendiri.
- **Terkait:** [configuration.md](../configuration.md), [operations.md](../operations.md)

## Konteks

Setelah ADR 0006 (engine tidak menyimpan apa pun), uji coba menunjukkan dua masalah:

- Foto yang ditolak tidak ada di mana pun, sehingga ops tidak bisa menilai apakah penolakan benar. Kalibrasi
  ("salah tolak < 5%") butuh foto itu.
- Web UI, evidence, dan backup bergantung pada aplikasi melaporkan lokasi foto (`object_key`). Halaman Uji Coba Kamera
  tidak menyimpan foto sama sekali.

## Keputusan

- Engine menyimpan **arsipnya sendiri** di satu bucket (`ENGINE_BUCKET_ARCHIVE`, bawaan `ocr`), dipisah per aplikasi
  lewat prefix: `<tenant>/<yyyy>/<mm>/<dd>/<id-foto>.<ext>`. Tenant bisa memakai bucket lain (`archive_bucket`).
- Isi arsip: foto **diterima** = foto yang sama dengan `output_image`; foto **ditolak** = salinan review untuk review
  dan kalibrasi: file asli bila ≤ 1,5 MB, selain itu JPEG kualitas tinggi (quality 82-92, sisi panjang s.d. 3000 px,
  EXIF tetap, tanpa koreksi sehingga buram/gelapnya tetap terlihat) maks. `archive_rejected_max_bytes` (bawaan
  1,5 MB; `0` = selalu file asli). Diperbarui 24 Okt 2026 atas permintaan pemilik produk supaya storage terkendali
  (foto kamera ditolak 3-7 MB → ±1,3 MB). Hash, EXIF, dan skor di catatan analisis tetap milik foto asli.
  Foto polling / `object_key` (sudah ada di SILO aplikasi) tidak diarsipkan ulang.
- Masa simpan arsip **520 hari** (`archive_retention_days` per tenant). Setelah itu worker menghapus file arsipnya;
  catatan analisis tetap sampai `retention_days`.
- Arsip ditulis **di belakang layar** setelah keputusan dikirim ke aplikasi, dengan beberapa kali coba ulang. Kurir tidak
  pernah menunggu SILO.
- Aplikasi **tetap** menerima `output_image` dan menyimpan foto pengirimannya ke bucket dan jalurnya sendiri. Engine
  tetap tidak bisa menulis/menghapus di bucket aplikasi (dijaga di kode).
- Web UI, evidence, dan backup NAS memakai arsip engine; lokasi dari aplikasi (`object_key`, `/stored`) menjadi
  pelengkap.

## Konsekuensi

- (+) Ops bisa melihat dan menilai foto yang ditolak; kalibrasi bisa dilakukan.
- (+) Evidence dan Web UI tidak bergantung pada aplikasi; arsip engine + hash menjadi bukti yang tidak bisa diubah
  aplikasi.
- (+) Uji Coba Kamera menyimpan foto, jadi alur bisa dicoba utuh tanpa aplikasi.
- (−) Foto diterima tersimpan dua kali (bucket aplikasi + `ocr`), ±1 MB per foto. Contoh 1.000 foto/hari ≈ 30 GB/bulan,
  dibatasi 520 hari.
- (−) Key engine butuh izin tulis/hapus di bucket `ocr` (tetap hanya baca di bucket aplikasi).
- Foto ditolak yang kemudian disetujui ops memakai foto asli dari arsip sebagai evidence.
