# Catatan versi

Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/); nomor versi mengikuti
[Semantic Versioning](https://semver.org/lang/id/). Versi yang sedang berjalan tampil di Web UI (sidebar & login),
`GET /healthz`, `GET /v1/me`, header `X-PQE-Version` di setiap respons, dan `pqe --version`.

Rilis dibuat dengan tag git `vX.Y.Z`; server production memakai tag, bukan cabang
([deploy-production.md](docs/deploy-production.md#11-operasional)).

## [0.1.0] - belum dirilis (UAT)

Rilis pertama untuk live uji coba dan UAT.

### Engine
- Penilaian kualitas foto: ketajaman, resolusi/pecah, pencahayaan, kontras, EXIF, foto identik/mirip dipakai ulang, OCR resi.
- Verdict `PASS`/`WARN`/`REJECT` dengan alasan + saran ID/EN, mode `shadow`/`warn`/`enforce` per tenant & profile.
- Keputusan siap pakai: `allow_status_update`, `courier_message`, `retake_required`. Status pengiriman tetap milik aplikasi.
- `POST /v1/analyze` mengembalikan foto hasil optimasi (`output_image`, base64, < 1 MB) untuk disimpan aplikasi ke
  bucket-nya sendiri ([ADR 0006](docs/adr/0006-engine-hanya-memproses-gambar.md)).
- Arsip engine di bucket `ocr`, per aplikasi, masa simpan 520 hari ([ADR 0007](docs/adr/0007-arsip-engine-di-bucket-ocr.md)):
  foto diterima = foto hasil, foto ditolak = foto asli untuk review & kalibrasi. Ditulis di belakang layar; bucket
  aplikasi tetap hanya dibaca.
- Optimasi tanpa manipulasi: hanya diperkecil proporsional + kompresi dinamis dengan pengaman SSIM.
- Lokasi SILO foto di aplikasi bisa dilaporkan (`object_key` atau `POST /v1/photos/{id}/stored`), opsional.
- Evidence PDF per resi / per invoice dengan kode verifikasi keaslian.
- Backup NAS terjadwal (folder SMB/NFS atau S3-compatible) untuk database dan arsip foto.
- `pqe preflight` dan `GET /v1/admin/preflight` untuk cek kesiapan production.
- Fail-open: engine lambat/mati tidak pernah menahan kurir.

### Web UI
- Dashboard, Review, Cari foto, Evidence, Uji foto, Tenant, API key, Koneksi SILO, Backup NAS, Sistem.
- Navigasi samping, versi terlihat, setiap halaman langsung fokus ke field utamanya, filter cukup tekan Enter, nyaman di HP.
- Uji Coba Kamera (`/ui/coba.html`) dan Panduan Integrasi (`/ui/guide.html`) tanpa build step.

### Integrasi & deploy
- Client TypeScript dan PHP (`checkFile`, `checkObject`, `reportStored`), tidak pernah throw, fail-open.
- `docker-compose.prod.yml` (engine + PostgreSQL + HTTPS Caddy), `scripts/prod-setup.sh`, opsi PostgreSQL di host
  (`scripts/host-postgres-setup.sh`), manifest Kubernetes, metrik Prometheus.
