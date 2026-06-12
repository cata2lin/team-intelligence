---
name: cs-ghost-shipments
description: Detects "ghost shipments" for Customer Service — the parcel where a shipping label was printed (AWB issued) but the courier NEVER scanned it at pickup, so the customer got the Shopify "your order has shipped" email while the parcel is still sitting in the warehouse. These generate the angriest WISMO tickets ("it says shipped X days ago, where is it?!"). Covers the PRE-PICKUP gap that cs-proactive-delays (in-transit only) misses. Two signals from the Scripturi profitability engine (profit_orders): (1) GHOST = shopify_delivery_status='LABEL_PRINTED' AND status_category='Netrimisa' older than N days (label made, AWB exists with courier status 'Shipment data received' = registered but never scanned at pickup); (2) NO-TRACKING = status_category='Lipsa awb' (marked shipped/FULFILLED with no AWB at all, nothing to track). Output per store, sorted by age then value, with order name, age in days, status, revenue, AWB and a suggested action (check warehouse / re-ship / proactive message), plus a summary with total count and revenue blocked. Use for "ghost shipments", "label printed but not shipped", "shipped email but parcel never left", "etichetă printată dar coletul n-a plecat", "colete fantomă", "marcate expediat fără tracking", "pre-pickup WISMO", "comenzi care n-au plecat din depozit". Read-only.
---

# CS — Colete fantomă (etichetă printată, coletul n-a plecat)

Eticheta s-a printat (AWB emis), Shopify a trimis clientului mail „s-a expediat" — dar curierul NU l-a scanat niciodată la ridicare. Coletul stă în depozit. Acestea generează cele mai furioase tichete WISMO. Acoperă gaura PRE-PICKUP pe care `cs-proactive-delays` (doar in-tranzit) o ratează.

## Cum rulezi
```bash
uv run cs_ghost_shipments.py                  # fantome >3 zile, toate magazinele
uv run cs_ghost_shipments.py --days 5         # prag vechime 5 zile
uv run cs_ghost_shipments.py --store Esteban  # un singur magazin
uv run cs_ghost_shipments.py --json           # pt automatizare
```

## Cum funcționează
- Citește DOAR din `data/profitability.db` (`profit_orders`, prin SSH la VPS). Read-only total.
- Semnal 1 — FANTOMĂ: `shopify_delivery_status='LABEL_PRINTED'` + `status_category='Netrimisa'` + `created_at` mai vechi de N zile. AWB-ul există (DPD, `courier_status='Shipment data received'` = înregistrat dar nescanat la ridicare).
- Semnal 2 — FĂRĂ TRACKING: `status_category='Lipsa awb'` = marcat expediat/FULFILLED fără AWB deloc → clientul are mail de expediere dar n-are ce urmări.
- Contact (nume/telefon/oraș) din `metrics.orders`. Output per magazin, sortat după vechime apoi valoare; acțiune sugerată per colet + sumar cu valoarea blocată.

## Note
- Aproape toate fantomele sunt DPD-RO cu „Shipment data received" — semnal curat de etichetă nescanată.
- Cele foarte vechi (>7 zile) = aproape sigur uitate în depozit → re-expediere/refund + scuze, nu doar mesaj.
