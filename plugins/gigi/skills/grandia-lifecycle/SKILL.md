---
name: grandia-lifecycle
description: Kill-list / Scale-list + drop-off de funnel GA4 per produs pentru GRANDIA, dintr-o singură citire pre-agregată din DB-ul Grandia (instant) — NU din 3 API-uri lente (Shopify + Google Ads + GA4). 4 rapoarte (--report) — summary (câte produse de tăiat/scalat/cu drop-off + impact RON), kill (cheltuie pe ads dar pierd bani: ad_spend_30d>0 AND profit_30d<0 → de oprit), scale (profitabile dar aproape rupte de stoc: profit_30d>0 AND days_of_stock<N → de reaprovizionat & scalat), cro (cerere irosită: GA4 add-to-cart fără cumpărare). Opțional scrie un Google Sheet partajat. Doar GRANDIA. Folosește pentru „ce produse Grandia să opresc/scalez", „kill list", „scale list", „produse care pierd bani pe ads", „produse profitabile cu stoc mic de reaprovizionat", „unde pică funnel-ul", „add to cart fără cumpărare", „drop-off", „lifecycle produs Grandia", „ce să tai din PMax".
---

# Grandia product lifecycle — kill / scale / CRO drop-off

Grandia ține deja un **panel zilnic per-produs** (`rpt_product_status_daily`: `profit_30d`,
`ad_spend_30d`, `days_of_stock`, `conversion_rate_30d`, `marketing_status`, `profit_status`) +
funnel-ul GA4 (`ga4_daily_product_metrics`). Acest skill îl citește direct (sub-secundă) în loc
să tragă lent din Shopify + Google Ads + GA4. **Doar Grandia.**

## Cum rulezi
```bash
uv run grandia_lifecycle.py                      # summary: câte de tăiat / scalat / cu drop-off + RON
uv run grandia_lifecycle.py --report kill        # produse care cheltuie pe ads dar pierd bani
uv run grandia_lifecycle.py --report scale --stock-days 14   # profitabile dar aproape fără stoc
uv run grandia_lifecycle.py --report cro --min-atc 3         # add-to-cart fără cumpărare (funnel)
uv run grandia_lifecycle.py --report kill --sheet            # + Google Sheet partajat
```

| Flag | Default | Ce face |
|---|---|---|
| `--report` | `summary` | `summary` / `kill` / `scale` / `cro` |
| `--stock-days` | 14 | `scale`: pragul `days_of_stock` (produse care se epuizează sub N zile) |
| `--min-atc` | 2 | `cro`: minim add-to-cart ca să nu fie zgomot |
| `--limit` | 40 | câte rânduri |
| `--sheet` | — | scrie și un Google Sheet partajat (anyone-with-link) |

## Logică
- **kill** = `ad_spend_30d > 0 AND profit_30d < 0` (ardem bani pe reclame la un produs care pierde) → exclude din PMax / oprește.
- **scale** = `profit_30d > 0 AND days_of_stock < N` (winner care se rupe de stoc) → reaprovizionează ȘI urcă bugetul. Sortat după profit.
- **cro** = produse cu add-to-cart GA4 dar cumpărări ~0, sortate după rata ATC→buy crescătoare → cerere care nu se convertește (verifică stoc/variantă/preț/checkout).

## Cheia de join (atenție)
Cele 3 tabele NU au cheie comună: `rpt_product_status_daily.product_id` e CUID,
`ga4_daily_product_metrics.shopifyProductId` e bigint. Join via
`Product.id` (CUID) → `Product."shopifyNumericId"` (bigint). Acoperire ~99% (479/479 produse au
shopifyNumericId, ~474 au metrici GA4 cu sesiuni>0).

## Plumbing & capcane
- DB Grandia: secret KB `DATABASE_URL_GRANDIA` (read-only, pg8000). Google Sheet: `GOOGLE_OAUTH_TOKEN_JSON` (ca restul).
- Fereastra e fixă pe **30 zile** (coloanele `*_30d` sunt rolling 30d). Panel-ul e fresh la ieri (rpt) / acum 1-2 zile (GA4 lag normal).
- `days_of_stock` poate fi NULL la SKU-urile fără viteză (nu intră în scale; irelevant la kill).
- Numai Grandia (alte branduri n-au panel-ul ăsta). Pt restul, vezi `gigi:product-matrix` (POAS din Google Ads).
