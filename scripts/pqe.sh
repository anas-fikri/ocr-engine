#!/usr/bin/env bash
# Jalankan perintah pqe di dalam container engine production (perintah pqe tidak terpasang di host).
#
#   scripts/pqe.sh create-client --code aplikasi-keuangan --name "Aplikasi Keuangan"
#   scripts/pqe.sh preflight
#   scripts/pqe.sh --version
#
# Sama dengan: docker compose -f docker-compose.prod.yml exec engine pqe <argumen>
set -euo pipefail
cd "$(dirname "$0")/.."
TTY_FLAG=()
[ -t 0 ] && [ -t 1 ] || TTY_FLAG=(-T)  # tanpa terminal (cron, pipe): jangan minta TTY
exec docker compose -f docker-compose.prod.yml exec ${TTY_FLAG[@]+"${TTY_FLAG[@]}"} engine pqe "$@"
