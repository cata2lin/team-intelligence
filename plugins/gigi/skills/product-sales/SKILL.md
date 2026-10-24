---
name: product-sales
description: Câte BUCĂȚI a vândut fiecare PRODUS, pe orice magazin(e) Arona Shopify, pe orice perioadă — clasament al celor mai vândute / cel mai puțin vândute N produse, scos direct într-un Google Sheet partajat (anyone-with-link). Date din Order Hub (comenzi plasate, instant) by default, sau live din Shopify (cu venit) (100%, mai lent) cu --source shopify — NU din metrics warehouse, fiindcă warehouse-ul orders/order_line_items poate fi incomplet pe unele branduri (observat: GT ~15% comenzi lipsă), deci pentru top/bottom-sellers cifrele de warehouse pot fi greșite. Per produs: bucăți gross (cantitate comandată) + net (după anulări/refund/editări) + comenzi + venit. Combinat pe mai multe magazine sau separat pe fiecare. Folosește pentru „cele mai vândute / cele mai puțin vândute produse", „top sellers", „worst sellers", „dead stock pe vânzări", „bucăți vândute per produs", „vânzări per produs", „best/worst sellers Esteban/GT/Nubra", „ce parfumuri se vând prost", „clasament produse pe bucăți". Triggers: vanzari per produs, bucati vandute, top sellers, worst sellers, cele mai vandute, cele mai putin vandute, dead stock, slow movers, product sales ranking, units sold.
---

# Product sales — bucăți vândute per produs (orice magazin, orice perioadă)

Răspunde direct la „care sunt cele mai / cel mai puțin vândute N produse pe magazinul X
în ultimele Y luni" și lasă rezultatul într-un **Google Sheet partajat** (oricine cu
link-ul poate edita → merge și pentru restul echipei, deși Sheet-ul e creat sub contul
celui logat).

## Sursa de date (`--source`, default `oh`)
NU folosi metrics warehouse (`public.orders`/`order_line_items`) — e **incomplet**:
pe 18 mar–18 iun 2026 GT avea 11.367 comenzi în warehouse vs **13.616** real (≈15%
lipsă, confirmat de numerotarea GT31433→GT44857 ≈ 13.400). Asta strică un clasament
de worst-sellers. Skill-ul are două surse bune:

| `--source` | De unde | Completitudine | Viteză |
|---|---|---|---|
| **`oh`** (default) | **Order Hub** `/api/cs/vanzari-pe-sku` (plasate, fără demo, ziua de București) | ~100% (incl. marketplace-urile locale) | instant (~10s) |
| `awb` | AWBprint `orders.line_items` — **doar pe server**, întoarcere explicită (`--source awb` sau env `PRODUCT_SALES_SURSA=awb`) | ~99% | instant |
| `shopify` | Shopify Admin API live (status:any, sare VOIDED neanulate) | 100% | lent (~25 min pt Esteban, throttling) |

- **oh** (din 24-oct-2026): `gross` = toate bucățile plasate (cu anulate), `net` = fără comenzile
  ANULATE. **Refuzurile rămân în `net`** (AWBprint le scotea) → net OH e ~1–4% mai mare decât net awb
  (sep-2026: EST 101.605 vs 99.692, GT 20.704 vs 19.887; gross identic ±0,1%: EST 110.332 vs 110.323,
  GRAN 6.444 vs 6.444). **Venitul pe produs NU există în OH** → coloana Venit = 0; pt venit, `--source shopify`.
  Fereastra: cel mult 400 de zile în urmă; `--to` e inclus.
- **awb**: SKU din `inventory_item.sku`, titlul rezolvat din catalogul Shopify (un singur
  pull de produse, rapid). `net` = exclude comenzile anulate/refuzate/întoarse
  (`cancelled/refused/back_to_sender/returning_to_sender`). Lipsește ~1% (comenzi
  neajunse încă la fulfillment / anulate înainte de AWB / ultima zi).
- **shopify**: 100% exact (inclusiv ultimele ore), `net` = `currentQuantity`. Folosește-l
  când vrei cifre decision-grade la produsele cu volum mic (acolo, diferența de ~1% din awb
  e proporțional mai mare). E ce face și `api/product_analytics.py` din Scripturi.

