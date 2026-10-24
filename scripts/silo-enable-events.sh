#!/usr/bin/env sh
# Aktifkan bucket notification SILO -> engine (real-time) untuk SILO lokal di docker-compose.
# Pemakaian: ./scripts/silo-enable-events.sh [bucket]
# Tanpa ini engine tetap jalan memakai polling (delay beberapa detik).
set -eu
BUCKET="${1:-${AWS_BUCKET:-ekas-pcptransport}}"
USER_="${SILO_ROOT_USER:-siloadmin}"
PASS_="${SILO_ROOT_PASSWORD:-siloadmin-local-only}"

docker compose exec -T silo mcli alias set local http://127.0.0.1:9000 "$USER_" "$PASS_" >/dev/null
docker compose exec -T silo mcli mb -p "local/$BUCKET" >/dev/null
docker compose exec -T silo mcli event add "local/$BUCKET" arn:minio:sqs::PQE:webhook --event put || true
docker compose exec -T silo mcli event list "local/$BUCKET"
echo "OK: upload ke bucket $BUCKET sekarang langsung dikirim ke engine."
