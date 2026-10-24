# ADR 0002: Web UI vanilla JS tanpa build step, bukan React

- **Status:** Diterima (22 Okt 2026)
- **Menggantikan:** rencana "Web UI: React + TypeScript" di [sdlc-plan.md](../sdlc-plan.md) 3.10

## Konteks

Web UI dipakai internal (admin, ops, viewer) untuk dashboard, antrean review, cari foto, uji foto, editor config tenant, API key, dan status sistem. Kebutuhannya:

- Bisa jalan di PC uji coba dan di jaringan internal yang mungkin tidak bisa mengakses CDN/npm registry.
- Mudah dirawat tim backend yang kebanyakan bukan spesialis frontend.
- Satu artefak deploy: image engine sudah berisi UI.

## Keputusan

Web UI berupa tiga file statis (`engine/webui/index.html`, `app.js`, `style.css`), vanilla JavaScript **tanpa framework, tanpa dependency eksternal, tanpa build step**. Disajikan oleh FastAPI di `/ui` dan memanggil API `/v1` yang sama dengan aplikasi lain (autentikasi API key).

## Konsekuensi

- (+) Tidak ada `node_modules`, bundler, atau pipeline build terpisah. Ubah file, refresh browser.
- (+) Tidak ada dependency frontend yang perlu dipatch (mengurangi permukaan serangan supply chain).
- (+) UI hanya klien API. Semua aturan bisnis & otorisasi tetap di backend.
- (−) Tanpa komponen reusable/type checking. Jika UI tumbuh jauh lebih kompleks (misal portal pelanggan publik), pertimbangkan framework di repo/artefak terpisah yang tetap memakai API yang sama.
- (−) Semua teks UI saat ini bahasa Indonesia (pengguna internal). Pesan alasan foto tetap mengikuti bahasa browser lewat `Accept-Language`.

## Alternatif yang ditolak

- **React + TypeScript (rencana awal):** butuh toolchain Node & build untuk setiap perubahan, menambah dependency yang harus dirawat, dan tidak memberi manfaat berarti untuk skala UI internal ini.
- **Server-side template (Jinja2):** mencampur logika UI ke backend. Pendekatan "UI sebagai klien API" lebih konsisten dengan integrasi aplikasi lain.
