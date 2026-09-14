#!/bin/bash
# Oglinda CS: capteaza din Richpanel firul complet (mesaje client + raspunsuri agent + note
# + atasamente), pe TOATE canalele — inclusiv email. DOAR CITIRE; nu raspunde, nu modifica.
#
# DE CE ZILNIC, NU SAPTAMANAL (masurat 31-aug-2026): comentariile FB se sterg repede.
#   tras la 3 zile -> 57,1% deja "This message was deleted"
#   tras in aceeasi zi -> 14,4%
# 43 puncte procentuale de continut mor intre ziua 0 si ziua 3, deci regula de inghet
# n-ar avea ce ingheta la o rulare saptamanala.
#
# NU face `. .env` — o linie din el strica bash-source; extragem doar ce ne trebuie.
export RICHPANEL_MCP_TOKEN="$(grep -m1 "^RICHPANEL_MCP_TOKEN=" /root/Scripturi/.env | cut -d= -f2-)"
export CS_MIRROR_DB=/root/Scripturi/data/cs_mirror.db
export PYTHONUNBUFFERED=1
export PATH=/root/.local/bin:$PATH
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/richpanel-export || exit 1
echo "=== $(date -Is) captare (fereastra 8 zile)"
# ⚠️ DE CE 8, NU 3: fereastra filtreaza pe DATA CREARII tichetului, dar raspunsurile agentilor vin
# peste zile. Masurat 02-sep: 28 raspunsuri pierdute, TOATE pe tichete create pe 30-aug si
# raspunse pe 01-sep — ziua 30-aug iesise deja din fereastra. In plus `--recent N` numara
# INCLUSIV ziua curenta, care la 02:00 e goala, deci "3" insemna de fapt 2 zile utile.
# Costul e mic: firele nemodificate se sar (masurat: 223 din 601 sarite intr-o zi).
# noaptea rata Richpanel e libera; 45/min lasa rezerva CS-ului de garda
uv run rp_sync.py --recent 8 --max-rpm 45
echo "=== $(date -Is) captare EMAIL (cutiile Google, READ-ONLY)"
# kb.py are nevoie de KB_DATABASE_URL ca sa scoata service account-ul cu delegare
export KB_DATABASE_URL="$(grep -m1 '^KB_DATABASE_URL=' /root/Scripturi/.env | cut -d= -f2-)"
uv run gmail_sync.py --recent 3
echo "=== $(date -Is) reconciliere (ce e in cutie dar NU in Richpanel)"
uv run gmail_sync.py --reconcile --days 3
echo "=== $(date -Is) paritate"
uv run parity_check.py --days 7
RC=$?
echo "=== $(date -Is) gata (paritate rc=$RC)"
exit $RC
