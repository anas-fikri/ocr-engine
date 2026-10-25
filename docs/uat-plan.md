# Rencana UAT (User Acceptance Test)

Dokumen ini dipakai setelah engine naik ke production ([deploy-production.md](deploy-production.md)). Tujuannya:
membuktikan engine bekerja dengan foto dan pengguna asli, **tanpa mengganggu kurir**, lalu memutuskan kapan mode
dinaikkan dari `shadow` ke `warn` dan `enforce`.

## 1. Ruang lingkup

| Termasuk | Tidak termasuk |
|---|---|
| Penilaian kualitas foto asli (buram, pecah, gelap, resolusi, duplikat, OCR) | Perubahan aplikasi kurir versi lama |
| Web UI: dashboard, review, cari foto, evidence, Koneksi SILO, Backup NAS, preflight | Penagihan otomatis ke pelanggan |
| Integrasi `checkFile` di aplikasi pengiriman versi baru (lingkungan pengembangan/pilot), termasuk aplikasi menyimpan foto hasil engine ke SILO | Deploy Kubernetes |
| Halaman Uji Coba Kamera dari HP | |
| Backup NAS dan uji restore | |

## 2. Peran

| Peran | Siapa | Tanggung jawab |
|---|---|---|
| PIC UAT | Anas | Jadwal, keputusan lulus/gagal, keputusan naik mode |
| IT / infra | Tim infra | Server, SILO, NAS, sertifikat, skenario I-* |
| Ops / SPV | 2–3 orang | Review foto, skenario O-*, label kalibrasi |
| Finance | 1–2 orang | Evidence penagihan, skenario F-* |
| Developer aplikasi | Tim developer | Integrasi `checkFile`, skenario D-* |
| Kurir pilot | 3–5 kurir, 1 cabang | Skenario K-* (setelah integrasi siap) |

Setiap peran memakai API key sendiri (dibuat admin di Web UI → API key): ops = `ops`, finance = `viewer`, aplikasi = `app`.

## 3. Jadwal

| Fase | Durasi | Mode tenant `courier-app` | Hasil |
|---|---|---|---|
| 0. Deploy & preflight | Hari 1 | `shadow` | Preflight "SIAP production", skenario I-1 s/d I-3 lulus |
| 1. Shadow + UAT fungsi | Minggu 1 | `shadow` | Skenario O, F, D lulus; ops memberi label pada foto yang dinilai |
| 2. Kalibrasi | Minggu 2 | `shadow` | Threshold disesuaikan sampai target kalibrasi (bagian 6) tercapai |
| 3. Pilot | Minggu 3 | `warn` untuk cabang pilot | Skenario K lulus, kurir paham pesan, tidak ada keluhan "aplikasi lambat" |
| 4. Keputusan | Akhir minggu 3 | `enforce` (jika lulus) | Sign-off bagian 8 |

Mode diubah admin di Web UI → Tenant tanpa deploy ulang dan tanpa mengubah aplikasi.

## 4. Kriteria masuk dan keluar

**Masuk UAT:** preflight tanpa ❌, backup NAS sudah berjalan minimal sekali, root CA terpasang di perangkat uji
(jika `PQE_TLS=internal`), semua peserta punya API key.

**Lulus UAT:** semua skenario wajib (★) lulus, target kalibrasi tercapai, tidak ada bug kategori kritis yang terbuka,
uji restore berhasil.

## 5. Skenario

Isi kolom **Hasil** dengan ✅ / ❌ dan catatan. Skenario bertanda ★ wajib lulus.

### IT / infra

| ID | Skenario | Langkah | Hasil yang diharapkan | Hasil |
|---|---|---|---|---|
| I-1 ★ | Preflight | `pqe preflight` atau Web UI → Sistem | "SIAP production", 0 gagal | |
| I-2 ★ | HTTPS | Buka `https://<domain>/ui` dari PC ops dan HP | Tanpa peringatan sertifikat, `http://` dialihkan ke `https://` | |
| I-3 ★ | Backup NAS | Web UI → Backup NAS → Backup foto & database sekarang | File muncul di share NAS (`photos/...`, `database/pqe-*.dump`), tanpa error | |
| I-4 ★ | Uji restore | `pg_restore` dump terbaru ke database uji; salin 3 foto dari NAS dan buka | Data dan foto utuh | |
| I-5 ★ | Pagar pengaman | Coba `pqe demo-upload` di server production | Ditolak dengan pesan `PQE_ENV=production`; tidak ada foto baru di bucket aplikasi | |
| I-6 | Engine mati | `docker compose -f docker-compose.prod.yml stop engine` selama 10 menit, lalu start | Aplikasi pengiriman tetap jalan; foto selama mati diproses setelah engine hidup (polling/reconcile) | |
| I-7 | Restart server | Reboot server | Semua layanan naik sendiri, NAS ter-mount, preflight tetap hijau | |
| I-8 | Metrik | Buka `/metrics` | Ada `pqe_queue_depth`, `pqe_backup_pending`, dll. | |
| I-9 ★ | Bucket aplikasi hanya dibaca, arsip di `ocr` | Preflight (Web UI → Sistem) dan Web UI → Koneksi SILO → Test koneksi; cek policy service account; kirim 1 foto lewat Uji Coba Kamera lalu lihat bucket `ocr` di console SILO | Preflight "Tulis bucket arsip 'ocr'" dan "Bucket aplikasi hanya dibaca" ✅; Test koneksi 3 cek ✅; key engine tanpa `PutObject`/`DeleteObject` di bucket aplikasi; file muncul di `ocr/uji-coba/<yyyy>/<mm>/<dd>/` dalam beberapa detik | |
| I-10 ★ | Versi terlihat | Buka Web UI (sidebar & pojok kanan atas), `curl -I https://<domain>/healthz`, `docker compose -f docker-compose.prod.yml exec engine pqe --version` | Ketiganya menunjukkan versi yang sama dengan tag yang di-deploy (header `X-PQE-Version`) | |

