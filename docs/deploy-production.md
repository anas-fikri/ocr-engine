# Deploy ke Production (live uji coba & UAT)

Panduan ini menaikkan engine ke server production untuk **live uji coba dan UAT**, dalam **mode aman**: tenant aplikasi
kurir berjalan di mode `shadow`, jadi engine membaca dan menilai foto asli tanpa mengganggu kurir maupun aplikasi.
Perkiraan waktu: 30–60 menit jika prasyarat sudah siap.

Setelah selesai, lanjut ke [Rencana UAT](uat-plan.md).

```mermaid
flowchart LR
    U[Aplikasi pengiriman / Ops / HP uji coba] -->|HTTPS 443| C[Caddy]
    C --> E[Engine]
    E --> P[(PostgreSQL)]
    E <-->|baca bucket aplikasi, tulis bucket engine| S[(SILO s3.pcpexpress.com)]
    E -->|backup foto + database| N[(NAS)]
```

Semua komponen di atas (Caddy, engine, PostgreSQL) berjalan di **satu server** lewat `docker-compose.prod.yml`.

---

## 1. Prasyarat

| # | Kebutuhan | Keterangan |
|---|---|---|
| 1 | Server Linux | Minimal 4 vCPU, 8 GB RAM, 50 GB disk. Menyala terus. Untuk ±1.000 foto/hari ini cukup |
| 2 | Docker 24+ dan Docker Compose v2 | `docker compose version` |
| 3 | Nama domain internal | Contoh `photo-quality.pcpexpress.com`, diarahkan ke IP server (DNS kantor atau `/etc/hosts`) |
| 4 | Port 443 (dan 80) terbuka | Dari jaringan aplikasi pengiriman, ops, dan HP uji coba ke server |
| 5 | Server bisa menjangkau SILO | `curl -I https://s3.pcpexpress.com` dari server |
| 6 | Key SILO | Disarankan service account khusus engine (baca bucket aplikasi, tulis bucket engine). Key admin tetap aman karena pagar pengaman di engine, tetapi sebaiknya tidak dipakai |
| 7 | Bucket engine di SILO | `photos-upload`, `photos-processed`, `photos-thumb` (langkah 2) |
| 8 | Share NAS | Folder SMB/NFS khusus backup + akun yang bisa menulis (langkah 3) |

## 2. Siapkan SILO (sekali, oleh tim infra)

Dengan `mcli` (client MinIO/SILO):

```bash
mcli alias set pcp https://s3.pcpexpress.com <ACCESS_KEY_ADMIN> <SECRET_KEY_ADMIN>
mcli mb pcp/photos-upload
mcli mb pcp/photos-processed
mcli mb pcp/photos-thumb
```

