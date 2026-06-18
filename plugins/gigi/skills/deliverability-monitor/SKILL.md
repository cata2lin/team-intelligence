---
name: deliverability-monitor
description: Diagnose the COD-refusal / failed-delivery money leak across every Arona brand. Default source AWBprint (--source awb, instant, ~99% complet, toate 21 magazinele, județ inclus direct din shipping_address, fără SSH); --source vps păstrează vechiul drum (profitability.db de pe VPS prin SSH + județ din metrics, incomplet). Computes refusal rate and revenue-at-risk by brand, by courier (DPD/Packeta/Econt/Sameday), by COUNTY and by product/SKU, plus wasted transport (refused parcels × real cost/parcel ×2). Surfaces the worst county×courier×brand pockets so ops can blacklist counties, tighten COD address validation, or switch courier per region. Use for "COD refusal analysis", "deliverability by county/courier", "refused/return rate per brand", "where do we lose money on shipping", "livrabilitate", "refuz ramburs", "rata de refuz". Vezi și `gigi:fulfillment-analytics` (refuse/geo/cod) pe aceeași sursă.
---

# Deliverability monitor — refuzul COD (cel mai mare levier de bani)

Cca **9.000 colete refuzate/lună (~16%)** = **~272k RON/lună transport irosit** + venit la risc. Acest skill arată EXACT unde se scurge banul (brand × curier × județ × SKU) ca să se poată acționa.

## Cum rulezi
```bash
uv run deliverability_monitor.py --month 2026-05 --by brand     # rata refuz + venit la risc per brand
uv run deliverability_monitor.py --month 2026-05 --by courier   # per curier (DPD/Packeta/Econt/Sameday)
uv run deliverability_monitor.py --month 2026-05 --by county    # per județ (direct din AWBprint shipping_address)
uv run deliverability_monitor.py --month 2026-05 --by sku       # produsele cel mai des refuzate
uv run deliverability_monitor.py --month 2026-05 --by pocket    # cele mai proaste combinatii brand×curier×judet
uv run deliverability_monitor.py --month 2026-05 --brand Esteban --by county
```

## Cum se calculează
- **Refuz/livrabilitate**: `profit_orders.status_category` (Livrata / Refuzata / Netrimisa / Anulata / In curs) din `data/profitability.db` (VPS, prin SSH). „Plecate" = Livrata+Refuzata+In curs; rata refuz = Refuzata/plecate.
- **Curier**: `courier_key` (dpd-ro/packeta/econt/sameday). **Județ**: join pe `order_name` cu `metrics.orders."shippingProvince"`. **SKU**: `profit_orders.skus` + `profit_sku_titles`.
- **Transport irosit** ≈ refuzate × `profit_transport_costs.cost_per_parcel` × 2 (tur-retur). **Venit la risc** = suma `revenue` a comenzilor refuzate (valută mixtă, brut).
- Read-only (doar SELECT-uri).

## Limitări
- Venitul la risc e brut, valută mixtă (RON/EUR amestecat pe BG/CZ/PL) — e un ordin de mărime, nu cash exact.
- Județul depinde de acoperirea `shippingProvince` în metrics (bună pe brandurile RO).