### Ops / SPV

| ID | Skenario | Langkah | Hasil yang diharapkan | Hasil |
|---|---|---|---|---|
| O-1 ★ | Dashboard | Web UI → Dashboard | Jumlah PASS/WARN/REJECT, alasan terbanyak, kurir/cabang bermasalah tampil | |
| O-2 ★ | Review foto | Web UI → Review, buka 20 foto | Penilaian masuk akal; foto bisa dibuka; tombol Setujui / Minta foto ulang berfungsi | |
| O-3 ★ | Cari resi | Cari dengan nomor resi asli | Semua foto resi itu tampil dengan verdict dan alasan | |
| O-4 ★ | Foto jelas | Uji Coba Kamera (HP), mode Simulasi, foto resi yang jelas | Diterima, status "diupdate", **Foto untuk disimpan aplikasi** terisi (ukuran < 1 MB); **Buka di Web UI** menampilkan foto dari arsip engine | |
| O-5 ★ | Foto buram | Foto sambil bergerak | Ditolak, pesan "Wajib foto ulang. Foto buram…", status "TIDAK diupdate", Foto untuk disimpan aplikasi "tidak ada (ditolak)"; di Web UI foto asli yang ditolak tetap terlihat (arsip engine, untuk review) | |
| O-6 ★ | Foto gelap | Foto di tempat gelap | Ditolak dengan saran cahaya; foto tidak diubah/dicerahkan | |
| O-7 | Foto pecah | Zoom digital berlebihan / screenshot | WARN atau REJECT dengan alasan pecah/resolusi | |
| O-8 | Duplikat | Kirim foto yang sama dua kali | Terdeteksi duplikat | |
| O-9 | Mode | "Tampilkan sebagai mode" shadow/warn/enforce | Pesan ke kurir berbeda sesuai mode | |
| O-10 ★ | Teks resi terbaca | Foto resi/label dari dekat, jelas | Diterima; di detail foto "Teks terbaca: N kata", isi No. resi = nomor di foto → "nomor resi terbaca" | |
| O-11 ★ | Teks tidak terbaca | Foto resi dari jauh / miring / sebagian tertutup | Ditolak dengan alasan teks tidak terbaca (`TEXT_UNREADABLE`/`NO_TEXT_DETECTED`) + saran mendekatkan kamera | |
| O-12 ★ | Teks tetap terbaca setelah kompres | Foto resi besar (> 1 MB) yang jelas | Detail foto: "Teks setelah kompres" skor tidak turun berarti; buka foto di arsip `ocr`, teks terbaca oleh mata | |
| O-13 ★ | Bukti kecelakaan tanpa teks | Foto kerusakan/kecelakaan tanpa tulisan, `photo_type=accident` | Tidak ditolak karena teks (`NO_TEXT_DETECTED` tidak menjadi WARN/REJECT); buram/gelap tetap dinilai | |
| O-14 | Invoice/struk | Foto invoice jelas dan buram, `photo_type=invoice` | Jelas → PASS; tidak terbaca → REJECT | |
| O-15 | Nomor tidak cocok | Foto label resi A dikirim dengan `awb` resi B (format sama) | WARN `REFERENCE_MISMATCH` berisi nomor yang terbaca | |
| O-16 | Jauh dari tujuan | Kirim `dest_lat/dest_lng` alamat lain (> 1 km) + `lat/lng` HP | WARN `FAR_FROM_DESTINATION` dengan jaraknya | |
| O-17 | Pra-cek di HP | Uji Coba Kamera: foto sengaja buram & gelap | Bagian "Pra-cek di HP" menandai BLURRY/TOO_DARK sebelum hasil engine; foto tetap terkirim | |
| O-18 ★ | Kalibrasi & alert | Review ≥ 10 foto REJECT (setujui/minta ulang); isi Laporan & alert → Kirim tes | Halaman Kalibrasi menampilkan salah tolak & saran; pesan tes masuk ke channel chat | |

### Finance

