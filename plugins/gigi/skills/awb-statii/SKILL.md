---
name: gigi:awb-statii
description: "Etichetele AWB care NU AU PLECAT încă, pentru magazinele SK / HU / ORC (și orice split-store), împărțite pe STAȚII (Bartolomeu / Uzina 2) și livrate ca UN SINGUR PDF per stație pe grupul WhatsApp AWB. Sursa adevărului = xConnector (comenzi + AWB + SKU) + DPD (cine a plecat). Folosește pentru: „trimite AWB-urile SK HU ORC pe stații", „ce n-a plecat", „etichete neplecate", „coadă de printat pe stații", „împarte awb-urile pe Uzina 2 și Bartolomeu"."
category: fulfillment-logistics
version: 2.0.0
---

# AWB-uri neplecate, împărțite pe stații

## 🕖 CRONUL ZILNIC (asta rulează singur, restul e pentru ad-hoc)
```
5 7 * * 1-5  /usr/bin/flock -n /tmp/awb_zilnic.lock /root/Scripturi/awb_zilnic.sh \
             >> /root/Scripturi/logs/awb_zilnic.log 2>&1
```
`awb_zilnic.sh` → `awb_zilnic.py`. Trimite pe grupul AWB (`120363418826898523@g.us`) etichetele de pe
**Bonhaus.hu / Bonhaus.sk / Orice Redus** — magazine fără coadă de print pe stație, deci WhatsApp e
singurul drum spre depozit. Fereastră: 14 zile pe DATA COMENZII (o comandă veche poate primi eticheta azi).

**Din 27-aug-2026 face două lucruri în plus:**
1. **Câte un PDF pe stație** — `AWB-BARTOLOMEU-<n>-<zi>.pdf` + `AWB-UZINA2-<n>-<zi>.pdf`, două mesaje.
2. **Trimite doar ce N-A PLECAT** — cere starea la curier (`awb_track.py --json`) și păstrează doar
   `generated`. Înainte nu filtra deloc: pe 27-aug, **11 din 27 de etichete erau pentru colete deja
   predate curierului**.

**Regula de stații NU e rescrisă în cron — se IMPORTĂ din `print_queue.py`** (`primary`, `category`,
`DEPOZIT_CATS`, `_is_lavete`). Două copii ale regulii = două adevăruri care diverg tăcut.

**Trei alegeri de proiectare, fiecare cu motivul ei:**
- **Filtrul rulează ÎNAINTE de descărcarea etichetei.** Descărcarea marchează `downloaded` în
  xConnector și scoate eticheta din coada de print a stației — n-o atinge pe una pe care n-o trimiți.
- **Fail-OPEN, dar zgomotos.** Tracker mort sau AWB necunoscut ⇒ eticheta pleacă totuși, cu o linie
  `ATENTIE, stare necunoscuta` în log. O etichetă în plus e o supărare; una lipsă e un colet care nu pleacă.
- **Evidența e per LOT, nu globală.** `/root/awb_zilnic_trimise.json` se scrie DUPĂ fiecare trimitere
  reușită, separat pe stație. ⚠️ Înainte, cheile se adăugau *înainte* de trimitere — cu două loturi,
  un eșec ar fi marcat „trimis" și ce n-a plecat, iar etichetele alea nu mai plecau niciodată.

Retrimitere curățată a unui lot deja trimis: `awb_resend.py "HU2050,HU2049,..."` (recere starea la
curier, scoate ce a plecat, trimite două PDF-uri). ⚠️ NU trece prin evidența cronului — dacă retrimiți
comenzi pe care cronul nu le-a trimis încă, mâine le trimite el din nou.

## Ordinea corectă (cuvintele ownerului)
1. **întâi** comenzile neplecate — TOT pool-ul lunii, nu o zi anume
2. **după** împărțite pe categorii → stații
3. **un singur PDF per stație**, fără text pe grup

## Lanțul
```
xConnector (comenzi + AWB + SKU)  →  DPD (status)  →  neplecate  →  categorii  →  2 PDF-uri
```

### 1. Comenzile lunii, din xConnector
```bash
# pe VPS
cd /root/Scripturi && set -a; . .env.xconnector
export KB_DATABASE_URL=$(grep -m1 '^KB_DATABASE_URL=' .env | cut -d= -f2-); set +a
.venv/bin/python xc_dump.py '16w7xv-0w,63e901-2f,oriceredus' 2026-08-01 > xc.csv
```
Dă `order,awb,skus`. Obiectul xConnector are **`skus`** direct (NU `lineItems`) —
plus `dispatched`, `holdStatus`, `documents[].downloaded`.

