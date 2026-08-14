#!/usr/bin/env bash
set -uo pipefail
ENVF=/root/Scripturi/.env.xconnector
export XCONNECTOR_SHOPS="$(sed -n 's/^XCONNECTOR_SHOPS=//p' "$ENVF")"
export SHOPIFY_ADMIN_TOKENS="$(sed -n 's/^SHOPIFY_ADMIN_TOKENS=//p' "$ENVF")"
export SHOPIFY_STORES_CSV=/root/Scripturi/stores.csv
export KB_DATABASE_URL="$(grep -m1 '^KB_DATABASE_URL=' /root/Scripturi/.env | cut -d= -f2-)"
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector
/root/Scripturi/.venv/bin/python /root/Scripturi/sku_box_map_build.py "$@"
