---
name: fulfillment-analytics
description: Analitică RAPIDĂ de livrare / vânzări / transport din AWBprint (DB-ul AWB/Frisbo), pe TOATE cele 21 de magazine Arona — Postgres = instant, ~99% complet (mult peste metrics warehouse, care e incomplet). 4 rapoarte (--report) — refuse (rată de refuz/retur per brand|curier|produs, din line_items × aggregated_status), sales (venit + comenzi + bucăți per brand, sau --daily pe zi), transport (cost REAL de curier per brand×curier, avg/colet, % din venit), stuck (colete blocate in_transit/pending de > N zile + „ghost" = AWB emis dar nescanat). Opțional scrie un Google Sheet partajat. Folosește pentru „rată de refuz", „COD refusal", „ce brand/produs/curier se refuză cel mai mult", „cât pierdem din retururi", „venit pe brand azi/luna asta", „vânzări pe zi", „cost real de transport", „cât mănâncă transportul din marjă", „DPD vs Sameday cost", „colete blocate", „ghost shipments", „comenzi stuck in transit". Triggers: refuz, retur, COD refusal, deliverability, livrabilitate, refuse rate, transport cost, cost curier, vanzari pe brand, venit zilnic, daily sales, stuck shipments, colete blocate, ghost shipment, AWBprint, Frisbo.
---

# Fulfillment analytics (AWBprint — rapid, toate magazinele)

Date instant din **AWBprint** (DB AWB/Frisbo, secret KB `DATABASE_URL_AWBPRINT`). De ce
nu warehouse: `metrics.orders/order_line_items` e incomplet (GT ~15% comenzi lipsă);
AWBprint e ~99% și instant (Postgres). E sora „pe livrare" a lui `gigi:product-sales`.

## Rapoarte (`--report`)
```bash
# rată de refuz/retur per brand (toate magazinele), ultimele 3 luni
uv run fulfillment_analytics.py --report refuse --by brand --months 3

# per curier (DPD/Econt/Sameday/Packeta), ultimele 30 zile
uv run fulfillment_analytics.py --report refuse --by courier --days 30

# per PRODUS (cere --stores ca să rezolve titlul din Shopify)
uv run fulfillment_analytics.py --report refuse --by product --stores EST,GT --limit 30

# venit + comenzi + bucăți per brand (luna asta), sau pe zi
uv run fulfillment_analytics.py --report sales --months 1
uv run fulfillment_analytics.py --report sales --stores EST --days 30 --daily

# cost REAL de transport per brand × curier
uv run fulfillment_analytics.py --report transport --months 1

# colete blocate de > 7 zile (in_transit/pending) + ghost (AWB emis, nescanat)
uv run fulfillment_analytics.py --report stuck --days 7 --limit 50

# orice raport + un Google Sheet partajat:
uv run fulfillment_analytics.py --report refuse --by brand --months 3 --sheet
```

### Parametri
| Flag | Default | Ce face |
|---|---|---|
| `--report` | `refuse` | `refuse` / `sales` / `transport` / `stuck` |
| `--by` | `brand` | doar la `refuse`: `brand` / `courier` / `product` |
| `--stores` | toate | prefixe (EST,GT) → mapate la magazine; obligatoriu la `refuse --by product` pt titluri |
| `--months` / `--days` | 3 luni | fereastra; la `stuck`, `--days` = pragul de vechime |
| `--from` / `--to` | — / azi | interval fix `YYYY-MM-DD` |
| `--daily` | — | `sales`: defalcat pe zi |
| `--limit` | 40 | câte rânduri |
| `--sheet` | — | scrie și un Google Sheet partajat (anyone-with-link) |

## Cum se calculează refuzul (canonic, din deliverability_calculation_reference.md)
Bucket-uri din `aggregated_status`:
- **DELIVERED** = delivered, customer_pickup
- **RETURNED** = back_to_sender, returning_to_sender, incorrect_address, lost
- **REFUSED** = refused, unsuccessful_delivery
- **IN_TRANSIT** = in_transit, fulfilled, redirected, deferred_delivery, on_hold, out_for_delivery
- **PENDING** = waiting_for_courier, not_fulfilled, new, ready_for_pickup, not_created, created_awb

`refuz % = (RETURNED + REFUSED) / (DELIVERED + RETURNED + REFUSED)` — DOAR pe comenzile
**rezolvate** (livrate sau întoarse); cele în tranzit/pending NU intră în numitor (altfel
rata iese fals mică). La produs, pragul minim e 5 comenzi rezolvate (anti-zgomot).

## Capcane
- `transport_cost` e populat ~81% (doar comenzile cu CSV de curier importat) — `cost/colet`
  se calculează doar pe cele cu cost > 0.
- `courier_name` are variante (`DPD` vs `dpd_ro`) și ~5% gol → apare „(necunoscut)/(fără)".
- Lipsește ~1% din comenzi (neajunse la fulfillment / anulate înainte de AWB). Pentru cifre
  100% decision-grade pe vânzări, vezi `gigi:product-sales --source shopify`.
- Monedă: brandurile non-RO sunt în moneda lor (CZK/PLN/BGN) — `sales` arată moneda per brand, nu convertește.
