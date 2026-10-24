# ADR 0006: Engine hanya memproses gambar, penyimpanan dikerjakan aplikasi

- **Status:** Diterima (24 Okt 2026). Menggantikan bagian "engine menulis ke bucket miliknya" di [ADR 0003](0003-engine-read-only-terhadap-sistem-lain.md).
- **Terkait:** [integration-guide.md](../integration-guide.md#0-cara-tercepat-kirim-foto-terima-keputusan), [api-reference.md](../api-reference.md#analisis)

## Konteks

Versi awal memakai alur "scan dulu, baru simpan": aplikasi mengirim file ke engine, lalu engine yang menyimpan foto
yang diterima ke SILO (`photos-processed`, `photos-upload`, `photos-thumb`) dan mengembalikan link `stored`.

Saat persiapan UAT, pemilik produk memutuskan alur itu terlalu rumit: ada bucket tambahan, izin tulis untuk engine,
dua tempat penyimpanan foto, dan tanggung jawab penyimpanan yang terbagi. Engine cukup memproses gambar. Sebelum dan
sesudahnya, termasuk penyimpanan ke SILO dan status pengiriman, adalah urusan aplikasi pengiriman.

## Keputusan

- Engine **tidak pernah menulis ke SILO**. Tidak ada bucket engine untuk instalasi baru; service account engine cukup
  izin baca (`s3:GetObject`, `s3:ListBucket`) di bucket aplikasi.
- `POST /v1/analyze` dengan file: engine menganalisis di request itu juga (maks. `timeout_ms`) dan mengembalikan
  keputusan + `output_image` (foto hasil optimasi, base64, < 1 MB). Foto ditolak: `output_image: null`.
- Aplikasi menyimpan `output_image` ke SILO dengan kode dan key miliknya sendiri. Engine mati atau lambat: aplikasi
  menyimpan foto asli (fail-open, [ADR 0005](0005-fail-open.md)).
- Engine tetap mencatat **metadata** hasil analisis (verdict, skor, hash, resi), bukan fotonya, untuk Dashboard,
  Review, deteksi foto dipakai ulang, dan Evidence. Supaya Web UI, Evidence, dan Backup NAS bisa menampilkan foto,
  aplikasi memberi tahu key SILO-nya (`object_key` di request, atau `POST /v1/photos/{id}/stored`). Engine hanya
  membacanya.
- Foto yang tidak disimpan aplikasi (ditolak, atau lokasinya tidak dilaporkan) tidak punya file di Web UI, tidak masuk
  Evidence, tidak ikut Backup NAS, dan tidak bisa dianalisis ulang.

## Konsekuensi

- (+) Satu tempat penyimpanan foto (bucket aplikasi), satu pemilik. Lebih mudah dipahami tim aplikasi dan infra.
- (+) Engine tidak butuh izin tulis sama sekali; bug engine tidak bisa mengubah isi SILO.
- (+) Foto yang ditolak benar-benar tidak tersimpan di mana pun.
- (−) Respons `/v1/analyze` lebih besar (foto base64, ±33% lebih besar dari file biner, maks. ±1,4 MB).
- (−) Foto asli kurir tidak lagi diarsipkan engine. Jika foto asli perlu disimpan (selain versi optimasi), itu
  keputusan aplikasi.
- (−) Thumbnail dibuat saat diminta (Web UI), bukan disimpan.
- Data versi sebelumnya di bucket engine tetap bisa dibuka dan dibackup (opsi "data versi lama" di Backup NAS).

## Alternatif yang ditolak

- **Engine tetap menyimpan ke SILO (alur lama):** lebih sedikit kode di aplikasi, tetapi tanggung jawab penyimpanan
  terbagi dan butuh bucket + izin tulis tambahan.
- **Mengembalikan foto sebagai respons biner dengan verdict di header HTTP:** lebih hemat, tetapi lebih sulit dibaca
  aplikasi PHP/TS dan tidak muat untuk pesan dan alasan yang panjang.
- **Tanpa catatan metadata sama sekali (engine stateless):** menghilangkan Dashboard, Review, deteksi duplikat, dan
  Evidence.
