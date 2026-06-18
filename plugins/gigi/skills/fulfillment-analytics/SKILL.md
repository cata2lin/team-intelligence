---
name: fulfillment-analytics
description: Analitică RAPIDĂ de livrare / vânzări / transport / retenție / COD-risk din AWBprint (DB-ul AWB/Frisbo), pe TOATE cele 21 de magazine Arona — Postgres = instant, ~99% complet (mult peste metrics warehouse, care e incomplet). 7 rapoarte (--report) — refuse (rată de refuz/retur per brand|curier|produs|payment|discount), sales (venit + comenzi + bucăți per brand, sau --daily pe zi), transport (cost REAL de curier per brand×curier, avg/colet, % din venit), stuck (colete blocate in_transit/pending de > N zile + „ghost"), repeat (retenție: clienți noi vs revenit, returning-rate), cod (COD value-at-risk: bani ramburs în zbor neîncasați, pe vârstă proaspăt vs stale 30z+, exclude comenzile test), geo (heatmap refuz pe județ/oraș RO). Folosește pentru „COD value-at-risk", „cât cash ramburs e în zbor", „rată de refuz pe județ/oraș", „COD vs prepaid refuz", „discount vs refuz". Opțional scrie un Google Sheet partajat. Folosește pentru „rată de refuz", „COD refusal", „ce brand/produs/curier se refuză cel mai mult", „cât pierdem din retururi", „venit pe brand azi/luna asta", „vânzări pe zi", „cost real de transport", „cât mănâncă transportul din marjă", „DPD vs Sameday cost", „colete blocate", „ghost shipments", „comenzi stuck in transit", „rată de revenire", „clienți care revin", „retenție", „repeat rate", „câți clienți noi". Triggers: refuz, retur, COD refusal, deliverability, livrabilitate, refuse rate, transport cost, cost curier, vanzari pe brand, venit zilnic, daily sales, stuck shipments, colete blocate, ghost shipment, repeat rate, returning customers, retentie, clienti noi, AWBprint, Frisbo.
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

# retenție: clienți noi vs revenit + returning-rate per brand
uv run fulfillment_analytics.py --report repeat --months 3

# COD value-at-risk: bani ramburs ÎN ZBOR (neîncasați), pe vârstă (proaspăt vs stale 30z+)
uv run fulfillment_analytics.py --report cod --months 3

# heatmap refuz pe județ (RO) — și pe oraș cu --by city
uv run fulfillment_analytics.py --report geo --by province

# levere de refuz: COD vs prepaid, și adâncimea reducerii
uv run fulfillment_analytics.py --report refuse --by payment
uv run fulfillment_analytics.py --report refuse --by discount

# orice raport + un Google Sheet partajat:
uv run fulfillment_analytics.py --report refuse --by brand --months 3 --sheet
```

### Parametri
| Flag | Default | Ce face |
|---|---|---|
| `--report` | `refuse` | `refuse` / `sales` / `transport` / `stuck` / `repeat` / `cod` / `geo` |
| `--by` | `brand` | `refuse`: brand/courier/product/**payment**/**discount** ; `geo`: province/city |
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

## cod & geo
- **cod** (COD value-at-risk): comenzi ramburs cu status IN_TRANSIT/PENDING (nici livrate, nici
  eșuate) = cash încă neîncasat. Defalcat „proaspăt (<30z)" vs „stale 30z+" (cash blocat/abandonat
  de urmărit separat). **Exclude comenzile de testare** (tag `test` în `tags`, mai ales Magdeal/Oferte).
  COD = `payment_gateway` ILIKE ramburs/cod/numerar/cash/„plata la livrare". Linia TOTAL = peste toate brandurile.
- **geo**: heatmap refuz pe `shipping_address->>'province'`/`'city'`, **DOAR RO** (brandurile străine
  n-au province); prag 40 comenzi rezolvate/zonă; antetul arată și rata națională.
- **refuse --by payment**: COD vs PREPAID (levierul mare). **--by discount**: 0%/1-15%/15-30%/30-40%/40%+
  (cu cât reducerea e mai mare, cu atât refuzul scade — clientul e mai angajat).

## Capcane
- `transport_cost` e populat ~81% (doar comenzile cu CSV de curier importat) — `cost/colet`
  se calculează doar pe cele cu cost > 0.
- `courier_name` are variante (`DPD` vs `dpd_ro`) și ~5% gol → apare „(necunoscut)/(fără)".
- Lipsește ~1% din comenzi (neajunse la fulfillment / anulate înainte de AWB). Pentru cifre
  100% decision-grade pe vânzări, vezi `gigi:product-sales --source shopify`.
- Monedă: brandurile non-RO sunt în moneda lor (CZK/PLN/BGN) — `sales` arată moneda per brand, nu convertește.
