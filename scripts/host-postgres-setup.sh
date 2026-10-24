#!/usr/bin/env bash
# Pindahkan database engine dari container ke PostgreSQL di host server (Ubuntu/Debian).
# Jalankan dari folder repo, dengan sudo, setelah scripts/prod-setup.sh:
#
#   sudo scripts/host-postgres-setup.sh
#   docker compose -f docker-compose.prod.yml up -d --remove-orphans
#
# Yang dilakukan (aman dijalankan ulang):
#   1. Pasang PostgreSQL dari repo OS jika belum ada.
#   2. Buat user & database "pqe" dengan POSTGRES_PASSWORD dari .env.
#   3. Izinkan koneksi dari container Docker saja (172.16.0.0/12), bukan dari LAN.
#   4. Jika container postgres masih jalan dan database host masih kosong, salin datanya.
#   5. Isi .env: PQE_BUNDLED_POSTGRES=0 dan PQE_DATABASE_URL (host.docker.internal).
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(id -u)" -eq 0 ] || { echo "Jalankan dengan sudo: sudo scripts/host-postgres-setup.sh"; exit 1; }
[ -f .env ] || { echo ".env belum ada. Jalankan scripts/prod-setup.sh dulu."; exit 1; }

get_kv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
set_kv() {
  # tulis ulang isi file (bukan mv) supaya pemilik & izin .env tidak berubah
  local tmp; tmp="$(mktemp)"
  if grep -qE "^$1=" .env; then
    awk -v k="$1" -v v="$2" 'index($0, k "=") == 1 { print k "=" v; next } { print }' .env > "$tmp"
  else
    cat .env > "$tmp"; printf '%s=%s\n' "$1" "$2" >> "$tmp"
  fi
  cat "$tmp" > .env; rm -f "$tmp"
}

PW="$(get_kv POSTGRES_PASSWORD)"
case "$PW" in ""|pqe-local-only) echo "POSTGRES_PASSWORD di .env kosong/bawaan. Jalankan scripts/prod-setup.sh dulu."; exit 1;; esac

if ! command -v psql >/dev/null || ! ls /etc/postgresql/*/main >/dev/null 2>&1; then
  echo "• Memasang PostgreSQL"
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql >/dev/null
fi
VER="$(ls /etc/postgresql | sort -V | tail -1)"
CONF="/etc/postgresql/$VER/main"
echo "• PostgreSQL $VER ($CONF)"

# Dengarkan semua alamat, tetapi pg_hba hanya mengizinkan localhost dan jaringan Docker.
mkdir -p "$CONF/conf.d"
cat > "$CONF/conf.d/pqe.conf" <<'CONFEOF'
# dibuat scripts/host-postgres-setup.sh (photo-quality-engine)
listen_addresses = '*'
password_encryption = scram-sha-256
CONFEOF
HBA="host    pqe    pqe    172.16.0.0/12    scram-sha-256    # photo-quality-engine (container Docker)"
grep -qF "photo-quality-engine (container Docker)" "$CONF/pg_hba.conf" || echo "$HBA" >> "$CONF/pg_hba.conf"
systemctl enable -q postgresql
systemctl restart postgresql

su - postgres -c "psql -qtAc \"SELECT 1 FROM pg_roles WHERE rolname='pqe'\"" | grep -q 1 \
  || su - postgres -c "psql -qc \"CREATE ROLE pqe LOGIN\""
su - postgres -c "psql -q" <<SQL
ALTER ROLE pqe WITH LOGIN PASSWORD '$PW';
SQL
su - postgres -c "psql -qtAc \"SELECT 1 FROM pg_database WHERE datname='pqe'\"" | grep -q 1 \
  || su - postgres -c "createdb -O pqe pqe"
echo "• User & database pqe siap"

# Salin data dari container (jika masih jalan) ke host, hanya jika database host masih kosong.
TABLES="$(su - postgres -c "psql -d pqe -qtAc \"SELECT count(*) FROM pg_tables WHERE schemaname='public'\"")"
CID="$(docker compose -f docker-compose.prod.yml ps -q postgres 2>/dev/null || true)"
if [ "$TABLES" = "0" ] && [ -n "$CID" ]; then
  echo "• Menyalin data dari container postgres ke host"
  docker compose -f docker-compose.prod.yml stop engine >/dev/null
  docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U pqe -Fc pqe \
    | PGPASSWORD="$PW" pg_restore -h 127.0.0.1 -U pqe -d pqe --no-owner --no-privileges
  echo "• Data tersalin. Volume container lama (pg-data) tidak dihapus; hapus manual jika sudah yakin."
elif [ "$TABLES" != "0" ]; then
  echo "• Database host sudah berisi tabel, tidak menyalin ulang"
else
  echo "• Container postgres tidak jalan, mulai dengan database kosong (engine membuat tabel saat start)"
fi

set_kv PQE_BUNDLED_POSTGRES 0
set_kv PQE_DATABASE_URL "postgresql+psycopg://pqe:$PW@host.docker.internal:5432/pqe"
echo "• .env: PQE_BUNDLED_POSTGRES=0, PQE_DATABASE_URL -> host.docker.internal:5432"

cat <<MSG

Selesai. Lanjutkan (tanpa sudo):
  docker compose -f docker-compose.prod.yml up -d --remove-orphans
  docker compose -f docker-compose.prod.yml exec engine pqe preflight
Backup database (Web UI -> Backup NAS) tetap jalan: engine memakai pg_dump ke host.
MSG
