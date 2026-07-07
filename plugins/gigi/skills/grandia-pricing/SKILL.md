---
name: grandia-pricing
description: Grandia price-competitiveness + CPA-aware REPRICING pipeline. Refreshes competitor prices into the live prc_* verdict engine (re-scrapes the existing product↔competitor mappings — 94% direct retailer URLs incl eMAG — via JSON-LD/og:price), tells you "are we ok on price?" (SCUMP/OK/HEADROOM vs live cheapest competitor, margin-aware), and builds a CPA-aware raise/lower plan: RAISE proven-sellers whose margin-AFTER-real-CPA is thin (toward market, protects conversion), LOWER slow-movers with headroom (floored so contribution still covers target CPA), stock-gated, then APPLIES to Shopify. Use for "suntem ok pe preț?", "reprice Grandia", "crește prețuri unde marja nu e ok", "scade unde e headroom", "price competitiveness", "competitor prices Grandia", "verdict preț", "prc engine", "which products are overpriced", "aplică prețuri Grandia". Distinct from gigi:pricewatch (standalone watchlist SQLite). Read-only by default; price writes are --apply after a verified dry-run.
argument-hint: "rescrape | verdict | reprice [--target-cpa N] | apply --tsv plan.tsv [--apply]"
---

# grandia-pricing — competitivitate preț + repricing CPA-aware pentru Grandia
> Author: Gigi.

Grandia se bate pe **preț** (home/garden, vs eMAG & retaileri RO). Are un engine de verdict VIU
în DB-ul Grandia (`postgres-grandia`): tabelele **`prc_*`**, rulează nightly 02:30 și scrie
`prc_product_status_daily` (competitiveness good/poor/no_data, pricing_action, suggested_price).
Acest skill **alimentează engine-ul cu prețuri proaspete** + adaugă **verdict imediat** + **repricing
CPA-aware** + **aplicare în Shopify**.

> 🗺️ Context profit/CPA: `shared/HARTA.md` + memoria [[grandia-price-engine-rebuild]], [[target-cpa-per-store]].
> ⚠️ NU e `gigi:pricewatch` (ăla = watchlist SQLite standalone). Aici = engine-ul `prc_*` LIVE + repricing.

## Pipeline (4 pași, fiecare = un script `uv run` self-contained)

### 1. `grandia_price_rescrape.py` — reîmprospătează prețurile de competitor
Reia **mapările existente** din `prc_competitor_products` (produs↔competitor, ~403 produse, 94% URL-uri
directe de retaileri **incl. eMAG**) și extrage prețul generic: **Shopify `.js` → JSON-LD `offers.price`
→ og/product:price:amount → itemprop**. Scrie `prc_competitor_prices(source='rescrape')` + updatează
`last_price`. **~84% succes** (eșecuri = 403 bot / URL mort). Robust la hang-uri DNS (backstop 18s/URL).
Engine-ul e **source-agnostic** → consumă feed-ul → verdictele se reaprind la 02:30.
```bash
uv run grandia_price_rescrape.py --limit 20            # dry sample
uv run grandia_price_rescrape.py --workers 20 --apply  # full refresh, scrie
```
> **Automat pe VPS**: cron `30 1 * * *` (`run_grandia_rescrape.sh`, înainte de engine-ul 02:30).

### 2. `grandia_price_verdict.py` — "suntem ok pe preț?"
Read-only. JOIN preț Shopify live (Product/Variant) cu prețurile proaspete → per produs: cel mai
ieftin/median competitor LIVE, Δ vs noi, marjă ex-TVA, verdict **SCUMP / OK / HEADROOM** + `price_floor`.
Filtru de plauzibilitate (ignoră prețuri <35% sau >280% din al nostru = wrong-product match).
```bash
uv run grandia_price_verdict.py --fresh-days 3 --tol 0.05 --only SCUMP
```

### 3. `grandia_reprice.py` — plan de repricing CPA-aware (cerere × marjă × competiție)
**Marja = NETĂ, DUPĂ CPA-ul REAL per produs** (spend 30z din `metrics cache.product_ad_spend` ÷ comenzi),
nu marja brută. Două mișcări asimetrice:
- **RAISE** = vânzări bune + marjă/CPA subțire → crește spre prețul pieței (protejează conversia/CPA;
  contribuția ↑ = CPA mai sustenabil). Flag `🔴CPA-problemă` dacă rămâne negativ chiar la piață (= problemă
  de ads, nu de preț).
- **LOWER** = vânzări slabe + marjă/CPA grasă (headroom) → scade, dar **floor = CPA-safe**
  (`1.21×(COGS+transport+CPA+profit_min)`) ca reclama să rămână profitabilă ("menține CPA").
- **Stock-gated** (`--min-stock`, sare produsele fără stoc).
```bash
uv run grandia_reprice.py --target-cpa 70 --min-profit 15 --transport 20 \
  --good-sales 6 --thin-margin 15 --slow-sales 2 --fat-margin 35 --min-stock 1 \
  --tsv /tmp/plan.tsv          # scrie planul (ambele liste) pt pasul 4
```

### 4. `grandia_apply_prices.py` — aplică planul în Shopify (store GRAN)
Citește TSV-ul de la pasul 3, selectează **câștigurile CLARE** (creșteri ancorate în piață + scăderi unde
suntem peste piață; ține deoparte creșterile oarbe fără piață + CPA-problemele), **verifică că prețul LIVE
încă se potrivește** cu „acum" (sare dacă a driftat), apoi `productVariantsBulkUpdate`. Citește `userErrors`.
```bash
uv run grandia_apply_prices.py --tsv /tmp/plan.tsv          # dry-run (preview + verify)
uv run grandia_apply_prices.py --tsv /tmp/plan.tsv --apply  # scrie prețurile
```

## Praguri (toate parametrizabile)
| Flag | Default | Sens |
|---|--:|---|
| `--target-cpa` | 70 | CPA țintă Grandia (breakeven ~69, agenție) — hurdle-ul de menținut |
| `--transport` | 20 | est. transport/comandă (bulky = mai mare → floor optimist, flag-uit) |
| `--good-sales` / `--slow-sales` | 6 / 2 | prag comenzi 30z „bun" / „slab" |
| `--thin-margin` / `--fat-margin` | 15 / 35 | marjă NETĂ cu CPA %: sub = de crescut / peste = loc de scădere |
| `--min-stock` | 1 | doar produse cu stoc |
| `--fresh-days` | 3 | prețul de competitor trebuie să fie mai nou de N zile |

## Siguranță
- Postgres **read-only** by default; verdict + reprice NU scriu în DB.
- `rescrape --apply` scrie DOAR în `prc_competitor_prices/products` (tranzacțional, idempotent).
- `apply --apply` = **singurul** care schimbă prețuri Shopify; doar după dry-run care verifică prețul live.
  Prețurile-s reversibile (TSV-ul păstrează „Preț acum").
- Secrete via `arona_pg.secret()` (env-first + KB), niciodată printate. Token Shopify piped, nu afișat.

## Capcane
- **eMAG** e prins doar unde scraperul vechi îl mapase (arona-bi n-are eMAG). Extindere = matcher eMAG dedicat.
- CPA flat distorsionează produse ieftine → de-aia folosim CPA REAL per-SKU (pasul 3). Produse fără spend = CPA 0 (organic, corect).
- Grandia brand_id în `cache.product_ad_spend` = `cmo5ulyl80003h1w2xlzfzhvh`.
- Un produs cu variante multiple la prețuri diferite: apply-ul updatează doar varianta al cărei preț live ≈ „acum".
