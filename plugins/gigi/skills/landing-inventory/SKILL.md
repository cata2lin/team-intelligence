---
name: gigi:landing-inventory
description: Inventar ZILNIC al TUTUROR paginilor care primesc trafic, per magazin — leagă GA4 (top landing pages, split paid/organic, sesiuni + conversii + venit) de STOC (InventorySync pool_states, boxul AWB Arona, pe barcode) și de VITEZĂ (timp de încărcare + HTTP status + bytes; opțional Lighthouse/PSI pe top-trafic). Semnalează paginile cu TRAFIC dar FĂRĂ STOC (bani de reclamă arși pe produse epuizate/oversold) și paginile LENTE. Scrie în cache.landing_inventory (upsert pe zi+url), sursa pentru modulul UI din app-ul metrics. Folosește pentru „ce pagini primesc trafic dar n-au stoc", „unde arunc bani pe reclame la produse epuizate", „inventar landere cu trafic", „ce landere sunt lente", „top pagini per magazin din GA".
category: analytics-bi
version: 1.0.0
---

# Landing Inventory — paginile cu trafic × stoc × viteză

Inventar zilnic care răspunde la o singură întrebare de bani: **pe ce pagini trimitem trafic (mai ales
plătit) și ce e în neregulă cu ele** — fără stoc (reclame arse) sau lente (conversie pierdută). Rulează din
`plugins/gigi/skills/landing-inventory/scripts/landing_inventory.py`.

## When to use
- „Ce pagini primesc trafic dar sunt fără stoc?" / „unde arunc bani pe reclame la produse epuizate?"
- „Dă-mi inventarul tuturor landerelor cu trafic, per magazin" (top pagini din GA4).
- „Ce landere sunt lente?" (timp de încărcare / HTTP status).
- Alimentează modulul UI **Landing Inventory** din app-ul metrics (citește `cache.landing_inventory`).
- NU e pentru: profit per SKU (→ `profit_by_sku`), doar stoc (→ `gigi:stock-restock-alerts`), doar analytics de trafic (→ `gigi:analytics`).

## Ce face (pipeline)
1. **GA4** per magazin (property din `BRANDS`): dimensiuni `hostName` + `landingPage` + `sessionDefaultChannelGroup`,
   metrici `sessions` + `keyEvents` + `purchaseRevenue`. Agregă pe URL normalizat, split **paid/organic**.
2. **Produs → barcode**: URL `/products/<handle>` → `products.handle` → `variants.barcode` (warehouse metrics).
3. **STOC**: barcode → `pool_states.quantity` (**InventorySync**, `DATABASE_URL_INVENTORYSYNC`, boxul AWB Arona =
   stocul poolat care se sync-uiește în Shopify). `in_stock=false` dacă suma cantităților ≤ 0 (prinde și oversold negativ).
4. **VITEZĂ**: GET cronometrat (UA de browser real ca să nu iei 503) → `load_ms` + `http_status` + `page_bytes`.
   Opțional **Lighthouse** (PageSpeed Insights) pe top-N pagini/magazin → `perf_score` + `lcp_ms` + `cls`.
5. **Scrie** în `cache.landing_inventory` (**upsert pe zi+url**, FAIL-SAFE, nu delete-then-insert). Flag-uri:
   `flag_traffic_no_stock` (trafic + stoc epuizat) și `flag_slow` (`load_ms` sau `lcp_ms` > prag).

## Steps
```bash
cd plugins/gigi/skills/landing-inventory/scripts

# un magazin, dry-run (nu scrie) — vezi inventarul + câte-s fără stoc
uv run landing_inventory.py sync --brand grandia --days 7

# scrie în tabel
uv run landing_inventory.py sync --brand grandia --days 7 --apply

# TOATE magazinele cu GA4 (rularea zilnică)
uv run landing_inventory.py sync --all --days 7 --pages 60 --apply

# + Lighthouse pe top-10 pagini/magazin (necesită PSI activat pe cheia Google — vezi Notes)
uv run landing_inventory.py sync --all --days 7 --lighthouse 10 --apply

# rapid, doar GA4 + stoc (fără măsurare viteză)
uv run landing_inventory.py sync --all --days 7 --no-fetch --apply
```
Flag-uri: `--brand <slug>` sau `--all` · `--days N` (sau `--from/--to`) · `--limit` (max landing pages GA4/magazin,
default 2000) · `--pages N` (câte pagini top verifici pt stoc/viteză, default 200) · `--slow-ms` (prag lent, 3000) ·
`--lighthouse N` (Lighthouse pe top-N/magazin, 0=off) · `--no-fetch` (sari peste viteză) · `--apply` (scrie).

