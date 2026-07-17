#!/usr/bin/env bash
# print_queue_nightly.sh — construiește coada de print pt DEPOZIT (până IERI, nu ziua curentă).
# Rulează la 1 NOAPTEA; salvează în metrics.print_queue tot ce-i de printat → dimineața depozitul
# doar interoghează + deschide în Chrome. Instalare cron (pe VPS, ca nomen_health.sh):
#     0 1 * * * /root/Scripturi/.../print-queue/print_queue_nightly.sh >> /var/log/print_queue.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
echo "===== print_queue nightly $(date '+%F %T') ====="
uv run print_queue.py sync --apply
echo "===== done $(date '+%F %T') ====="
