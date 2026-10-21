#!/usr/bin/env bash
# Trimite pe grupul WhatsApp „AWB" eticheta UNEI comenzi (sau a mai multora), luată din ORDER HUB.
#
# De ce există: calea veche (`awb.sh send`, prin xConnector) a MURIT pe 19-oct-2026, când s-a dezinstalat
# xConnector — răspunde `401` la `getOrders`, deci skill-ul nu mai putea trimite nimic. Etichetele se fac acum
# în Order Hub (inclusiv pe Dragon Star, care n-a fost niciodată în xConnector), iar PDF-ul lor stă în
# `etichete_pdf`, în versiunea `pdf_gata` — cea cu banda de produse pe care o citește depozitul.
#
# Trei pași, fiindcă nicio mașină nu are acces la amândouă capetele: Postgres-ul OH ascultă doar pe
# 127.0.0.1 pe boxul lui, iar bridge-ul WhatsApp (baileys) rulează pe boxul scanerului, care n-are cheie ssh
# spre OH. Scriptul se rulează de pe mașina operatorului, care are ssh la ambele.
#
#   ./trimite_eticheta_wa.sh GRAND000001 [EST000001 ...]      # trimite
#   ./trimite_eticheta_wa.sh --proba GRAND000001              # arată ce ar trimite, NU trimite
#
# Pe grup se pune DOAR documentul, fără mesaj (regula ownerului: „pe grup doar documentul"). Confirmarea
# validă e linia `SENT doc -> <jid>` de la bridge; ieșirea goală NU înseamnă trimis.
set -uo pipefail

OH=${OH_HOST:-root@161.97.69.226}
SCANER=${SCANER_HOST:-root@84.46.242.181}
GRUP=${WA_AWB_GROUP:-120363418826898523@g.us}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

PROBA=0
if [ "${1:-}" = "--proba" ]; then PROBA=1; shift; fi
if [ $# -eq 0 ]; then
  echo "Folosire: $0 [--proba] COMANDA [COMANDA ...]" >&2
  exit 2
fi

for CMD in "$@"; do
  # eticheta VIE a comenzii, cu PDF-ul ei: cea mai nouă, neanulată, cu rând în `etichete_pdf`
  read -r SHID AWB PAGINI < <(ssh -o ConnectTimeout=30 "$OH" "su - postgres -c \"psql -d order_hub -t -A -F' ' -c \\\"
      SELECT e.shipment_id, e.awb, coalesce(e.pagini, 0)
        FROM etichete_pdf e
        JOIN shipments s ON s.id = e.shipment_id
        JOIN orders o ON o.id = s.order_id
       WHERE o.name = '$CMD'
         AND coalesce(trim(s.awb), '') = trim(e.awb)
         AND upper(coalesce(s.courier_specific_data::jsonb->>'ff_status','')) <> 'CANCELLED'
       ORDER BY s.id DESC LIMIT 1\\\"\"" 2>/dev/null)

  if [ -z "${AWB:-}" ]; then
    echo "$CMD: NICIO etichetă vie cu PDF în Order Hub — verifică fișa comenzii (gigi:order-hub comanda)" >&2
    continue
  fi

  FIS="$TMP/$CMD-$AWB.pdf"
  ssh -o ConnectTimeout=30 "$OH" "su - postgres -c \"psql -d order_hub -t -A -c \\\"
      SELECT encode(coalesce(pdf_gata, pdf), 'base64') FROM etichete_pdf WHERE shipment_id = $SHID\\\"\" | tr -d '\n'" \
    | base64 -d > "$FIS" 2>/dev/null

  if [ ! -s "$FIS" ] || ! head -c 4 "$FIS" | grep -q '%PDF'; then
    echo "$CMD: PDF-ul din OH e gol sau nu e PDF (shipment $SHID) — nu trimit" >&2
    continue
  fi
  # Paginile: le ia din `etichete_pdf.pagini`, scris la descărcarea etichetei. NU le număr din PDF cu `strings`
  # — fluxurile de pagini sunt comprimate, deci căutarea după `/Type /Page` dă 0 pe un PDF perfect valid (am
  # încercat; raporta 0 pentru o etichetă de 2 pagini). Verificarea „atâtea pagini câte colete" rămâne a omului.
  echo "$CMD · awb $AWB · $(wc -c < "$FIS") octeți · $PAGINI pagini"

  if [ "$PROBA" = "1" ]; then
    echo "   (probă: nu trimit)"
    continue
  fi
  scp -q -o ConnectTimeout=30 "$FIS" "$SCANER:/tmp/$CMD-$AWB.pdf" || { echo "$CMD: scp a picat" >&2; continue; }
  ssh -o ConnectTimeout=120 "$SCANER" "cd /root/wa-bridge && timeout 180 node wa_send.js '$GRUP' doc '/tmp/$CMD-$AWB.pdf'" \
    | tee "$TMP/out" | tail -2
  grep -q "^SENT doc" "$TMP/out" || echo "$CMD: ATENȚIE — bridge-ul n-a confirmat 'SENT doc'; NU presupune că a plecat" >&2
done