### 2. Cine n-a plecat — DPD
```bash
# LOCAL (pe VPS lipsesc credentialele DPD)
cut -d, -f2 xc.csv | tail -n +2 > awbs.txt
uv run .../awb-track/awb_track.py --awb-file awbs.txt --json --allow-partial > track.json
# `--allow-partial` = exit 0 chiar dacă rămân AWB-uri NEVERIFICATE. Fără el, de la 2026-08-26
# awb_track iese cu 1 când un colet n-a primit status confirmat de la curier (fail-closed),
# iar un `&&` din pipeline s-ar opri. Verifică `unverified` din JSON înainte să te bazezi pe cifre.
```
Păstrezi doar `status == "generated"` („AWB GENERAT / Shipment data received").
Celelalte: `in_transit`, `delivered`, `returned`.

### 3. Stația — din SKU-ul principal
SK/HU/ORC sunt **split stores**: se împart pe categorie, nu pe magazin.
```python
def bartolomeu(sku):
    u = (sku or "").upper()
    return u.startswith("HA") or "LAVET" in u or bool(re.search(r"\d+-[MS](?:-|$)", u))
# True  -> BARTOLOMEU (depozit)
# False -> UZINA 2
```
(identic cu `_machine_keep` din `print-queue/print_queue.py`)

### 4. PDF-urile
```bash
NO_SEND=1 .venv/bin/python awb_station.py BARTOLOMEU "SK2430,HU2025,..."   # doar construiește
.venv/bin/python awb_station.py UZINA2 "SK2413,ORC1195,..."                # construiește + trimite
```
Numele fișierului duce informația: `BARTOLOMEU-61-AWB-26.08.pdf`.

## ⛔ Ce NU merge (verificat, nu presupus)
| sursă | de ce nu |
|---|---|
| **AWBprint** | `aggregated_status` blocat pe `waiting_for_courier` la 459 comenzi; `is_printed` false la toate; toate câmpurile IDENTICE între plecat și neplecat. Ownerul: „să nu mai apelezi NICIODATĂ la AWBprint pentru asta". |
| **`dispatched` din xConnector** | buggy (ownerul). Marchează `in_transit`-uri ca neplecate și invers — 5 discrepanțe pe HU/ORC. |
| **`not-downloaded`** | e coada de PRINT, nu ce n-a plecat. Iese din coadă când descarci eticheta ⇒ nereproductibil retroactiv. |
| **Shopify** | tokenurile SK și ORC sunt moarte (401). |
| **ecranul xConnector** | e PAGINAT — captura SK arăta 25 din 56. |

## Reguli de livrare (owner)
- **UN PDF per stație**, niciodată câte un fișier pe comandă — „omul de la depozit primea N mesaje
  și se pierdeau etichete în derulare"
- pe grupul AWB **doar eticheta, fără text** (owner, 18-aug)
- fereastra = **luna curentă** (magazinele astea s-au lansat luna asta)

## Rezultat 26-aug-2026
460 comenzi cu AWB în august → 104 neplecate (SK 56, HU 41, ORC 7) →
**BARTOLOMEU 61** (HA-1005 ×30, lavete ×27) · **UZINA 2 43** (oglinzi, fețe de masă, tuburi termo)

## Fișiere
| în skill | pe VPS | ce face |
|---|---|---|
| `awb_zilnic.sh` / `awb_zilnic.py` | `/root/Scripturi/` | **cronul zilnic** (split + filtru) |
| `awb_station.py` | `/root/Scripturi/` | construiește+trimite un PDF pentru o listă de comenzi |
| `awb_resend.py` | `/root/Scripturi/resend.py` | retrimite curățat un lot deja trimis |
| `xc_dump.py` | `/root/Scripturi/` | dump `order,awb,skus` din xConnector |

⚠️ `.env.xconnector` **NU se poate da `source`** — valoarea `XCONNECTOR_SHOPS` e multi-linie și shell-ul
o execută („command not found"). Extrage-o cu `sed -n 's/^XCONNECTOR_SHOPS=//p'`, cum face `awb_zilnic.sh`.
