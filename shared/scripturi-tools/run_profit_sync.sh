#!/bin/bash
# run_profit_sync.sh — sincronizează comenzile lunii curente în motorul de profit (profit_orders).
#
# DE CE EXISTĂ: `profit_orders` e scris DOAR de endpointul `/api/profitability/run` al aplicației
# web — care nu avea niciun cron. A rămas nesincronizat 23 de zile (1→23 iul 2026), timp în care
# `cache.brand_pnl_monthly` s-a recalculat la fiecare 3 ore și a raportat fidel, pentru iulie,
# 0 comenzi livrate / 0 venit / −1.508.923 RON pierdere. Toate skill-urile care moștenesc acel
# cache (multi-brand-pnl, daily-ops-briefing, agency-audit, product-matrix) au dat același răspuns
# fals. Descoperit de data_health.py, nu de un om.
#
# `resync_shopify=true` e obligatoriu: dacă luna are deja rânduri, endpointul sare peste Shopify
# și doar re-face tracking-ul AWB — deci comenzile noi n-ar intra niciodată.
# În primele 3 zile ale lunii reluăm și luna precedentă, ca s-o închidem cu datele finale.
#
# cron: 30 2 * * *  (înaintea build_cache de la 5:30, ca P&L-ul să se recalculeze pe date proaspete)
set -euo pipefail
set -a; source /root/Scripturi/.env; set +a
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/scripts-app/scripts

run_month () {
  /root/.local/bin/uv run scripts_cli.py call POST /api/profitability/run \
    --json "{\"month\":\"$1\",\"resync_shopify\":true,\"force\":true}" --apply --confirm
}

echo "=== $(date '+%F %T') sync luna curentă $(date +%Y-%m) ==="
run_month "$(date +%Y-%m)"

if [ "$((10#$(date +%d)))" -le 3 ]; then
  prev=$(date -d "$(date +%Y-%m-01) -1 day" +%Y-%m)
  echo "=== $(date '+%F %T') închid luna precedentă $prev ==="
  run_month "$prev"
fi
echo "=== $(date '+%F %T') gata ==="
