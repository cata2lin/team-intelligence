#!/bin/bash
# Reimprospatare fisa CS din AWBprint (AWB/curier/status/link tracking) prin Custom Connector.
# Geam de 7 zile: dupa 14 zile doar 0,9% din comenzi isi mai schimba statusul (masurat 17-aug-2026).
# Push-ul e idempotent + jurnal de semnaturi => se trimit DOAR comenzile care s-au schimbat
# (AWB anulat/refacut, status nou). Rulare repetata = ieftina.
# NU face `. .env` — o linie din el strica bash-source; extragem doar ce ne trebuie.
export DATABASE_URL_AWBPRINT="$(grep -m1 ^DATABASE_URL_AWBPRINT= /root/Scripturi/.env | cut -d= -f2-)"
export PYTHONUNBUFFERED=1
export PATH=/root/.local/bin:$PATH
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/richpanel-export || exit 1
SINCE=$(date -d "7 days ago" +%Y-%m-%d)
echo "=== $(date -Is) push --since $SINCE"
exec uv run rp.py push --since "$SINCE" --limit 40000 --rate 3 --apply
