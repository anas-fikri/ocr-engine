# Memulai dalam 10 Menit

Panduan ini menjalankan engine lengkap di PC (engine + SILO lokal + PostgreSQL), mengisi contoh foto, lalu
mencoba Web UI dan API. Tidak butuh akses ke SILO kantor.

## Prasyarat

| Kebutuhan | Versi | Cek |
|---|---|---|
| Docker + Docker Compose | Docker 24+, Compose v2 | `docker compose version` |
| RAM kosong | ± 2 GB | |
| Port bebas | 8000 (engine), 9000-9001 (SILO), 5432 tidak dipakai dari luar | |

Tanpa Docker? Lihat [Menjalankan tanpa Docker](#menjalankan-tanpa-docker) di bawah.

## 1. Jalankan

```bash
git clone https://github.com/anas-fikri/ocr-engine.git
cd ocr-engine
cp .env.example .env
docker compose up -d --build
docker compose ps
```

`cp .env.example .env` opsional untuk mode lokal (nilai bawaan sudah jalan). Build pertama butuh beberapa menit
(mengunduh image dan Tesseract OCR). Setelah itu `docker compose ps` harus menunjukkan engine `healthy` (±30 detik).

> **Mac (zsh):** paste perintah tanpa komentar `# ...` di belakangnya, dan jangan paste teks dalam kurung. zsh di Mac
> tidak menganggap `#` sebagai komentar saat di-paste (error `no such service: #`) dan membaca `( ... )` sebagai pola
> file (`unknown file attribute`). Alternatif: jalankan `setopt interactivecomments` sekali. Docker Desktop harus
> sedang berjalan. Apple Silicon (M1/M2/M3) didukung: semua image tersedia untuk `arm64`.

| Layanan | URL | Login |
|---|---|---|
| Web UI | http://localhost:8000/ui | API key: `pqe_local_admin_change_me` (`PQE_BOOTSTRAP_ADMIN_KEY`) |
| Swagger API | http://localhost:8000/docs | Klik **Authorize**, isi API key yang sama |
| SILO console | http://localhost:9001 | `siloadmin` / `siloadmin-local-only` |

> Nilai di atas **hanya untuk lokal**. Di server, ganti semua lewat `.env` (lihat [operations.md](operations.md)).

## 2. Aktifkan notifikasi real-time SILO (opsional)

Tanpa langkah ini engine tetap menemukan foto baru lewat polling (setiap 15 detik). Dengan langkah ini foto
diproses begitu selesai diupload:

```bash
./scripts/silo-enable-events.sh
```

## 3. Isi contoh foto

```bash
docker compose exec engine pqe demo-upload
```

Perintah ini meng-upload 12 foto sintetis (tajam, buram, pecah, gelap, terlalu terang, kecil, kompresi berat, dll.)
ke bucket `ekas-pcptransport` lengkap dengan metadata resi/kurir. Dalam beberapa detik engine menganalisis semuanya.

Compose lokal memakai `PQE_ENV=development`, jadi perintah ini diizinkan. Di server (`PQE_ENV=production`, bawaan
`.env.example`) perintah ini **ditolak** agar foto sintetis tidak pernah masuk bucket produksi. Engine juga tidak pernah
menulis atau menghapus di bucket aplikasi dalam operasi normal; `demo-upload` adalah pengecualian eksplisit untuk uji lokal.

## 4. Lihat hasilnya

1. Buka http://localhost:8000/ui dan masuk dengan API key admin. Menu ada di sidebar kiri (di HP: tombol ☰), versi
   engine tampil di sidebar dan pojok kanan atas. Setiap halaman langsung menaruh kursor di field utamanya.
2. **Dashboard**: ringkasan PASS/WARN/REJECT, alasan terbanyak, kurir/cabang bermasalah.
3. **Review**: antrean foto WARN/REJECT (opsional: foto WARN sudah otomatis masuk evidence). Klik satu foto untuk
   melihat skor, alasan, saran, EXIF, ukuran sebelum dan sesudah kompres. Foto yang kualitasnya tidak memenuhi syarat
   (buram berat, pecah, terlalu gelap) bertanda **Wajib foto ulang**; engine tidak memperbaiki/memanipulasi gambar,
   hanya mengoptimasi ukuran. Coba juga tombol **Setujui** / **Minta foto ulang**.
4. **Uji foto**: upload foto dari HP Anda sendiri untuk melihat penilaian engine tanpa menyimpan apa pun.
5. **Koneksi SILO** (admin): koneksi ke SILO (baca bucket aplikasi, tulis arsip engine di bucket `ocr`).
   Di compose lokal sudah terisi dari env; untuk mencoba SILO lain, tempel JSON `mc` lalu **Test koneksi** (jangan
   pakai `127.0.0.1` dari dalam Docker, lihat [configuration.md §1.6](configuration.md#16-koneksi-silo-lewat-web-ui)).
6. **Backup NAS** (admin): di compose lokal tujuan folder `/backup` = folder `./backup` di repo. Klik **Test NAS**, centang
   **Aktifkan backup terjadwal**, **Simpan**, lalu **Backup database sekarang**; file muncul di `./backup` dalam ±10 detik.
   Di server, mount share NAS lalu isi `NAS_MOUNT` ([operations.md 6.3](operations.md#63-mount-nas-untuk-tujuan-folder)).
7. **Panduan Integrasi ↗**: http://localhost:8000/ui/guide.html, panduan untuk programmer aplikasi (tanpa login),
   lengkap dengan diagram alur integrasi, alur penggunaan, keputusan di aplikasi kurir, dan proses di dalam engine.

Panduan Web UI lengkap: [web-ui-guide.md](web-ui-guide.md).

## 4b. Uji coba dari HP tanpa membangun aplikasi

Halaman **Uji Coba Kamera** (`/ui/coba.html`) berperan sebagai aplikasi kurir: ambil foto dengan kamera HP, kirim ke
engine, lalu lihat pesan yang akan tampil di layar kurir beserta detail analisisnya.

1. Cari IP PC yang menjalankan engine di jaringan kantor (`ip addr` di Linux, `ipconfig` di Windows), misal `192.168.1.10`.
   HP harus di jaringan (Wi-Fi) yang sama, dan port 8000 tidak diblokir firewall PC.
2. Di HP buka `http://192.168.1.10:8000/ui/coba.html`, masuk dengan API key admin (`pqe_local_admin_change_me` di lokal).
3. Pilih mode:
   - **Simulasi aplikasi kurir** (bawaan): sama dengan integrasi aplikasi (`POST /v1/analyze?timeout_ms=3000` dengan
     file): engine memeriksa dan mengoptimasi foto, lalu mengembalikan keputusan + foto hasil (`output_image`). Di
     aplikasi asli, aplikasi yang menyimpan foto itu ke bucket-nya. Engine mengarsipkan foto di bucket `ocr`
     (`ocr/uji-coba/...`). Hasil analisis masuk tenant **`uji-coba`** (dibuat otomatis dari
     `config/tenants/uji-coba.yaml`, mode `enforce`, retensi 30 hari), jadi tidak tercampur dengan data aplikasi kurir,
     dan tampil di Web UI lengkap dengan fotonya.
   - **Uji cepat**: hanya dianalisis, tidak disimpan.
4. Tekan **📷 Ambil foto** (membuka kamera bawaan HP, EXIF ikut terkirim) atau **Pilih dari galeri**.
5. Hasil: tampilan **layar kurir** (pesan `courier_message`, tombol Foto ulang / Tetap kirim / Lanjut, dan baris
   **Status pengiriman: diupdate / TIDAK diupdate** sesuai `allow_status_update`, plus apakah aplikasi akan menyimpan
   foto), **Foto untuk disimpan aplikasi** (ukuran, dioptimasi atau asli; "tidak ada" jika ditolak), waktu tunggu kurir, perbandingan foto
   asli vs hasil engine (hanya dioptimasi/dikompres), skor, alasan, kompresi, dan EXIF. **Tampilkan sebagai mode** memperlihatkan pengalaman kurir di `shadow`/`warn`/`enforce` tanpa
   mengubah config. Tombol **Buka di Web UI** langsung membuka detail foto tersebut.

Tips menguji: foto resi yang jelas, lalu foto sambil bergerak (buram), di tempat gelap (tidak diperbaiki: wajib foto
ulang), zoom digital berlebihan (pecah), dan foto layar/screenshot. Di tenant `uji-coba` (mode `enforce`) foto `REJECT`
memperlihatkan status pengiriman yang ditahan.

**Kamera langsung di browser** (viewfinder di halaman, tanpa membuka aplikasi kamera) butuh HTTPS. Jalankan proxy
HTTPS bawaan:

```bash
PQE_HTTPS_HOST=192.168.1.10 docker compose -f docker-compose.yml -f docker-compose.https.yml up -d
# lalu buka di HP: https://192.168.1.10:8443/ui/coba.html
```

Sertifikatnya dibuat sendiri oleh Caddy, jadi browser HP menampilkan peringatan sekali: pilih **Lanjutkan /
Advanced → Proceed**. Ini hanya untuk uji coba; produksi memakai ingress dengan sertifikat resmi.

## 5. Coba API

```bash
export PQE_URL=http://localhost:8000
export ADMIN_KEY=pqe_local_admin_change_me

# buat API key untuk aplikasi (role app, terikat tenant courier-app)
curl -s -X POST $PQE_URL/v1/admin/api-keys -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"name":"percobaan","role":"app","tenant":"courier-app"}'
export PQE_API_KEY=pqe_...   # salin nilai api_key dari respons di atas

# kirim foto dan tunggu hasilnya (maks. 3 detik)
curl -s -X POST "$PQE_URL/v1/analyze?sync=true" -H "X-API-Key: $PQE_API_KEY" \
  -F "file=@/path/ke/foto.jpg" -F "awb=AWB0001" -F "photo_type=pod_recipient"

# cari berdasarkan resi
curl -s -H "X-API-Key: $PQE_API_KEY" "$PQE_URL/v1/photos?awb=AWB0001"

# PDF bukti pengiriman (hanya foto layak: PASS + WARN bawaan, atau yang sudah di-approve)
curl -s -H "X-API-Key: $PQE_API_KEY" -o bukti.pdf "$PQE_URL/v1/evidence/AWB0001/pdf"
```

Langkah berikutnya untuk programmer aplikasi: [integration-guide.md](integration-guide.md).

## 6. Analisis file dari command line (tanpa server)

Berguna untuk mencoba atau mengkalibrasi threshold dengan kumpulan foto:

```bash
docker compose exec engine pqe synth-samples /tmp/sampel          # buat sampel sintetis
docker compose exec engine pqe analyze /tmp/sampel/*.jpg            # ringkasan per foto
docker compose exec engine pqe analyze --profile pod_document --json /tmp/sampel/pass_huge_camera.jpg
```

Contoh keluaran:

```
/tmp/s/pass_huge_camera.jpg  [profile pod_document]
  verdict : PASS
  skor    : sharpness=100, resolution=100, exposure=100, ocr=95.9, authenticity=100
  - [info] EXIF_MISSING: Metadata kamera tidak tersedia
  ukuran  : 3822 KB -> 502 KB (quality 90, SSIM 0.9974)
  waktu   : 3424 ms
```

Untuk foto di PC Anda: `docker compose cp ./folder-foto engine:/tmp/foto` lalu `pqe analyze /tmp/foto/*`.

## 7. Menghubungkan ke SILO kantor

```bash
cp .env.example .env
# isi AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (service account khusus engine dari tim infra),
# AWS_BUCKET, PQE_BOOTSTRAP_ADMIN_KEY (nilai acak panjang), PUBLIC_BASE_URL
docker compose -f docker-compose.remote-silo.yml up -d --build
```

Engine **membaca** bucket aplikasi dan menyimpan arsipnya sendiri di bucket `ocr`
([ADR 0007](adr/0007-arsip-engine-di-bucket-ocr.md)); minta tim infra membuat bucket `ocr`. Key engine: baca di bucket
aplikasi, baca/tulis/hapus di `ocr`. Detail permission dan setup SILO produksi: [operations.md](operations.md).

## Menjalankan tanpa Docker

Untuk pengembangan engine (Python 3.11+, disarankan 3.12):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock -e ".[dev]"

# S3 tiruan lokal (pengganti SILO) di terminal terpisah
moto_server -H 127.0.0.1 -p 5055

export DATABASE_URL=sqlite:///./pqe.db AWS_ENDPOINT=http://127.0.0.1:5055
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test ENGINE_CREATE_BUCKETS=true
export PQE_BOOTSTRAP_ADMIN_KEY=pqe_local_admin_change_me PQE_ENV=development
pqe run                         # http://localhost:8000/ui
pqe demo-upload                 # di terminal lain, dengan env yang sama
```

OCR (pemeriksaan teks resi) butuh Tesseract: `sudo apt install tesseract-ocr tesseract-ocr-ind` lalu
`pip install -e ".[ocr]"`. Tanpa Tesseract, check OCR dilewati (`OCR_UNAVAILABLE`) dan check lain tetap jalan.

Detail pengembangan & test: [development.md](development.md).

## Menghentikan & membersihkan

```bash
docker compose down             # data tetap (volume)
docker compose down -v          # HAPUS semua data lokal (DB + foto SILO lokal)
```
