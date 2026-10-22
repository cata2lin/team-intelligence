#!/usr/bin/env bash
# BOOTSTRAP de stoc pe Trendyol — deschide ușa, nu ține stocul.
#
# DE CE EXISTĂ: pe Trendyol un produs cu stoc 0 primește `onSale: false`. Conectorul Trendyol din
# stock-sync citește exact flagul ăsta (`isActive = onSale && !archived && !locked && !blacklisted`),
# deci marchează listingul INACTIVE, matcher-ul nu-l leagă de master, alocatorul nu-i dă unități și
# nimic nu-i mai împinge stoc. Blocaj circular: stoc 0 → onSale false → listing INACTIVE → alocare 0.
# Scriptul trimite O SINGURĂ dată felia de 16%, Trendyol pune onSale=true, stock-sync leagă
# listingul la următoarea sincronizare și de atunci ÎL ȚINE EL (`--bootstrap` sare peste ce e deja
# ACTIVE, ca să nu fim doi scriitori pe același câmp).
# Dovedit pe 13-oct-2026 cu n-47 / n-69 / n-89.
set -euo pipefail
cd /root/Scripturi
export KB_DATABASE_URL="$(grep -m1 '^KB_DATABASE_URL=' .env | cut -d= -f2-)"
echo "=== $(date '+%F %T %Z') trendyol stock bootstrap ==="
exec /root/.local/bin/uv run /root/Scripturi/trendyol_stock_push.py --bootstrap "$@"
