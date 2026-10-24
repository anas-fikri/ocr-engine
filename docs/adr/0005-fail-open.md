# ADR 0005: Fail-open, engine tidak boleh menghambat kurir

- **Status:** Diterima (22 Okt 2026)
- **Terkait:** [sdlc-plan.md](../sdlc-plan.md) 1.4 prinsip 1, 4.5

## Konteks

Prinsip dari pengguna: deteksi dan kompresi **tidak boleh membuat proses kurir (dan aplikasi lain) terasa lambat**, dan error engine tidak boleh mengganggu update status pengiriman.

## Keputusan

- **Default async.** Engine bekerja di luar jalur request kurir: foto diupload ke SILO seperti biasa, engine menemukannya lewat event/polling dan memprosesnya di worker.
- **Mode sinkron opsional** (`POST /v1/analyze?sync=true`) punya batas waktu (`timeout_ms`, default `PQE_SYNC_TIMEOUT_MS` = 3000). Jika lewat, respons tetap `202` dan hasil menyusul. Request tidak pernah gagal karena analisis lambat.
- **Client resmi** (`clients/`) tidak pernah melempar exception karena engine lambat/mati. Hasilnya `{ok: false, unavailable: true}` dan aplikasi melanjutkan proses bisnis.
- **Engine mati** → upload & update status kurir tetap jalan. Saat hidup lagi, ingestor melakukan reconciliation (lookback minimal 7 hari) dan event SILO yang tertunda dikirim ulang dari `queue_dir`.
- **Error per foto terisolasi:** file rusak ditandai `UNREADABLE_FILE` dan masuk review. Error sementara di-retry lalu `dead` tanpa menghentikan antrean.
- **Mode penegakan bertahap** `shadow` → `warn` → `enforce` per tenant/profile. Hanya `enforce` + `REJECT` yang menghasilkan `action: "reject"`, dan keputusan menolak tetap di tangan aplikasi.

## Konsekuensi

- (+) Operasional kurir tidak pernah tergantung ketersediaan engine.
- (+) Deploy/rollback engine kapan saja tanpa jendela maintenance untuk aplikasi.
- (−) Pada kondisi gangguan, sebagian foto bisa lolos tanpa dicek real-time. Dimitigasi dengan reconciliation dan antrean review setelahnya, sebelum invoice keluar.
- (−) Aplikasi yang ingin menolak foto secara real-time harus menangani tiga kemungkinan: hasil tersedia, `202` (masih diproses), dan `unavailable`. Panduan ada di [integration-guide.md](../integration-guide.md).

## Alternatif yang ditolak

- **Fail-closed (tolak upload jika engine tidak menjawab):** membuat engine menjadi single point of failure operasional pengiriman.
- **Analisis sinkron di backend aplikasi sebelum menyimpan foto:** menambah latency setiap upload dan butuh perubahan backend yang belum memungkinkan.