| ID | Skenario | Langkah | Hasil yang diharapkan | Hasil |
|---|---|---|---|---|
| F-1 ★ | Evidence per resi | Web UI → Evidence, isi satu resi → Buat PDF (atau dari detail foto: "Evidence PDF resi ini") | PDF hanya berisi foto PASS/WARN (dari arsip engine), tanpa skor internal | |
| F-2 | Evidence per invoice | Web UI → Evidence, isi banyak resi (satu per baris) → Buat PDF | Satu PDF berisi foto layak dari semua resi | |
| F-3 | Verifikasi keaslian | `GET /v1/verify/<sha256>` dari PDF | Foto asli ditemukan dan cocok | |

### Developer aplikasi (lingkungan pengembangan / pilot)

| ID | Skenario | Langkah | Hasil yang diharapkan | Hasil |
|---|---|---|---|---|
| D-1 ★ | Foto diterima | `checkFile` dengan foto jelas dan `objectKey` | `allow_status_update: true`, `image` (API: `output_image`) terisi; aplikasi menyimpan foto itu ke bucket-nya di `objectKey` dan update status; di Web UI foto tampil, **Arsip engine** = `ocr/<tenant>/...` (isi sama dengan `output_image`) dan **Lokasi di aplikasi** = key itu | |
| D-2 ★ | Foto ditolak | Mode `enforce` (tenant `uji-coba` atau tenant pilot), foto buram | `allow_status_update: false`, `image: null` (API `output_image: null`), `courier_message` tampil di HP, aplikasi **tidak** menyimpan foto, status tidak diupdate; ops tetap bisa melihat foto asli di Web UI (arsip engine) | |
| D-3 ★ | Engine mati | Matikan engine / arahkan ke URL salah | `decided: false`, `image: null`; aplikasi menyimpan **foto asli** ke SILO dan update status, kurir tidak tertahan | |
| D-4 ★ | Waktu tunggu | 50 foto berturut-turut | p95 waktu tunggu ≤ 3 detik; jika lewat, aplikasi tetap lanjut (fail-open) | |
| D-5 | Webhook | Daftarkan URL webhook di tenant | Event `analysis.completed` diterima, tanda tangan valid | |
| D-6 | Bahasa | `lang: "en"` | `courier_message` berbahasa Inggris | |
| D-7 | Lapor lokasi belakangan | `checkFile` tanpa `objectKey`, simpan foto, lalu `reportStored(photoId, key)` | Di Web UI baris **Lokasi di aplikasi** berubah dari "tidak dilaporkan aplikasi" menjadi key itu | |

### Kurir pilot (fase 3)

| ID | Skenario | Langkah | Hasil yang diharapkan | Hasil |
|---|---|---|---|---|
| K-1 ★ | Alur normal | Pengiriman harian di cabang pilot | Tidak ada keluhan aplikasi lambat; foto jelas langsung diterima | |
| K-2 ★ | Pesan foto ulang | Foto buram di mode `warn` | Kurir paham pesannya dan bisa memilih Foto ulang / Tetap kirim | |
| K-3 | Sinyal lemah | Kirim di area sinyal lemah | Engine tidak menambah kegagalan; status tetap bisa diupdate | |

## 6. Kalibrasi

Selama fase 1–2, ops memberi label lewat Review (Setujui = foto layak, Minta foto ulang = tidak layak). Target sebelum
naik ke `warn`/`enforce`:

| Ukuran | Target | Cara melihat |
|---|---|---|
| Salah tolak (foto layak dinilai REJECT) | < 5% | Foto REJECT yang di-Setujui ops ÷ semua REJECT yang direview |
| Foto buruk lolos (foto tidak layak dinilai PASS) | < 5% | Sampel 100 foto PASS acak, dicek ops |
| Salah tolak karena teks (teks terbaca oleh mata, engine bilang tidak) | < 5% | Foto REJECT dengan alasan `TEXT_UNREADABLE`/`NO_TEXT_DETECTED` yang di-Setujui ops ÷ semua yang direview |
| Waktu proses | p95 ≤ 3 detik | Web UI → Uji Coba Kamera (waktu tunggu) / metrik `pqe_analysis_seconds` |

Jika target belum tercapai, sesuaikan threshold di Web UI → Tenant (lihat [configuration.md bagian 6](configuration.md#6-kalibrasi-threshold)),
lalu nilai ulang dengan tombol Analisis ulang pada sampel foto.

## 7. Pencatatan masalah

| No | Tanggal | Skenario | Kategori (kritis/sedang/ringan) | Deskripsi | Pelapor | Status |
|---|---|---|---|---|---|---|
| | | | | | | |

Kritis = kurir tertahan, data hilang, foto bocor ke pihak lain, atau backup gagal. Masalah kritis harus selesai sebelum
sign-off.

## 8. Sign-off

| Peran | Nama | Keputusan (lulus / lulus dengan catatan / gagal) | Tanggal | Tanda tangan |
|---|---|---|---|---|
| PIC UAT | | | | |
| IT / infra | | | | |
| Ops | | | | |
| Finance | | | | |
| Developer aplikasi | | | | |

Setelah sign-off: naikkan mode `courier-app` sesuai keputusan, nonaktifkan tenant `uji-coba`, dan catat versi engine
(tag) yang disetujui.