Buat service account engine dengan izin minimal (contoh policy ada di Web UI → **Pengaturan SILO**, dan
[operations.md bagian 4](operations.md#4-setup-silo-produksi-untuk-tim-infra)). Kalau memakai key yang sudah ada,
engine tetap **tidak bisa** menulis atau menghapus di bucket aplikasi (dijaga di kode).

## 3. Mount NAS di server

SMB (Synology/QNAP/Windows share):

```bash
sudo apt-get install -y cifs-utils
sudo mkdir -p /mnt/nas-pqe
sudo mount -t cifs //192.168.1.20/pqe-backup /mnt/nas-pqe -o username=<user-nas>,password=<password-nas>,uid=10001,gid=10001,file_mode=0660,dir_mode=0770
```

`uid=10001` adalah user di dalam container engine, supaya engine bisa menulis. Agar tetap ter-mount setelah server
restart, tambahkan baris yang sama ke `/etc/fstab` (pakai file credentials, jangan password di fstab). NFS dan NAS
S3-compatible juga didukung, lihat [operations.md bagian 6.3](operations.md).

## 4. Ambil kode

```bash
git clone https://github.com/anas-fikri/ocr-engine.git
cd ocr-engine
git checkout v0.1.0
```

Pakai tag versi (bukan cabang yang terus bergerak) supaya production bisa di-rollback dengan jelas.

## 5. Buat konfigurasi production

```bash
scripts/prod-setup.sh photo-quality.pcpexpress.com
```

Script ini membuat `.env` dengan semua secret acak (password database, API key admin, kunci enkripsi, token event),
`PQE_ENV=production`, domain HTTPS, dan izin file `600`. Aman dijalankan ulang: secret yang sudah ada tidak diganti.
**API key admin** tidak dicetak (supaya tidak ikut tersalin ke chat atau tiket). Lihat dengan
`grep ^PQE_BOOTSTRAP_ADMIN_KEY= .env` langsung di server, lalu simpan di password manager. Jangan tempel ke chat.

Lalu edit `.env`:

```bash
nano .env
```

- `NAS_MOUNT=/mnt/nas-pqe` (folder dari langkah 3).
- Key SILO **boleh dikosongkan** dan diisi nanti lewat Web UI (langkah 7), atau isi `AWS_ACCESS_KEY_ID` dan
  `AWS_SECRET_ACCESS_KEY` di sini.
- Sertifikat: bawaan `PQE_TLS=internal` (CA internal, untuk intranet). Jika domain publik dan port 80/443 terbuka dari
  internet, jalankan `scripts/prod-setup.sh <domain> admin@pcpexpress.com` untuk sertifikat Let's Encrypt otomatis.

## 6. Jalankan

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

Ketiga layanan (`postgres`, `engine`, `https`) harus `Up`, engine `healthy`. Build pertama butuh beberapa menit.

## 7. Konfigurasi lewat Web UI

Buka `https://photo-quality.pcpexpress.com/ui`, masuk dengan API key admin dari langkah 5.

1. **Pengaturan SILO**: endpoint `https://s3.pcpexpress.com`, access key, secret key, bucket. Klik **Test koneksi**
   (semua ✅), lalu **Simpan**.
2. **Backup NAS**: tujuan Folder `/backup`, klik **Test NAS** (semua ✅), centang **Aktifkan backup terjadwal**,
   **Simpan**, lalu **Backup database sekarang**.
3. **Tenant** → `courier-app`: sesuaikan `bucket`/`prefix` dengan folder foto POD di SILO. Biarkan `mode: shadow`.
   Prefix yang tepat membuat polling ringan; tanpa prefix engine memeriksa seluruh bucket.
4. **API key**: buat key per orang/aplikasi (role `ops`, `viewer`, `app`). Jangan bagikan key admin.

## 8. Preflight: harus tanpa ❌

```bash
docker compose -f docker-compose.prod.yml exec engine pqe preflight
```

atau Web UI → **Sistem** → **Jalankan preflight**. Preflight memeriksa secret, database, koneksi dan izin SILO,
pagar pengaman bucket aplikasi, mode tenant, backup NAS, worker, dan OCR. Hasil akhir harus **"SIAP production"**.
Peringatan (⚠️) boleh untuk uji coba, tetapi baca saran perbaikannya.

## 9. Sertifikat internal di perangkat uji

Dengan `PQE_TLS=internal`, browser dan HP akan memberi peringatan sampai root CA Caddy dipasang:

```bash
docker compose -f docker-compose.prod.yml cp https:/data/caddy/pki/authorities/local/root.crt ./pqe-root.crt
```

Pasang `pqe-root.crt` sebagai sertifikat tepercaya di PC ops (Windows/macOS), HP uji coba (Android: Settings →
Security → Install certificate; iOS: buka file, lalu aktifkan di Settings → General → About → Certificate Trust
Settings), dan server aplikasi pengiriman yang memanggil engine. Alternatif: pakai sertifikat resmi perusahaan atau
Let's Encrypt.

## 10. Uji coba langsung

- HP uji coba: `https://photo-quality.pcpexpress.com/ui/coba.html` (kamera langsung berfungsi karena sudah HTTPS).
  Foto masuk tenant `uji-coba`, tidak tercampur data kurir.
- Developer aplikasi: `https://photo-quality.pcpexpress.com/ui/guide.html`.
- Ops: Web UI → Dashboard / Review untuk foto asli yang dinilai di mode shadow.

Lanjutkan dengan [Rencana UAT](uat-plan.md).

## 11. Operasional

| Kebutuhan | Perintah |
|---|---|
| Lihat log | `docker compose -f docker-compose.prod.yml logs -f engine` |
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Update ke versi baru | `git fetch --tags && git checkout v0.1.1 && docker compose -f docker-compose.prod.yml up -d --build` |
| Rollback | `git checkout v0.1.0 && docker compose -f docker-compose.prod.yml up -d --build` |
| Restart | `docker compose -f docker-compose.prod.yml restart engine` |
| Restore dari NAS | [operations.md bagian 6.4](operations.md) |

Sebelum update, pastikan backup database terakhir berhasil (Web UI → Backup NAS). Saat start, engine membuat tabel
baru yang belum ada; versi 0.1.x tidak mengubah tabel lama, jadi rollback cukup dengan kembali ke tag sebelumnya.
Jika suatu rilis mengubah skema, catatan rilisnya akan menyebutkan langkah tambahan.

## 12. Checklist keamanan production

- `.env` hanya bisa dibaca root/admin server (`chmod 600`, sudah diatur script).
- Key SILO admin yang pernah dibagikan di chat/email sudah diganti (rotate).
- Key admin engine hanya dipegang 1–2 orang; tim lain memakai key `ops`/`viewer`/`app`.
- Port 8000 hanya terbuka di `127.0.0.1` server (sudah diatur compose); akses dari luar hanya lewat HTTPS.
- Backup NAS aktif dan uji restore dilakukan minimal sebulan sekali.
- Tenant `uji-coba` dinonaktifkan setelah UAT selesai (Web UI → Tenant).
