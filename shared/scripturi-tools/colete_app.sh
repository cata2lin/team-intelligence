#!/usr/bin/env bash
# /colete (pagina depozit "cate bucati intra intr-un colet"). uvicorn pe 127.0.0.1:8091, sub pm2 `colete`.
# Env-ul de Shopify e necesar fiindca pagina scrie metafield-ul custom.nr_cutii DIRECT la salvare
# (aceleasi surse ca parcel_colete.sh — fara ele salvarea ar merge doar local, nu si in Shopify).
set -uo pipefail
cd /root/Scripturi
ENVF=/root/Scripturi/.env.xconnector
export XCONNECTOR_SHOPS="$(sed -n "s/^XCONNECTOR_SHOPS=//p" "$ENVF")"
export SHOPIFY_ADMIN_TOKENS="$(sed -n "s/^SHOPIFY_ADMIN_TOKENS=//p" "$ENVF")"
export SHOPIFY_STORES_CSV=/root/Scripturi/stores.csv
export KB_DATABASE_URL="$(grep -m1 "^KB_DATABASE_URL=" /root/Scripturi/.env | cut -d= -f2-)"
exec /root/Scripturi/.venv/bin/python -m uvicorn parcel_density_app:app --host 127.0.0.1 --port 8091
