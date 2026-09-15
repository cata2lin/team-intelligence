#!/usr/bin/env bash
# GARDA DE PORNIRE pentru cs_backlog.sh (VPS).
#
# ⚠️ SCRISĂ, NU INSTALATĂ. Nu o copia pe VPS și nu o pune în crontab decât după desfășurarea
# completă (vezi POARTA.md §3) și numai cu decizia ownerului.
#
# De ce există: /root/Scripturi/cs_backlog.sh buclează pe facebook_feed_comment și
# instagram_comment cu --auto-hide, adică drafturi PUBLICE + ascundere de comentarii. Linia lui
# din crontab e comentată („# PAUZAT"), deci azi nu rulează nimic — dar decomentarea e o singură
# tastă, iar codul de pe VPS e de dinaintea rundei 1 de reparații. Garda asta face ca o
# decomentare accidentală să NU poată drafta public cu cod vechi: rulează poarta pe copia
# DESFĂȘURATĂ și refuză dacă iese ROȘU.
#
# Instalare (DUPĂ desfășurare, decizie de owner):
#   1. copiază fișierul în /root/Scripturi/cs_backlog_garda.sh (chmod 750)
#   2. în cs_backlog.sh, ca PRIMĂ linie executabilă:  . /root/Scripturi/cs_backlog_garda.sh
#   3. abia apoi decomentează linia de cron
set -euo pipefail

POARTA="${CS_POARTA:-/root/Scripturi/poarta_pornire.py}"
ARBORE="${CS_ARBORE:-/root/Scripturi/skills}"   # arborele cu cs-draft-reply/ și cs-photo/
MARCAJ="${CS_POARTA_MARCAJ:-/root/Scripturi/.poarta_verde}"
VALABIL_ORE="${CS_POARTA_VALABIL_ORE:-24}"

garda_refuza() {
  echo "⛔ cs_backlog OPRIT de gardă: $1" >&2
  echo "   Rulează poarta și repar-o înainte să pornești cronul:" >&2
  echo "   uv run $POARTA --root $ARBORE" >&2
  exit 3
}

[ -f "$POARTA" ] || garda_refuza "poarta nu există pe mașina asta ($POARTA) — cod nedesfășurat"

# Marcajul e verdictul PORȚII, nu al omului: se rescrie doar când poarta iese VERDE.
if [ -f "$MARCAJ" ]; then
  varsta_min=$(( ( $(date +%s) - $(stat -c %Y "$MARCAJ") ) / 60 ))
  if [ "$varsta_min" -lt $(( VALABIL_ORE * 60 )) ] && [ "$(cat "$MARCAJ")" = "VERDE" ]; then
    # marcajul mai e valabil, dar codul se poate fi schimbat sub el
    amprenta_acum=$(md5sum "$ARBORE/cs-draft-reply/cs_auto_draft.py" "$ARBORE/cs-photo/cs_photo.py" | md5sum | cut -d' ' -f1)
    amprenta_marcaj=$(sed -n 2p "$MARCAJ" 2>/dev/null || true)
    [ "$amprenta_acum" = "$amprenta_marcaj" ] && return 0 2>/dev/null || exit 0
    echo "ℹ️  codul s-a schimbat de la ultima poartă — re-rulez." >&2
  fi
fi

echo "🚦 rulez poarta de pornire înainte de backlog…" >&2
if uv run "$POARTA" --root "$ARBORE" >/tmp/poarta_cs.log 2>&1; then
  { echo VERDE
    md5sum "$ARBORE/cs-draft-reply/cs_auto_draft.py" "$ARBORE/cs-photo/cs_photo.py" | md5sum | cut -d' ' -f1
  } > "$MARCAJ"
  echo "🟢 poartă VERDE — backlogul poate rula." >&2
else
  cp -f /tmp/poarta_cs.log "${MARCAJ}.rosu" 2>/dev/null || true
  garda_refuza "poarta a ieșit ROȘU (motivele în ${MARCAJ}.rosu)"
fi
