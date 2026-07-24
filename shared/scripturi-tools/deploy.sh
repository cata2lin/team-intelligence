#!/bin/bash
# deploy.sh — deploy GIT-DRIVEN al tool-urilor pe VPS, SIGUR prin construcție.
#
# DE CE: până acum tool-urile se copiau flat cu scp manual → drift tăcut git↔VPS (2 bombe într-o zi,
# 24-iul). Ăsta le aduce din git (origin/main) cu o singură comandă versionată, verificată, cu backup.
#
# Siguranță:
#  - fișierele flat se sincronizează prin deploy_parity.py (face .bak la fiecare fișier schimbat);
#  - checkout-ul se aduce la zi cu `pull --ff-only` → NU poate șterge modificări locale (dacă are, SARE);
#  - dry-run implicit; scrie DOAR cu --apply.
#
# Rulare (local):  ssh <vps> 'bash /root/Scripturi/deploy.sh --apply'
#   sau pe VPS:     bash /root/Scripturi/deploy.sh            # dry-run: arată ce s-ar schimba
#                   bash /root/Scripturi/deploy.sh --apply    # aplică
set -uo pipefail
D=/root/Scripturi/team-intelligence
PY=/root/Scripturi/.venv/bin/python
APPLY="${1:-}"

echo "=== deploy $(date -u '+%F %T') UTC ==="
git -C "$D" fetch -q origin || { echo "✗ git fetch a eșuat"; exit 1; }

if [ "$APPLY" != "--apply" ]; then
  echo "-- DRY-RUN: ce fișiere flat diferă de origin/main --"
  $PY /root/Scripturi/deploy_parity.py check
  echo "(dry-run — rulează cu --apply ca să sincronizezi)"
  exit 0
fi

# 1) sincronizează fișierele flat din origin/main (cu .bak per fișier)
echo "-- sync fișiere flat (git→VPS, cu .bak) --"
$PY /root/Scripturi/deploy_parity.py deploy --apply

# 2) adu checkout-ul la zi — ff-only = imposibil să piardă mods locale; sare curat dacă nu poate
echo "-- checkout team-intelligence --"
if git -C "$D" pull --ff-only origin main >/dev/null 2>&1; then
  echo "  ✓ ff la origin/main ($(git -C "$D" rev-parse --short HEAD))"
else
  echo "  ⚠ SKIP (checkout are modificări locale) — reconciliază manual; fișierele flat sunt oricum sincronizate"
fi

# 3) heartbeat (ca data_health să știe că deploy-ul a rulat)
$PY /root/Scripturi/heartbeat.py deploy --interval-min 100000 --note "deploy.sh manual" >/dev/null 2>&1 || true
echo "=== deploy gata ==="
