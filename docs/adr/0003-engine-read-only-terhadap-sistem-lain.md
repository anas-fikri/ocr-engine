# ADR 0003: Engine read-only terhadap sistem lain

- **Status:** Diterima (22 Okt 2026). Sebagian digantikan [ADR 0006](0006-engine-hanya-memproses-gambar.md) (24 Okt 2026): engine kini tidak menulis ke bucket mana pun.
- **Terkait:** [sdlc-plan.md](../sdlc-plan.md) 1.4 prinsip 6, 3.4

## Konteks

Aplikasi kurir dan backend-nya belum bisa diubah dalam waktu dekat. Foto original adalah bukti penagihan. Engine baru dan masih dikalibrasi, sehingga kesalahan (bug, threshold keliru) tidak boleh merusak data operasional.

## Keputusan

- Engine **hanya membaca** bucket foto milik aplikasi (`AWS_BUCKET`) dan database aplikasi (source `db_poll`, sebaiknya replica read-only).
- Engine **menulis hanya** ke database miliknya. ~~Dan ke bucket miliknya (`photos-processed`, `photos-thumb`, `photos-upload`).~~ Sejak [ADR 0006](0006-engine-hanya-memproses-gambar.md) engine tidak menulis ke SILO sama sekali; foto hasil optimasi dikembalikan ke aplikasi, dan aplikasi yang menyimpannya.
- Engine tidak pernah menimpa/menghapus foto original. Retensi original diatur lifecycle SILO oleh tim infra, bukan oleh engine.
- Service account engine di SILO diberi policy minimum (lihat [operations.md](../operations.md#42-service-account-engine-least-privilege)).

## Konsekuensi

- (+) Bug engine tidak bisa merusak bukti penagihan atau data aplikasi.
- (+) Engine bisa dipasang/dicabut/di-rollback kapan saja tanpa koordinasi perubahan aplikasi.
- (+) Integrasi Level 0 ("tanpa ubah aplikasi") dimungkinkan.
- (−) ~~Ada duplikasi penyimpanan kecil (versi kompres + thumbnail di bucket engine).~~ Tidak berlaku lagi sejak ADR 0006: tidak ada salinan di SILO milik engine.
- (−) Penghematan storage dengan mengganti original (`replace_original`) sengaja **tidak** diimplementasikan. Jika kelak dibutuhkan, harus lewat persetujuan dan ADR baru.

## Alternatif yang ditolak

- **Engine mengganti original dengan versi kompres:** hemat storage, tapi menghilangkan bukti terkuat dan membuat kesalahan kalibrasi tidak bisa dipulihkan.
- **Engine menulis status ke database aplikasi:** mengikat engine ke skema aplikasi tertentu dan melanggar prinsip integrasi yang fleksibel. Penggantinya: webhook dan API.
