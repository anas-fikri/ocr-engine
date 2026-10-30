#!/usr/bin/env bash
# Update engine di server production ke kode terbaru, dengan kode build (commit) yang terlihat di Web UI.
#
#   scripts/deploy.sh            # ambil commit terbaru cabang yang sedang dipakai
#   scripts/deploy.sh v0.1.0     # pindah ke tag/cabang tertentu (rollback juga pakai ini)
#
# Tidak butuh curl/wget di server: /healthz dibaca dari dalam container engine dengan Python milik image.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

if [ -n "${1:-}" ]; then
  git fetch --tags origin
  git checkout "$1"
fi
git pull --ff-only || echo "• PERINGATAN: git pull --ff-only gagal (cabang lokal server berbeda dengan remote?). Lanjut dengan kode lokal."
PQE_BUILD="$(git rev-parse --short HEAD)"
export PQE_BUILD
echo "• Build $PQE_BUILD: $(git log -1 --format=%s)"
if UPSTREAM="$(git rev-parse --short '@{u}' 2>/dev/null)" && [ "$UPSTREAM" != "$PQE_BUILD" ]; then
  echo "• PERINGATAN: commit $PQE_BUILD tidak sama dengan remote ($UPSTREAM). Ada commit/merge lokal di server:"
  git log --oneline '@{u}..HEAD' | sed 's/^/    /' || true
  echo "  Untuk mengikuti remote persis: git fetch origin && git reset --hard @{u} (perubahan lokal hilang)."
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "• PERINGATAN: ada perubahan lokal yang belum di-commit; image di-build termasuk perubahan itu."
fi

$COMPOSE up -d --build

# Baca /healthz: utamakan dari dalam container (tanpa alat tambahan di host), lalu curl/wget host jika ada.
healthz() {
  $COMPOSE exec -T engine python -c \
    'import sys, urllib.request; sys.stdout.write(urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=5).read().decode())' \
    2>/dev/null && return 0
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null && return 0
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -qO- -T 5 http://127.0.0.1:8000/healthz 2>/dev/null && return 0
  fi
  return 1
}

echo -n "• Menunggu engine siap"
RUNNING=""
for _ in $(seq 1 60); do
  if RUNNING="$(healthz)" && [ -n "$RUNNING" ]; then
    break
  fi
  RUNNING=""
  echo -n "."
  sleep 2
done
echo

STATUS=0
if [ -z "$RUNNING" ]; then
  echo "• GAGAL: /healthz tidak bisa dibaca dalam 2 menit (engine belum siap atau gagal start), versi yang berjalan"
  echo "  TIDAK diketahui. Cek: $COMPOSE ps && $COMPOSE logs --tail 80 engine"
  STATUS=1
else
  echo "• Engine berjalan: $RUNNING"
  RUN_BUILD="$(printf '%s' "$RUNNING" | sed -n 's/.*"build":"\([^"]*\)".*/\1/p')"
  if [ "$RUN_BUILD" = "$PQE_BUILD" ]; then
    echo "• OK: server menjalankan build $PQE_BUILD (cek juga di Web UI, di bawah nama aplikasi)"
  else
    echo "• PERINGATAN: build yang berjalan '${RUN_BUILD:-?}', seharusnya $PQE_BUILD. Cek: $COMPOSE logs --tail 50 engine"
    STATUS=1
  fi
fi

echo "• Preflight:"
$COMPOSE exec -T engine pqe preflight || true

cat <<EOF

• Perintah pqe dijalankan DI DALAM container engine (tidak ada perintah pqe di host). Contoh:
    scripts/pqe.sh create-client --code <kode-aplikasi> --name "Nama Aplikasi"
    $COMPOSE exec engine pqe create-client --code <kode-aplikasi> --name "Nama Aplikasi"
EOF
exit $STATUS
