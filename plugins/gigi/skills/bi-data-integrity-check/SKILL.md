---
name: bi-data-integrity-check
description: "Brand-health / data-integrity auditor for the metrics BI warehouse: which brands are missing ad-account mapping (spend reads 0 despite real revenue), which platform syncs are STALE/failing, and which rows are broken (conversionRate=0 with orders>0). Outputs a RAG table per brand x source with last-sync date, lag and the specific fix. Triggers: 'is the BI data correct', 'which syncs are stale', 'why is GT spend zero', 'data integrity check', 'brand health', 'which brands missing tracking', 'is TikTok syncing'."
---

# BI Data Integrity Check

Auditor de sanatate / integritate pentru warehouse-ul BI `metrics`. Spune rapid:
**ce e gresit in date inainte sa te bazezi pe ele** — branduri fara mapare de
ad-account (spend citeste 0 desi au venit real), sync-uri vechi/picate, si
randuri sparte (conversionRate=0 cu orders>0). Citeste live din Postgres
(`DATABASE_URL_METRICS`), **doar SELECT, nu scrie nimic**.

## How to run

```bash
cd plugins/gigi/skills/bi-data-integrity-check

uv run bi_data_integrity_check.py audit                 # tabelul RAG complet + freshness + top issues
uv run bi_data_integrity_check.py audit --threshold-days 3
uv run bi_data_integrity_check.py audit --no-glyph       # text RAG in loc de emoji (terminale fara emoji)
uv run bi_data_integrity_check.py issues                 # doar lista RED/AMBER cu fix-ul concret
uv run bi_data_integrity_check.py mapping                # branduri cu venit dar 0 conturi mapate
uv run bi_data_integrity_check.py freshness              # MAX(date) + lag per tabel de insight
uv run bi_data_integrity_check.py brand esteban          # focus pe un singur brand
```

Flag-uri: `--threshold-days N` (prag lag pana la AMBER, RED = peste 2x; default 2),
`--window-days N` (fereastra pt venit / randuri sparte; default 30),
`--min-revenue N` (prag venit RON pt "missing mapping"; default 1000),
`--no-glyph`.

Secretul `DATABASE_URL_METRICS` vine din `os.environ` daca exista, altfel din
knowledge base (`kb.py secret-get`). Nu printeaza niciodata valoarea.

## How it works

Pentru fiecare **brand activ x sursa** din `{meta, google, tiktok, shopify}`:

- **Ad sources (meta/google/tiktok)** — exista cont mapat **si activ** in
  `brand_meta_ad_accounts` / `brand_google_ads_accounts` /
  `brand_tiktok_ad_accounts`? Insights-ul nu e legat de `brandId` direct, ci prin
  `adAccountId` / `customerAccountId`, deci se face join prin tabelele de mapare
  spre `*_ad_insights_daily` pt `MAX(date)` + lag.
- **Shopify** — `MAX(date)` + lag in `shopify_analytics_daily`, plus share de
  randuri **sparte** (`conversionRate=0`/NULL desi `orders>0`).
- **Venit** = `SUM(totalPrice)` din `orders` pe fereastra (default 30z, exclude
  `deletedAt`).

RAG:
- **RED** — brand cu venit dar fara cont mapat (spend va citi 0), cont mapat dar
  INACTIVE, cont activ dar 0 insights, sau lag > 2x prag (sync STALE).
- **AMBER** — lag intre prag si 2x prag, sau majoritatea randurilor Shopify cu
  `conversionRate=0`.
- **GREEN** — mapat, activ, proaspat.
- **N/A** — fara venit si fara cont (brand inactiv comercial).

La nivel global afiseaza `MAX(date)` per tabel de insight (prinde un sync picat
pe toata platforma) si lista de branduri cu venit dar 0 conturi mapate. Fiecare
issue vine cu **fix-ul concret** (ce tabel sa mapezi / ce conector sa repari).

## Limitations

- "Venit" e brut din `orders.totalPrice` pe fereastra (nu P&L; doar ordin de
  marime ca sa prioritizeze brandurile importante).
- Pragul de freshness e calendaristic; nu stie de weekend-uri / pauze
  intentionate de campanie (un brand care chiar nu ruleaza TikTok va aparea RED
  daca are venit — verifica daca chiar e o problema sau brandul nu ruleaza acel
  canal).
- `convertedSessions` este nepopulat global in warehouse (apare 0 chiar si la
  brandurile sanatoase), deci RAG-ul Shopify se bazeaza pe `conversionRate=0`,
  nu pe `convertedSessions`; cel din urma e raportat doar informativ in cod.
- Read-only pe `metrics`. Nu atinge celelalte baze (grandia, arona-bi) si nu
  scrie nimic.
