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
git clone <url-repo> ocr-engine && cd ocr-engine
cp .env.example .env            # opsional untuk mode lokal; nilai default sudah jalan
docker compose up -d --build
docker compose ps               # engine harus "healthy" dalam ±30 detik
```

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

## 4. Lihat hasilnya

1. Buka http://localhost:8000/ui dan masuk dengan API key admin.
2. **Dashboard**: ringkasan PASS/WARN/REJECT, alasan terbanyak, kurir/cabang bermasalah.
3. **Review**: antrean foto WARN/REJECT (opsional: foto WARN sudah otomatis masuk evidence). Klik satu foto untuk
   melihat skor, alasan, saran, EXIF, ukuran sebelum dan sesudah kompres. Foto gelap/terlalu terang yang berhasil diperbaiki
   bertanda **Diperbaiki otomatis**, foto buram berat bertanda **Wajib foto ulang**. Coba juga tombol **Setujui** /
   **Minta foto ulang**.
4. **Uji foto**: upload foto dari HP Anda sendiri untuk melihat penilaian engine tanpa menyimpan apa pun.
5. **Pengaturan SILO** (admin): koneksi ke SILO. Di compose lokal sudah terisi dari env; untuk mencoba SILO lain,
   tempel JSON `mc` lalu **Test koneksi** (jangan pakai `127.0.0.1` dari dalam Docker, lihat
   [configuration.md §1.6](configuration.md#16-pengaturan-silo-lewat-web-ui)).
6. **Panduan Integrasi ↗**: http://localhost:8000/ui/guide.html, panduan untuk programmer aplikasi (tanpa login).

Panduan Web UI lengkap: [web-ui-guide.md](web-ui-guide.md).

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

Bucket engine (`photos-processed`, `photos-thumb`, `photos-upload`) harus sudah dibuat tim infra. Engine hanya
**membaca** bucket aplikasi. Detail permission dan setup SILO produksi: [operations.md](operations.md).

## Menjalankan tanpa Docker

Untuk pengembangan engine (Python 3.11+, disarankan 3.12):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock -e ".[dev]"

# S3 tiruan lokal (pengganti SILO) di terminal terpisah
moto_server -H 127.0.0.1 -p 5055

export DATABASE_URL=sqlite:///./pqe.db AWS_ENDPOINT=http://127.0.0.1:5055
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test ENGINE_CREATE_BUCKETS=true
export PQE_BOOTSTRAP_ADMIN_KEY=pqe_local_admin_change_me
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
