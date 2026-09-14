#!/usr/bin/env bash
# Wrapper cu env pt pipeline-ul de densitate colete (/colete <-> Shopify).
# Uz: parcel_colete.sh learn|push|list [--apply]
set -uo pipefail
ENVF=/root/Scripturi/.env.xconnector
export XCONNECTOR_SHOPS="$(sed -n "s/^XCONNECTOR_SHOPS=//p" "$ENVF")"
export SHOPIFY_ADMIN_TOKENS="$(sed -n "s/^SHOPIFY_ADMIN_TOKENS=//p" "$ENVF")"
export SHOPIFY_STORES_CSV=/root/Scripturi/stores.csv
export KB_DATABASE_URL="$(grep -m1 "^KB_DATABASE_URL=" /root/Scripturi/.env | cut -d= -f2-)"
CMD="${1:-}"; shift || true
case "$CMD" in
  learn) exec /root/Scripturi/.venv/bin/python /root/Scripturi/parcel_density_learn.py "$@" ;;
  push)  exec /root/Scripturi/.venv/bin/python /root/Scripturi/parcel_density_push.py  "$@" ;;
  list)  exec /root/Scripturi/.venv/bin/python /root/Scripturi/parcel_products_build.py "$@" ;;
  *) echo "uz: parcel_colete.sh learn|push|list [--apply]"; exit 2 ;;
esac
