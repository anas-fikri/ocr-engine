#!/usr/bin/env bash
# Siapkan .env production sekali: secret acak, domain HTTPS, mode production.
# Aman dijalankan ulang: secret yang sudah diisi (bukan nilai bawaan) tidak diubah.
#
#   scripts/prod-setup.sh <domain> [internal|email-untuk-lets-encrypt]
#   contoh: scripts/prod-setup.sh photo-quality.pcpexpress.com
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
TLS="${2:-internal}"
if [ -z "$DOMAIN" ]; then
  echo "Pakai: scripts/prod-setup.sh <domain> [internal|email]"
  echo "  internal (bawaan) = sertifikat CA internal, untuk intranet"
  echo "  email             = sertifikat Let's Encrypt, domain harus publik dan port 80/443 terbuka"
  exit 1
fi
command -v openssl >/dev/null || { echo "openssl tidak ditemukan"; exit 1; }

[ -f .env ] || { cp .env.example .env; echo "• .env dibuat dari .env.example"; }

rand() { openssl rand -hex 32; }
get_kv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
set_kv() {
  if grep -qE "^$1=" .env; then
    awk -v k="$1" -v v="$2" 'index($0, k "=") == 1 { print k "=" v; next } { print }' .env > .env.tmp && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
fill_secret() {
  case "$(get_kv "$1")" in
    ""|pqe_local_admin_change_me|local-secret-key-change-me|local-event-token|pqe-local-only)
      set_kv "$1" "$2"; echo "• $1 dibuat (acak)";;
    *) echo "• $1 sudah terisi, tidak diubah";;
  esac
}

fill_secret POSTGRES_PASSWORD "$(rand)"
fill_secret PQE_BOOTSTRAP_ADMIN_KEY "pqe_$(rand)"
fill_secret PQE_SECRET_KEY "$(rand)"
fill_secret PQE_S3_EVENT_TOKEN "$(rand)"
set_kv PQE_ENV production
set_kv PQE_DOMAIN "$DOMAIN"
set_kv PQE_TLS "$TLS"
set_kv PUBLIC_BASE_URL "https://$DOMAIN"
set_kv ENGINE_CREATE_BUCKETS false
chmod 600 .env

NAS="$(get_kv NAS_MOUNT)"; NAS="${NAS:-./backup}"
mkdir -p "$NAS" 2>/dev/null || true
if [ "$NAS" = "./backup" ]; then
  echo "• NAS_MOUNT masih ./backup (folder lokal). Mount share NAS lalu isi NAS_MOUNT di .env (docs/deploy-production.md)."
fi

cat <<MSG

Selesai. Langkah berikutnya:
  1. docker compose -f docker-compose.prod.yml up -d --build
  2. Buka https://$DOMAIN/ui dan masuk dengan API key admin:
       $(get_kv PQE_BOOTSTRAP_ADMIN_KEY)
     (simpan di password manager; key ini juga ada di .env)
  3. Web UI -> Pengaturan SILO: isi key SILO, Test koneksi, Simpan.
  4. Web UI -> Backup NAS: Test NAS, aktifkan, Simpan.
  5. docker compose -f docker-compose.prod.yml exec engine pqe preflight   (harus tanpa ❌)
MSG
