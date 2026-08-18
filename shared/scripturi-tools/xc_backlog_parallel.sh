#!/usr/bin/env bash
# Golire PARALELĂ de backlog xConnector: `fulfill` pe FIECARE magazin în paralel (rate-limit-ul e per
# token/magazin, deci nu se calcă). 2 pasuri (pas 1: AWB unfulfilled + eliberează held; pas 2: AWB pe
# held-urile eliberate). Uz: bash xc_backlog_parallel.sh [DAYS] [MAX_AGE_MIN]  (default 60 5).
#
# LOCK PER MAGAZIN (2026-08-18): înainte se baza pe lock-ul GLOBAL `/tmp/xc_fulfill.lock` ca să blocheze
# cronul de 15 min cât rulează. Lock-ul global a fost eliminat (un magazin lent oprea toată flota), deci
# acum foloseşte ACELEAŞI lock-uri `/tmp/xc_shop.<magazin>.lock` ca `xc_fulfill.sh` → un magazin nu poate
# fi procesat simultan de backlog și de cronul de 15 min, dar restul flotei merge normal în paralel.
# Suprapunerea între două rulări de backlog (cron 3,4,5) e prevenită de `/tmp/xc_backlog.lock` din crontab.
set -uo pipefail
DAYS="${1:-60}"; MAXAGE="${2:-5}"
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
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector
LOGD=/root/Scripturi/logs/backlog_par; mkdir -p "$LOGD"
DOMAINS=$(python3 -c "import xconnector as X; print(' '.join(s['shopDomain'] for s in X.load_shops()))")
TMPD=$(mktemp -d /tmp/xc_backlog_run.XXXXXX)
echo "===== BACKLOG PARALEL start $(date '+%F %T') · $(echo $DOMAINS | wc -w) magazine · days=$DAYS max-age=$MAXAGE ====="
for D in $DOMAINS; do
  K="${D%%.*}"
  (
    exec 9>"/tmp/xc_shop.$K.lock"
    # magazinul e deja în lucru (cronul de 15 min) → îl sar DOAR pe el, nu toată flota
    flock -n 9 || exit 0
    # lock luat ÎNAINTE de redirect: altfel aș trunchia logul rulării în curs pentru acel magazin
    ( python3 -u xconnector.py fulfill --shop "$D" --apply --days "$DAYS" --held-sweep-hours 0 --max-age-min "$MAXAGE"
      python3 -u xconnector.py fulfill --shop "$D" --apply --days "$DAYS" --held-sweep-hours 0 --max-age-min "$MAXAGE" ) > "$LOGD/$D.out" 2>&1
    grep -oE 'APLICAT: AWB [0-9]+' "$LOGD/$D.out" | awk '{s+=$3} END{print s+0}' > "$TMPD/$K.n"
  ) &
done
wait
TOT=$(echo $DOMAINS | wc -w)
RAN=$(ls "$TMPD" 2>/dev/null | wc -l)
AWB=$(cat "$TMPD"/*.n 2>/dev/null | awk '{s+=$1} END{print s+0}')
REL=$(cat "$LOGD"/*.out 2>/dev/null | grep -cE 'eliberat \(→ AWB')
rm -rf "$TMPD"
echo "===== BACKLOG PARALEL DONE $(date '+%F %T') · AWB=$AWB · held-eliberate=$REL · rulate=$RAN/$TOT · sarite(ocupate)=$((TOT-RAN)) ====="
