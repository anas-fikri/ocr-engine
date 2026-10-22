# ADR 0003: Engine read-only terhadap sistem lain

- **Status:** Diterima (22 Okt 2026)
- **Terkait:** [sdlc-plan.md](../sdlc-plan.md) 1.4 prinsip 6, 3.4

## Konteks

Aplikasi kurir dan backend-nya belum bisa diubah dalam waktu dekat. Foto original adalah bukti penagihan. Engine baru dan masih dikalibrasi, sehingga kesalahan (bug, threshold keliru) tidak boleh merusak data operasional.

## Keputusan

- Engine **hanya membaca** bucket foto milik aplikasi (`AWS_BUCKET`) dan database aplikasi (source `db_poll`, sebaiknya replica read-only).
- Engine **menulis hanya** ke bucket miliknya (`photos-processed`, `photos-thumb`, `photos-upload`) dan database miliknya.
- Engine tidak pernah menimpa/menghapus foto original. Retensi original diatur lifecycle SILO oleh tim infra, bukan oleh engine.
- Service account engine di SILO diberi policy minimum (lihat [operations.md](../operations.md#42-service-account-engine-least-privilege)).

## Konsekuensi

- (+) Bug engine tidak bisa merusak bukti penagihan atau data aplikasi.
- (+) Engine bisa dipasang/dicabut/di-rollback kapan saja tanpa koordinasi perubahan aplikasi.
- (+) Integrasi Level 0 ("tanpa ubah aplikasi") dimungkinkan.
- (−) Ada duplikasi penyimpanan kecil (versi kompres + thumbnail di bucket engine). Foto yang sudah < 1 MB tidak disalin (passthrough ke original).
- (−) Penghematan storage dengan mengganti original (`replace_original`) sengaja **tidak** diimplementasikan. Jika kelak dibutuhkan, harus lewat persetujuan dan ADR baru.

## Alternatif yang ditolak

- **Engine mengganti original dengan versi kompres:** hemat storage, tapi menghilangkan bukti terkuat dan membuat kesalahan kalibrasi tidak bisa dipulihkan.
- **Engine menulis status ke database aplikasi:** mengikat engine ke skema aplikasi tertentu dan melanggar prinsip integrasi yang fleksibel. Penggantinya: webhook dan API.
