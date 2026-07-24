#!/bin/bash
# run_profit_sync.sh — sincronizează profit_orders (motorul de profit) pt luna curentă ȘI cea precedentă.
#
# DE CE (2026-07-24): vechiul script apela `scripts_cli.py call POST /api/profitability/run` dintr-un path
# `scripts-app/scripts` care S-A MUTAT + app-ul web nu mai rula → cron-ul crăpa tăcut pe `cd` → profit_orders
# ne-sincronizat → `cache.brand_pnl_monthly` (sursa unică) subnumăra livrările (ex CZ iunie 2.174 vs 2.450 real
# = falsă pierdere −14k). ACUM apelăm DIRECT `profit_orders_sync.py` (care rulează run_profitability în proces,
# fără app/CLI). Vezi [[profit-orders-no-cron-silent-collapse]].
#
# Re-sincronizează ȘI luna precedentă ÎN FIECARE ZI (nu doar primele 3): livrările internaționale (CZ/PL/BG via
# DPD) ajung săptămâni mai târziu, iar dacă luna se îngheață devreme, coada de livrări se pierde.
set -uo pipefail
set -a; source /root/Scripturi/.env 2>/dev/null; set +a
PY=/root/Scripturi/.venv/bin/python
SYNC=/root/Scripturi/profit_orders_sync.py
cd /root/Scripturi

echo "=== $(date -u '+%F %T') UTC — sync luna curentă $(date +%Y-%m) ==="
$PY "$SYNC" "$(date +%Y-%m)"; rc_cur=$?

prev=$(date -d "$(date +%Y-%m-01) -1 day" +%Y-%m)
echo "=== $(date -u '+%F %T') UTC — re-închid luna precedentă $prev (coada de livrări intl) ==="
$PY "$SYNC" "$prev"; rc_prev=$?

# exit 0 DOAR dacă ambele au reușit → cron-ul face `&& heartbeat.py profit_sync`; altfel data_health semnalează.
if [ "$rc_cur" -eq 0 ] && [ "$rc_prev" -eq 0 ]; then
  echo "✅ sync OK (curent + $prev)"; exit 0
fi
echo "⚠️ sync eșuat (cur=$rc_cur prev=$rc_prev)"; exit 1