## Interogare rezultat (cache.landing_inventory)
```sql
-- Pagini cu trafic PLĂTIT dar fără stoc (bani arși), toată rețeaua, ziua cea mai recentă
SELECT brand, path, stock_qty, sessions, sessions_paid, revenue
FROM cache.landing_inventory
WHERE day=(SELECT max(day) FROM cache.landing_inventory) AND flag_traffic_no_stock
ORDER BY sessions_paid DESC;

-- Landere lente (după LCP dacă există, altfel load_ms)
SELECT brand, path, sessions, load_ms, lcp_ms, perf_score
FROM cache.landing_inventory WHERE day=(SELECT max(day) FROM cache.landing_inventory) AND flag_slow
ORDER BY sessions DESC;
```

## Notes
- **Surse/secrete** (din KB, `core:fetch-secret`): `GA4_SA_JSON` (service account, `analytics.readonly`),
  `DATABASE_URL_METRICS` (warehouse: brands/products/variants + tabelul cache), `DATABASE_URL_INVENTORYSYNC`
  (pool_states, boxul AWB Arona), `GADS_GOOGLE_API_KEY` (doar pt Lighthouse/PSI).
- **BRANDS**: property_id GA4 per magazin (același map ca `gigi:analytics/ga4.py`). Magazinele fără trafic în
  fereastră întorc „fără date GA4" (0 sesiuni = OK, nu eroare). Adaugi un magazin → adaugi property_id în `BRANDS`.
- **Stoc = InventorySync, NU Shopify live**: `pool_states.quantity` e stocul poolat care se sync-uiește în Shopify
  (boxul AWB Arona). Un produs fără barcode (ex „cutie-de-cadou") rămâne `stock_qty=NULL` (nu-l putem verifica).
- **⚠️ Lighthouse (PSI)**: PageSpeed Insights API trebuie ACTIVAT pe proiectul Google al cheii (`GADS_GOOGLE_API_KEY`
  dă 403 „API not enabled"; keyless dă 429 quota partajată). Până se activează, `--lighthouse` degradează grațios
  (întoarce NULL, NU crapă cronul) — semnalul de viteză rămâne `load_ms` (timp de încărcare, funcțional). Când PSI
  e activat, coloanele `perf_score`/`lcp_ms`/`cls` se populează automat.
- **Cron zilnic** (LIVE pe VPS `/root/Scripturi`, 05:30): `flock … uv run landing_inventory.py sync --all --days 7 --pages 60 --apply`.
  Secretele pe VPS vin din `/root/Scripturi/.env` (scriptul îl încarcă via python-dotenv; `kb()` verifică `os.environ` întâi).
- **Acoperire magazine**: `landing_inventory.py properties` listează toate property-urile GA4 la care are acces service
  account-ul → adaugă-le în `BRANDS`. Magazine ACTIVE fără GA4 accesibil (SA neadăugat): Magdeal, Reduceri bune, Apreciat,
  Bonhaus(+BG/PL), Rossi Nails, Lab Noir → adaugă SA-ul (`client_email` din `GA4_SA_JSON`) ca **Viewer** pe property-urile lor.
- **`cache.landing_inventory_runs`** (day,brand,ga4_ok,ga4_sessions): ce magazine au dat GA4 în ziua respectivă → UI-ul
  arată bannerul „GA4 lipsă" pt cele cu `ga4_ok=false` (0 sesiuni sau fără acces).
- **FAIL-SAFE**: upsert pe (day,url) cu `COALESCE` pe coloanele Lighthouse (o rulare fără PSI nu șterge scorurile
  dintr-o rulare cu PSI din aceeași zi). Niciun DELETE pe istoric.
- Related: [[gigi:analytics]], [[gigi:stock-restock-alerts]], [[inventorysync-app]], [[gigi:multi-brand-pnl]].
```
