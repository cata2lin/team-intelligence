#!/usr/bin/env bash
# Trimite pe grupul de AWB etichetele facute pe HU / SK / Orice Redus. L-V, o data pe zi.
# Magazinele astea n-au coada de print pe statie, deci WhatsApp e singurul drum spre depozit.
set -euo pipefail
ENVF=/root/Scripturi/.env.xconnector
export XCONNECTOR_SHOPS="$(sed -n 's/^XCONNECTOR_SHOPS=//p' "$ENVF")"
export KB_DATABASE_URL="$(grep -m1 '^KB_DATABASE_URL=' /root/Scripturi/.env | cut -d= -f2-)"
cd /root/Scripturi/xc_preview
exec /usr/bin/python3 /root/Scripturi/awb_zilnic.py "$@"
