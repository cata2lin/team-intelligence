---
name: stock-restock-alerts
description: Low-stock, out-of-stock and restock-priority report across all Arona Shopify stores. Reads metrics inventory_levels + inventory_daily_snapshots to compute per-SKU 28-day sell-through velocity, days-of-cover, projected stock-out date and the value of the gap; flags OOS, low-stock and "will stock out within X days" per brand, plus dead-stock and inventory value. Use for restock planning, "what's about to go out of stock", "which best-sellers are low", "out of stock", dead-stock/overstock, inventory value by brand, feeding procurement. Triggers: low stock, out of stock, OOS, restock, reorder, stock alert, inventory report, days of cover, stockout, slow movers, stoc, epuizare, aprovizionare.
---

# Stock & restock alerts (toate magazinele)

Per SKU: viteza de vânzare pe 28 zile, zile de acoperire (days-of-cover) și data estimată de epuizare — ca procurement (Adriana/Anne) să știe ce să comande ÎNAINTE să rupă stocul.

## Cum rulezi
```bash
uv run stock_restock_alerts.py --report low --brand Esteban     # days-of-cover <= prag (default 14)
uv run stock_restock_alerts.py --report oos --brand all          # rupte de stoc (qty<=0) care încă vând
uv run stock_restock_alerts.py --report restock --brand Grandia  # prioritate reaprovizionare (lead+buffer)
uv run stock_restock_alerts.py --report deadstock --brand all    # stoc care nu se mișcă (capital blocat)
uv run stock_restock_alerts.py --report value --brand all        # valoare inventar la cost, per brand
# opțiuni: --window 28 (fereastra velocity), --threshold 14, --lead 14, --buffer 14, --limit 30
```

## Cum se calculează
- Sursă: `metrics.inventory_levels` (stoc curent) + `inventory_daily_snapshots` (istoric ~54z) + `inventory_items`/`variants` (costPerItem).
- Velocity = (stoc acum 28z − stoc azi), clamp ≥0; days_cover = stoc_curent / (velocity/28); epuizare = azi + days_cover.
- Restock când days_cover < lead_time. Read-only (SELECT).

## Limitări
- Depinde de istoricul de snapshot-uri (cele cu <window zile de istoric dau velocity parțial).
- `--max-real-stock` exclude SKU-urile cu stoc placeholder „infinit" (mystery box etc).
