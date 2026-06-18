---
name: product-sales
description: Câte BUCĂȚI a vândut fiecare PRODUS, pe orice magazin(e) Arona Shopify, pe orice perioadă — clasament al celor mai vândute / cel mai puțin vândute N produse, scos direct într-un Google Sheet partajat (anyone-with-link). Trage LIVE din Shopify (autoritativ, complet) — NU din metrics warehouse, fiindcă warehouse-ul orders/order_line_items poate fi incomplet pe unele branduri (observat: GT ~15% comenzi lipsă), deci pentru top/bottom-sellers cifrele de warehouse pot fi greșite. Per produs: bucăți gross (cantitate comandată) + net (după anulări/refund/editări) + comenzi + venit. Combinat pe mai multe magazine sau separat pe fiecare. Folosește pentru „cele mai vândute / cele mai puțin vândute produse", „top sellers", „worst sellers", „dead stock pe vânzări", „bucăți vândute per produs", „vânzări per produs", „best/worst sellers Esteban/GT/Nubra", „ce parfumuri se vând prost", „clasament produse pe bucăți". Triggers: vanzari per produs, bucati vandute, top sellers, worst sellers, cele mai vandute, cele mai putin vandute, dead stock, slow movers, product sales ranking, units sold.
---

# Product sales — bucăți vândute per produs (orice magazin, orice perioadă)

Răspunde direct la „care sunt cele mai / cel mai puțin vândute N produse pe magazinul X
în ultimele Y luni" și lasă rezultatul într-un **Google Sheet partajat** (oricine cu
link-ul poate edita → merge și pentru restul echipei, deși Sheet-ul e creat sub contul
celui logat).

## De ce LIVE din Shopify, nu din warehouse
`metrics.order_line_items` / `orders` (sursa folosită de alte skill-uri) poate fi
**incomplet** pentru unele branduri — măsurat pe 18 mar–18 iun 2026, GT avea ~11.367
comenzi în warehouse vs **13.616** live (≈15% lipsă; confirmat și de numerotarea
comenzilor GT31433→GT44857 ≈ 13.400). Pentru un clasament de „cele mai puțin vândute"
asta schimbă lista. Deci skill-ul citește **direct din Shopify Admin API** (status:any,
sare doar comenzile VOIDED neanulate), cum face și `api/product_analytics.py` din Scripturi.

## Metrice
- **gross** = `sum(lineItem.quantity)` — cantitatea comandată (qty_sold clasic). Implicit ordonez după asta.
- **net** = `sum(lineItem.currentQuantity)` — după anulări / refund / editări de comandă (mai aproape de „chiar a rămas vândut").
- comenzi (câte comenzi distincte conțin produsul), venit (după discounturi: 2+1, coduri).

## Cum rulezi
```bash
# cele mai PUȚIN vândute 40 parfumuri, Esteban + GT combinat, ultimele 3 luni → Google Sheet
uv run product_sales.py --stores EST,GT --months 3 --order bottom --limit 40

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
| `--exclude` | regex cutii/mostre/testere/carduri | exclude non-parfumuri (loghează ce a sărit) |
| `--no-exclude` | — | nu exclude nimic |
| `--sheet-id` | — | scrie într-un Sheet existent în loc să creeze |
| `--no-sheet` | — | doar print în terminal |
| `--json PATH` | — | salvează clasamentul complet ca JSON |

## Plumbing (de unde vin credențialele)
- **Tokeni Shopify**: `stores.csv` — env `SHOPIFY_STORES_CSV`, apoi `./stores.csv`, apoi secret KB `SHOPIFY_STORES_CSV`. (vezi `gigi:shopify-stores`)
- **Google Sheet**: tokenul OAuth personal din KB `GOOGLE_OAUTH_TOKEN_JSON` (scopes `spreadsheets` + `drive.file`). Creează Sheet-ul în Drive-ul celui logat și îl face **anyone-with-link → editor** via Drive API. Dacă rulează alt coleg, Sheet-ul tot sub contul din KB se creează, dar link-ul e editabil de oricine.

## Capcane
- **Esteban e mare** (~60k comenzi / 3 luni) → pull-ul live durează câteva minute (throttling Shopify). E normal; merge în background.
- Un produs poate avea mai multe SKU-uri (mărimi) — grupez pe `product.id`, nu pe SKU.
- Combinat pe magazine cu volume diferite, „cele mai puțin vândute" e dominat de magazinul mic (ex. GT vs Esteban) — folosește `--scope per-store` ca să compari corect.
- `--exclude` scoate implicit cutii cadou / mostre / testere / carduri (loghează ce a sărit); pune `--no-exclude` dacă vrei chiar tot.
