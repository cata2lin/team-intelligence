---
name: gigi:awb-fast
description: Trimite eticheta AWB a unei comenzi ARONA pe grupul WhatsApp „AWB" — luată din ORDER HUB, nu din xConnector. Folosește când cineva zice „trimite awb pentru X pe whatsapp", „trimite eticheta pe grup", „pune AWB-ul pe WhatsApp". Pentru „refă AWB-ul cu N colete", „câte colete are comanda", „anulează AWB-ul" NU mai e aici: trec la gigi:order-hub (acțiunile refacere / comanda / oprire).
category: fulfillment
version: 2
---

# Eticheta AWB pe grupul WhatsApp „AWB"

> Autor: gigi. **Rescris pe 21-oct-2026**: calea veche, prin xConnector, a murit.

## When to use
- „trimite awb pentru GRAND000001 pe whatsapp", „pune eticheta pe grup", „trimite etichetele pentru X, Y, Z".
- NU pentru refacere / numărul de colete / anulare → **`gigi:order-hub`** (`refacere`, `comanda`, `oprire`).
  Acolo sunt gărzile, lacătele, plafoanele și istoricul; aici e doar trimiterea documentului.

## Steps

```bash
$TEAM_REPO/plugins/gigi/skills/awb-fast/trimite_eticheta_wa.sh --proba GRAND000001   # arată, nu trimite
$TEAM_REPO/plugins/gigi/skills/awb-fast/trimite_eticheta_wa.sh GRAND000001 EST000001 # trimite
```

1. **Întâi `--proba`.** Spune ce etichetă a găsit (comandă · AWB · octeți · pagini) fără să trimită nimic.
   Verifică: AWB-ul e cel pe care-l aștepți, iar **paginile sunt cel puțin câte coletele** comenzii — altfel
   depozitul primește o singură etichetă pentru mai multe cutii. Numărul de colete îl vezi în fișa din
   `gigi:order-hub` (`comanda`).
2. **Apoi fără `--proba`.** Confirmarea VALIDĂ e linia `SENT doc -> 120363418826898523@g.us`. **Ieșire goală
   NU înseamnă trimis** (vezi capcanele).
3. Pe grup se pune **DOAR documentul**, fără niciun mesaj — regula ownerului. Dacă ai trimis greșit, nu scrie
   o corecție pe grup: trimite documentul CORECT, o singură dată.

## De unde vine eticheta (și de ce nu mai din xConnector)

- Etichetele se fac acum în **Order Hub**, pe conturile lui (DPD, Dragon Star, FAN, Sameday). PDF-ul stă în
  `etichete_pdf`, iar scriptul ia **`pdf_gata`** — versiunea cu banda de produse de sub etichetă, cea pe care
  o citește depozitul ca fișă de cules (`coalesce(pdf_gata, pdf)`: dacă banda nu e desenată încă, ia PDF-ul brut).
- Alege eticheta **VIE și cea mai nouă** a comenzii (`shipments.awb` = `etichete_pdf.awb`, fulfillment neanulat).
  Așa, după o refacere, pleacă AWB-ul nou, nu cel aruncat. Dacă nu găsește nimic, o spune și NU trimite.
- Trei pași, fiindcă nicio mașină nu are ambele capete: Postgres-ul OH ascultă doar pe `127.0.0.1` pe boxul lui
  (161.97), iar bridge-ul WhatsApp (baileys) rulează pe boxul scanerului (84.46), care n-are cheie ssh spre OH.
  Scriptul se rulează **de pe mașina operatorului**, care are ssh la amândouă. Override: `OH_HOST`,
  `SCANER_HOST`, `WA_AWB_GROUP`.

## Capcane care au costat timp

- ⛔ **`awb.sh send|check|regen` (v1) NU mai merge.** Trecea prin `xconnector.py resolve_order`, iar xConnector
  s-a dezinstalat pe 19-oct-2026: răspunde **401** (`xConnector getOrders a picat la pagina 0 … status 401`).
  Nu-l repara — `check`/`regen` sunt acum acțiuni în `gigi:order-hub`, cu gărzi.
- **Ieșire goală ≠ trimis.** Singura confirmare e `SENT doc -> <jid>` din `wa_send.js`. Scriptul ți-o spune
  explicit când lipsește.
- **Nu număra paginile din PDF cu `strings`/`grep '/Type /Page'`**: fluxurile sunt comprimate, deci dă 0 pe un
  PDF valid (măsurat pe o etichetă de 2 pagini). Numărul corect e `etichete_pdf.pagini`, scris la descărcare.
- **Dragon Star:** eticheta are 2 pagini pe un singur colet. Nu deduce coletele din pagini.
- **Nu trimite eticheta unei comenzi refăcute fără să scoți hârtia veche din teanc.** Dacă vechea etichetă era
  printată, cineva trebuie s-o arunce; altfel coletul pleacă pe o etichetă moartă (283 de colete facturate de
  DPD în septembrie, 111 în octombrie până pe 18). `gigi:order-hub refacere` îți spune în plan dacă era printată.
- Descărcarea unei etichete din coada de print o marchează `downloaded` → **iese din coada depozitului**.
  Scriptul ăsta NU descarcă din coadă: citește PDF-ul deja luat, deci nu mișcă nimic.

## Grupul WhatsApp
`AWB` = `120363418826898523@g.us` (override `WA_AWB_GROUP`). Bridge: `/root/wa-bridge/wa_send.js` pe 84.46
(baileys). Reconectare prin QR: vezi memoria `wa-bridge-vps`.

## Fișiere
| Cale | Ce e |
|---|---|
| `$TEAM_REPO/plugins/gigi/skills/awb-fast/trimite_eticheta_wa.sh` | scriptul (OH → bridge), cu `--proba` |
| `84.46:/root/wa-bridge/wa_send.js` | bridge-ul WhatsApp (baileys) |
| `161.97:order_hub.etichete_pdf` | PDF-urile etichetelor (`pdf_gata` = cu banda de produse) |

Rudă: **`gigi:order-hub`** (fișa comenzii, refacere, oprire, anulare), `depozit:print-queue` (printul pe stație).
