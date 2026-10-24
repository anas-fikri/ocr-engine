#!/usr/bin/env bash
# Update engine di server production ke kode terbaru, dengan kode build (commit) yang terlihat di Web UI.
#
#   scripts/deploy.sh            # ambil commit terbaru cabang yang sedang dipakai
#   scripts/deploy.sh v0.1.0     # pindah ke tag/cabang tertentu (rollback juga pakai ini)
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

if [ -n "${1:-}" ]; then
  git fetch --tags origin
  git checkout "$1"
fi
git pull --ff-only || true
PQE_BUILD="$(git rev-parse --short HEAD)"
export PQE_BUILD
echo "• Build $PQE_BUILD: $(git log -1 --format=%s)"

$COMPOSE up -d --build

echo -n "• Menunggu engine siap"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    break
  fi
  echo -n "."
  sleep 2
done
echo
RUNNING="$(curl -fsS http://127.0.0.1:8000/healthz || echo '{}')"
echo "• Engine berjalan: $RUNNING"
case "$RUNNING" in
  *"\"build\":\"$PQE_BUILD\""*) echo "• OK: server menjalankan build $PQE_BUILD (cek juga di Web UI, di bawah nama aplikasi)";;
  *) echo "• PERINGATAN: build yang berjalan belum $PQE_BUILD. Cek: $COMPOSE logs --tail 50 engine"; exit 1;;
esac
$COMPOSE exec -T engine pqe preflight || true
