#!/usr/bin/env bash
# Verifica nr. de colete de pe ultimul AWB vs regula curenta; mail DOAR pe diferente noi.
set -uo pipefail
ENVF=/root/Scripturi/.env.xconnector
export XCONNECTOR_SHOPS="$(sed -n "s/^XCONNECTOR_SHOPS=//p" "$ENVF")"
export SHOPIFY_ADMIN_TOKENS="$(sed -n "s/^SHOPIFY_ADMIN_TOKENS=//p" "$ENVF")"
export KB_DATABASE_URL="$(grep -m1 "^KB_DATABASE_URL=" /root/Scripturi/.env | cut -d= -f2-)"
cd /root/Scripturi
exec /root/Scripturi/.venv/bin/python /root/Scripturi/parcel_count_watch.py "$@"
