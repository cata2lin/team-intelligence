#!/usr/bin/env bash
# xConnector fulfill — PARALEL, cu LOCK PER MAGAZIN (nu global).
#
# DE CE per magazin (schimbat 2026-08-18): înainte crontab-ul avea UN SINGUR `flock -n /tmp/xc_fulfill.lock`
# pe toată flota. Când un magazin avea backlog mare (Esteban, 641 comenzi după incidentul de azi = 1h+ de
# rulare), lock-ul rămânea ținut și tick-urile următoare erau SĂRITE COMPLET — adică TOATE celelalte 22 de
# magazine stăteau o oră fără AWB-uri, deși erau libere. Acum fiecare magazin are lock-ul lui: un magazin
# ocupat se sare doar pe el însuși, restul merg normal la fiecare 15 min.
#
# ⚠️ Ordinea contează: flock-ul se ia ÎNAINTE de redirectarea în log. Dacă truncam `$LOGD/$D.out` înainte de
# flock, un tick nou ar șterge logul rulării încă în curs pentru acel magazin.
#
# Lock-urile `/tmp/xc_shop.<magazin>.lock` sunt PARTAJATE cu xc_backlog_parallel.sh → cele două nu se pot
# călca niciodată pe același magazin, deși nu mai există lock global.
set -uo pipefail
ENVF=/root/Scripturi/.env.xconnector
export XCONNECTOR_SHOPS="$(sed -n 's/^XCONNECTOR_SHOPS=//p' "$ENVF")"
export SHOPIFY_ADMIN_TOKENS="$(sed -n 's/^SHOPIFY_ADMIN_TOKENS=//p' "$ENVF")"
export SHOPIFY_STORES_CSV=/root/Scripturi/stores.csv
export DATABASE_URL_AWBPRINT="$(sed -n 's/^DATABASE_URL_AWBPRINT=//p' "$ENVF")"
export HERE_API_KEY="$(sed -n 's/^HERE_API_KEY=//p' "$ENVF")"
export DATABASE_URL_METRICS="$(grep -m1 '^DATABASE_URL_METRICS=' /root/Scripturi/.env | cut -d= -f2-)"
export KB_DATABASE_URL="$(grep -m1 '^KB_DATABASE_URL=' /root/Scripturi/.env | cut -d= -f2-)"
export XC_AWB_EVENT_LOG=/root/Scripturi/logs/xc_awb_events.jsonl
export COURIER_CREDS_JSON="$(/root/.local/bin/uv run /root/Scripturi/team-intelligence/plugins/core/scripts/kb.py secret-get COURIER_CREDS_JSON 2>/dev/null)"
echo "=== $(date '+%F %T %Z') xconnector fulfill PARALEL (lock/magazin) ==="
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector
DOMAINS=$(python3 -c "import xconnector as X; print(' '.join(s['shopDomain'] for s in X.load_shops()))")
LOGD=/root/Scripturi/logs/fulfill_par; mkdir -p "$LOGD"
TMPD=$(mktemp -d /tmp/xc_fulfill_run.XXXXXX)
for D in $DOMAINS; do
  K="${D%%.*}"
  (
    exec 9>"/tmp/xc_shop.$K.lock"
    # -n = nu aștepta: magazinul e deja în lucru (tick anterior / backlog de noapte) → îl sar DOAR pe el.
    flock -n 9 || exit 0
    python3 -u xconnector.py fulfill --shop "$D" --apply --days 30 --max-age-min 5 > "$LOGD/$D.out" 2>&1
    grep -oE 'APLICAT: AWB [0-9]+' "$LOGD/$D.out" | awk '{print $3}' | head -1 > "$TMPD/$K.n"
  ) &
done
wait
TOT=$(echo $DOMAINS | wc -w)
RAN=$(ls "$TMPD" 2>/dev/null | wc -l)
AWB=$(cat "$TMPD"/*.n 2>/dev/null | awk '{s+=$1} END{print s+0}')
rm -rf "$TMPD"
# dead-man-switch: cronul care face TOATE AWB-urile nu avea heartbeat, desi 20 de joburi mai mici aveau.
# Ping DOAR daca rularea a ajuns pana aici (deci nu a crapat) -> data_health il raporteaza overdue daca moare.
/root/Scripturi/.venv/bin/python /root/Scripturi/heartbeat.py xc_fulfill >/dev/null 2>&1 || true
echo "=== $(date '+%F %T') PARALEL gata · AWB=$AWB · rulate=$RAN/$TOT · sarite(ocupate)=$((TOT-RAN)) ==="
