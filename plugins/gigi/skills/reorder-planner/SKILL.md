---
name: reorder-planner
description: "Reorder / purchase-order planner per SKU for any ARONA brand — computes sell-through velocity from daily inventory snapshots, then how MUCH and WHEN to reorder so you never run out of winners: reorder_qty = velocity × (lead_time + safety_days) − on_hand − incoming. Flags SKUs that will stock out BEFORE the next shipment arrives, ranked by revenue-at-risk. Use when the user asks what to reorder, purchase order, restock quantity, how much to order, when will I run out, stockout, days of cover, or inventory planning."
user-invokable: true
---

> **ARONA (gigi) — diferența față de [[stock-restock-alerts-skill]].** `stock-restock-alerts` îți spune
> CE e pe terminate (alertă low/out-of-stock). Ăsta îți spune **CÂT și CÂND să comanzi** — cantitatea de
> PO și data de stockout, ca să nu rămâi fără winneri (mulți SKU HA/Grandia vin pe container cu lead time
> lung). Sursă: `metrics.inventory_daily_snapshots` (FRESH zilnic, ~1300 SKU/zi, toate brandurile).
> Vezi și [[fulfillment-analytics-skill]] (viteză din vânzări) și [[data-analytics-skill]] (forecast cerere).

# reorder-planner — cât și când reaprovizionezi (PO planner)

## Când o folosești
„Ce trebuie să comand la Grandia?", „câte bucăți comand din X?", „când rămân fără stoc?",
„ce winneri sunt pe terminate înainte să vină containerul?".

## Cum rulezi
```bash
cd plugins/gigi/skills/reorder-planner/scripts
export DATABASE_URL_METRICS="$(uv run ../../../../core/scripts/kb.py secret-get DATABASE_URL_METRICS)"

uv run reorder_planner.py --brand Grandia
uv run reorder_planner.py --brand Esteban --lead-days 21 --safety-days 10 --top 30
uv run reorder_planner.py --brand "George Talent" --only-reorder
```
- `--brand` (obligatoriu, ILIKE pe `brands.name`: Grandia, Esteban, Nubra, Bonhaus, „George Talent", …).
- `--lead-days` (default 30) = câte zile durează până sosește marfa de la furnizor — **pune-l real** (container vs local).
- `--safety-days` (default 14) = tampon de siguranță.
- `--days` (default 28) = fereastra din care se calculează viteza.
- `--only-reorder` = doar SKU-urile care trebuie comandate ACUM · `--top N`.

## Cum calculează
- **Viteză/zi** = `Σ max(onHand_ieri − onHand_azi, 0)` pe fereastră / nr zile — adică suma scăderilor
  zilnice de stoc (creșterile = reaprovizionări sunt ignorate). Proxy robust din snapshot-uri, o singură
  sursă, mereu fresh.
- **Cover (zile)** = `onHand / viteză/zi`.
- **COMANDĂ** = `ceil(viteză × (lead + safety) − onHand − incoming)`, minim 0.
- **Venit/zi risc** = `viteză × preț` — cât pierzi pe zi dacă rămâi fără stoc (folosit la rankare).
- 🔴 = cover < lead time → se termină ÎNAINTE să vină marfa, comandă ACUM · 🟡 = sub pragul de siguranță.

## Cum citești
- Lista e sortată: întâi ce trebuie comandat, apoi după cover crescător (cele mai urgente sus), apoi venit-risc.
- `cost PO ≈` (subsol) = `Σ comandă × costPerItem` în moneda brandului — bugetul de aprovizionare.
- `cover ∞` = nu s-a vândut în fereastră (viteză 0) → nu comanda.

## Capcane
- Viteza din snapshot-uri = proxy: recount-uri sau corecții de stoc apar ca „vânzări" false; pt viteză din
  vânzări reale încrucișează cu [[fulfillment-analytics-skill]] (AWBprint delivered).
- `onHand`/`incoming` negative = quirk-uri de inventar (afișate ca atare, nu blochează calculul).
- Brand cu fereastră scurtă de snapshot-uri (ex. magazine adăugate recent: Ofertele/Reduceri ~6-8 zile) →
  viteza e pe mai puține zile, mai zgomotoasă; mărește `--days` când există istoric.
- COGS = `costPerItem` din Shopify (CZ/PL pot fi 0 → cost PO sub-estimat acolo).