## Metrice
- **gross** = `sum(lineItem.quantity)` — cantitatea comandată (qty_sold clasic). Implicit ordonez după asta.
- **net** = `sum(lineItem.currentQuantity)` — după anulări / refund / editări de comandă (mai aproape de „chiar a rămas vândut").
- comenzi (câte comenzi distincte conțin produsul), venit (după discounturi: 2+1, coduri).

## Cum rulezi
```bash
# cele mai PUȚIN vândute 40 parfumuri, Esteban + GT combinat, ultimele 3 luni → Google Sheet
# (default --source oh = instant, ~11s)
uv run product_sales.py --stores EST,GT --months 3 --order bottom --limit 40

# același lucru dar 100% exact (live Shopify, mai lent)
uv run product_sales.py --stores EST,GT --months 3 --order bottom --limit 40 --source shopify

# cele mai VÂNDUTE 20, doar Esteban, interval fix
uv run product_sales.py --stores EST --from 2026-01-01 --to 2026-03-31 --order top --limit 20

# liste separate pe fiecare magazin (40 EST + 40 GT)
uv run product_sales.py --stores EST,GT --scope per-store --order bottom --limit 40

# ordonează după net (exclude anulate), doar print în terminal (fără Sheet)
uv run product_sales.py --stores GT --metric net --no-sheet

# scrie într-un Sheet existent (partajat deja) în loc să creeze unul nou
uv run product_sales.py --stores EST,GT --sheet-id <SPREADSHEET_ID>
```

### Parametri
| Flag | Default | Ce face |
|---|---|---|
| `--stores` | `EST,GT` | prefixe din stores.csv (ex. `EST,GT,NUB`) |
| `--months` | `3` | fereastra = azi − N luni (suprascris de `--from/--to`) |
| `--from` / `--to` | — / azi | interval fix `YYYY-MM-DD` |
| `--order` | `bottom` | `bottom` = cele mai puțin vândute, `top` = cele mai vândute |
| `--limit` | `40` | câte produse |
| `--scope` | `combined` | `combined` = o listă din toate magazinele; `per-store` = câte una pe fiecare |
| `--metric` | `gross` | după ce ordonez (`gross` sau `net`) |
| `--source` | `oh` | `oh` = Order Hub (plasate, fără venit); `awb` = AWBprint, doar server; `shopify` = live 100% lent, cu venit |
| `--exclude` | regex cutii/mostre/testere/carduri | exclude non-parfumuri (loghează ce a sărit) |
| `--no-exclude` | — | nu exclude nimic |
| `--sheet-id` | — | scrie într-un Sheet existent în loc să creeze |
| `--no-sheet` | — | doar print în terminal |
| `--json PATH` | — | salvează clasamentul complet ca JSON |

## Plumbing (de unde vin credențialele)
- **Order Hub** (sursa default): cheia `OH_TEAM_READ_TOKEN` (env, apoi KB), prin `plugins/core/scripts/oh_citire.py`. Magazinul = domeniul myshopify din `stores.csv`. Fără cheie / OH căzut = eroare, nu cifra veche.
- **AWBprint** (`--source awb`, doar pe server): secret KB `DATABASE_URL_AWBPRINT`. Mapare prefix→nume magazin în `PREFIX_TO_STORE`.
- **Tokeni Shopify** (pt titluri în awb, și pt pull-ul full în shopify): `stores.csv` — env `SHOPIFY_STORES_CSV`, apoi `./stores.csv`, apoi secret KB `SHOPIFY_STORES_CSV`. (vezi `gigi:shopify-stores`)
- **Google Sheet**: tokenul OAuth personal din KB `GOOGLE_OAUTH_TOKEN_JSON` (scopes `spreadsheets` + `drive.file`). Creează Sheet-ul în Drive-ul celui logat și îl face **anyone-with-link → editor** via Drive API. Dacă rulează alt coleg, Sheet-ul tot sub contul din KB se creează, dar link-ul e editabil de oricine.

## Capcane
- Default `--source oh` e instant (~10s). Doar `--source shopify` e lent: **Esteban e mare** (~60k comenzi / 3 luni) → câteva minute (throttling Shopify).
- La worst-sellers cu volum mic (1–5 buc), diferența de ~1% dintre oh și shopify poate schimba ordinea exactă; pt decizii fine de discontinuare, rulează `--source shopify`.
- Un produs poate avea mai multe SKU-uri (mărimi) — grupez pe `product.id`, nu pe SKU.
- Combinat pe magazine cu volume diferite, „cele mai puțin vândute" e dominat de magazinul mic (ex. GT vs Esteban) — folosește `--scope per-store` ca să compari corect.
- `--exclude` scoate implicit cutii cadou / mostre / testere / carduri (loghează ce a sărit); pune `--no-exclude` dacă vrei chiar tot.
