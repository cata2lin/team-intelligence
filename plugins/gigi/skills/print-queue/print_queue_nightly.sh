#!/usr/bin/env bash
# print_queue_nightly.sh — construiește coada de print pt DEPOZIT (până IERI, nu ziua curentă).
# Rulează la 1 NOAPTEA; salvează în metrics.print_queue → dimineața depozitul doar interoghează + printează.
# Cron (VPS): 0 1 * * * /root/Scripturi/team-intelligence/plugins/gigi/skills/print-queue/print_queue_nightly.sh >> /root/Scripturi/logs/print_queue.log 2>&1
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"                              # cron n-are uv în PATH
[ -f /root/Scripturi/.env ] && { set -a; . /root/Scripturi/.env 2>/dev/null || true; set +a; }  # VPS: KB_DATABASE_URL + secrete (tolerant, ÎNAINTE de set -e)
set -euo pipefail
cd "$(dirname "$0")"
echo "===== print_queue nightly $(date '+%F %T') ====="
uv run print_queue.py sync --apply
echo "===== done $(date '+%F %T') ====="
