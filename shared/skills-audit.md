# Team Skills Audit — overlap, consolidation & DB-efficiency plan
> Generated 2026-06-16 by Gigi. 93 skills reviewed (gigi 64, library 13, core 13, anne 2, catalin 1).
> Purpose: stop every skill re-deriving the same expensive data each run; collapse near-duplicates.

## TL;DR
- **Biggest efficiency win is data, not code:** ~3 independent P&L paths, ~8 inline copies of the Google-Ads MCC client, FX/DSN/brand-mapping helpers duplicated dozens of times, and nearly every CS skill re-resolves order+customer+AWB live. Introduce a **shared cache layer in the `metrics` warehouse** (tables below, refreshed by cron) + a handful of **shared libs in `core/scripts/`**.
- **~25 skills can consolidate into ~9** without losing any capability (subcommands/modes instead of separate skills).
- Nothing here is destructive yet — this is the plan; execute per-item after sign-off (skills are team-wide).

---

## 1. Cache layer (precompute → metrics warehouse). The core of "don't recompute every run".
| Table | Columns (core) | Replaces / feeds | Readers | Refresh |
|---|---|---|---|---|
| `metrics.daily_brand_pnl` | date, brand_id, orders, revenue_net, cogs_net, transport_net, fb/google/tiktok_spend, total_adspend, contribution_margin, mer, cpa, aov | the VPS `daily_perf.db` SQLite reached over **SSH** (slow, off-warehouse, SPOF) | multi-brand-pnl, agency-audit, daily-ops-briefing, weekly-insights | nightly |
| `metrics.daily_ad_spend_ron` | date, brand_id, platform, account_id, spend_native, currency, spend_ron | repeated live Meta/Google/TikTok spend pulls + per-day FX | grandia-pnl, meta-ads, tiktok-ads, weekly-insights, multi-brand-pnl | daily/hourly |
| `metrics.fx_rate_daily_ron` | rate_date, currency, ron_per_unit (forward-filled) | `build_fx_index()` reimplemented 4× | every P&L/ads skill | daily |
| `metrics.order_enriched` | order_name, brand, phone, email, name, status, financialStatus, totalRefunded, awb, courier, ship_status (delivered/in_transit/returned/refused), is_refusal, placed_at, shipped_at | every CS skill re-resolving order+customer+AWB live | ~all cs-*, cod-confirmation, customer-identity, daily-ops-briefing | hourly |
| `metrics.customer_agg` | identity(phone/email), order_count, ltv_delivered, refused_count, refusal_rate, brands[], first/last_order, vip/serial_refuser flags | per-customer LTV/refusal recomputed in many profiles | cs-customer-360, cs-profile, cs-conversation-profile, cs-draft-reply, cod-confirmation | daily |
| `metrics.ticket_order_link` | ticket_id, order_name, brand, match_method | ticket→order linking (partly already in `richpanel_tickets.order_name/resolved_store/match_order/link_method`) | profiles, auto-triage, quality-audit, draft-reply | with ticket sync |
| `metrics.product_refusal_rate` | sku, brand, livrate, refuz, anulate, refuz_pct, window | the SSH→remote-SQLite `profit_orders` hop | cod-confirmation, anne:ha-refuz, product-quality-radar | daily |
| `metrics.product_economics` | variant_id, sku, brand, price, cost_per_item, effective_margin, bundle_ratio, inventory_qty | per-product margin re-derived | product-matrix, grandia-product-marketing | daily |
| `metrics.product_basket_pairs` | brand, product_a, product_b, co_count, support, confidence, lift, window_days | the heaviest query in the SEO cluster (180-day self-join) | cross-sell, PDP "bought together", Klaviyo, 2+1 pairing | nightly |
| `metrics.rma_signal_daily` | sku, brand, refunds_shopify, returns_rma, reason, defect_rate | metrics-orders × grandia-RMA cross-join | product-quality-radar, returns-rma-report | nightly |
| `metrics.dataforseo_cache` | query_type, key, location, fetched_at, payload jsonb, cost | **the only PAY-PER-CALL source, currently uncached** | analytics (dataforseo) | TTL 7–30d |
| `metrics.seo_gsc_daily` / `metrics.domain_authority` | (brand,date,query,page,clicks,impr,ctr,pos) / (domain,opr,fetched) | live GSC/OpenPageRank re-pulls | analytics, shopify-seo prioritization | daily / weekly |

## 2. Shared libraries (one module, many callers) → `core/scripts/`
| Lib | Folds in (today duplicated in…) |
|---|---|
| `pg_dsn` (clean DSN + read-only connect) | **~40 files** re-inline `_clean_dsn`/`_PG_OK` |
| `gads_client` (promote `google-ads-mcc/gads.py`) | ~8 inline OAuth/MCC copies: ads-anomalies, budget-simulator, campaign-structure, search-terms, ad-copy, product-matrix, weekly-insights, grandia-pnl |
| `shopify_lib` (promote shopify-seo's `Store`) | 3 clients: shopify-seo `Store`, shopify-stores `shopify_gql.py`, xconnector inline `shopify_gql()` |
| `fx_ron` (to_ron/build_fx_index) | grandia-pnl, meta-ads, tiktok-ads, cs-agent-performance |
| `pnl_core` (CM = rev−COGS−transport−adspend + MER/ROAS/CPA/AOV) | grandia-pnl, multi-brand-pnl, agency-audit, weekly-insights |
| `brand_accounts` (brand→{meta,google,tiktok} acct + token) | grandia-pnl, meta-ads, tiktok-ads, bi-data-integrity; reconcile the 2 parallel resolvers (DB-JOIN vs `brandmap.py` Mapping sheet) |
| `metrics_db` (+ BRANDS map) | cross-sell, reviews-manager, stock-restock-alerts, product-quality-radar each redefine it |
| `rma_lib` (grandia rma_requests joins) | returns-rma-report, rma-sla-watchdog, product-quality-radar |
| `awb_lib` (courier guess + normalize_status) | awb-track (canonical) ← xconnector, rma-sla-watchdog, + every cs-* AWB lookup |
| `richpanel_client` | cs-tickets, richpanel-auto-triage, richpanel-backlog-janitor, cs-draft-reply, richpanel-export, cs-sla-dashboard |
| `ro_text` (de-AI: invisible-char strip + RO blocklist) | ai-scrub (canonical) ← labnoir_rewrite_articles re-implements it |
| `prodmap.py`/`brandmap.py` | **byte-identical** between meta-ads/ and tiktok-ads/ — dedupe to one |

## 3. Skill consolidation map (~25 → ~9)
- **Google Ads suite** — fold `ad-copy, ads-anomalies, budget-simulator, campaign-structure, search-terms, weekly-insights` (and keep `product-matrix` sharing the same client) into **`google-ads`** subcommands over `gads_client`. (-6)
- **`multi-brand-pnl --agency`** absorbs **agency-audit** (same VPS query + contrib formula). (-1)
- **`articles --store esteban|gt|nubra|labnoir`** — collapse the 4 brand-article skills; first three already share `blog_publish_articles.py` (`STORES` dict), labnoir is the only fork → add a config row. (-3)
- **`cro --speed --visual`** merges **cro + landing-audit** (~80% identical fetch+BS4 scoring). (-1)
- **`rma` (report|watchdog)** merges **returns-rma-report + rma-sla-watchdog**. (-1)
- **`anne:ha-refuz` (now|trend)** merges the 2 refusal skills (identical SSH+SQLite scaffold). (-1)
- **`cs-360` (conversation|customer|order|wismo)** merges **cs-profile + cs-conversation-profile + cs-customer-360 + cs-order-status**. (-3)
- **`cs-refusals` (pre-ship|bad-address|recovery|leak)** merges **cod-confirmation + cs-address-guard + cs-refused-recovery + deliverability-monitor**. (-3)
- **`cs-watchdog` (duplicates|ghost|refund|delays)** merges **cs-duplicate-orders + cs-ghost-shipments + cs-refund-watchdog + cs-proactive-delays**. (-3)
- **richpanel-auto-triage** absorbs **richpanel-backlog-janitor** as a mode. (-1)
- meta-ads + tiktok-ads stay 2 skills but share `socialads` lib + deduped prodmap/brandmap.
- **library design family** (design, design-system, ui-styling, ui-ux-pro-max, banner-design, brand, slides) — heavy mutual overlap, but these are upstream/vendored library skills; flag for review, don't merge unilaterally.
- Leave standalone (low overlap): ads-transparency, pricewatch, ad-banners, klaviyo, merchant-center-feed, shopify-geo, shopify-knowledge-base, cs-sentiment/quality-audit/comment-intelligence (share a text-classification lib instead), cs-stock-answer, cs-procedures, cs-actions, cs-agent-performance.

## 4. Suggested rollout order (low-risk → high-value first)
1. **Cache tables** `fx_rate_daily_ron`, `daily_ad_spend_ron`, `order_enriched`, `customer_agg`, `product_refusal_rate`, `dataforseo_cache` + cron (no skill behaviour change; skills opt-in to read them). Biggest run-cost reduction.
2. **Shared libs** `pg_dsn`, `gads_client`, `fx_ron`, `metrics_db` (remove the worst duplication; mechanical, low risk).
3. **Move `daily_brand_pnl` into the warehouse**, drop the SSH dependency for multi-brand-pnl/agency-audit/daily-ops-briefing.
4. **Skill merges** one cluster at a time (articles → cro → rma → cs-360 → cs-refusals → cs-watchdog → google-ads), each via `gigi:publish-skill`, keeping old names as thin aliases for one release.

> Cross-refs: data plumbing & traps in the team memory (profitability-marketing-feed-fix, organic-traffic-analysis, cs-richpanel-pipeline-deploy). Shopify client model in shopify-seo/shopify-stores. Brand→account mapping in bi-data-integrity-check.

---

# Part 2 — Marketing clusters + CONTRADICTIONS (2026-07-24)

> The 2026-06-16 plan above covers CS/profit/ops overlap + the cache/lib layer (still valid — the CS-360 / cs-refusals / cs-watchdog / rma / articles / google-ads merges are confirmed). This part adds what it didn't: the **marketing clusters** (SEO ~33, ADS ~30) and, more importantly, **contradictions** — skills that give *opposite* answers, not just duplicate work.

## 5. ⚠️ CONTRADICTIONS (fix these first — a wrong number here loses money)

1. **P&L — 4-5 divergent "profit/delivered" definitions.** `multi-brand-pnl` (engine, DELIVERED ex-VAT) vs its own `--estimat` (daily_perf, gross-with-VAT, "OVERSTATES profit") vs `spend-pacing` MER (placed GROSS) vs `cpa-report` (Sheet, gross) vs `core:grandia-pnl` (Shopify+AWBprint) vs `ha-grandia-pnl` (profit_orders). `ops-health reconcile` exists *because* these diverge. **Fix:** one `pnl_core` (delivered, ex-VAT); deprecate `--estimat`; align spend-pacing/cpa-report/promo-profitability/cs-agent-performance on it. Fold `core:grandia-pnl`+`ha-grandia-pnl` into `multi-brand-pnl` modes.
2. **Breakeven ROAS — contradictory formula.** `ads-math`: `1/margin`. `budget-simulator`+`campaign-structure`: `1/(margin×delivery_rate)` (COD-aware). ARONA is COD with refusals (delivery<1) → `ads-math` greenlights campaigns that actually lose money (e.g. 50% margin, 70% delivery: ads-math says BE ROAS 2.0, real 2.86). **Fix:** archive `ads-math`+`ads-budget`; `budget-simulator` is canonical.
3. **Creative fatigue threshold — different cutoffs.** `creative-fatigue`: Δfreq>+15% AND (ΔCTR<−10% OR ΔCPA>+20%). `ads-creative`: CTR decline >20% = fail. Same campaign labeled differently. **Fix:** one threshold in `creative-fatigue`.
4. **Kill/scale rule.** `ads-budget`: generic "3× kill" + 70/20/10 (ignores margin & delivery). `budget-simulator`/`campaign-structure`: profit vs COD-aware breakeven. **Fix:** COD-aware wins; archive generic.
5. **Attribution model.** `ads-attribution` assumes pixel/app world (AAK iOS view-through, GA4 DDA, MMP). ARONA attributes by **campaign-name token** (`tiktok-ads`, `attribution-audit`). `ads-attribution` is inapplicable → archive.
6. **COGS — 4 definitions.** `anne-cogs-automat` writes `(cogs+ship)×USD×1.10×1.21` to Shopify; `grandia-pnl`/`fulfillment-analytics` read COGS from AWBprint; `anne:stoc-arona` from import invoices (LEI); `cod-product-validator` from category benchmark. Per-product P&L is ambiguous by source. **Fix:** declare the canonical per-SKU COGS source; others reference it.

## 6. Root cause of marketing bloat: two vendored suites installed wholesale
- **SEO suite** (`AgriciDaniel v2.2.0`, ~21 `category: seo` skills) + **ADS generic suite** (~16, `user-invokable:false`, tested 2026-05-17). Both advisory, US-centric, **do not touch the real accounts/stores**. They sit on top of the ARONA-operational skills (shopify-seo, meta/tiktok/google-ads-mcc, campaign-structure…) that actually execute. Treat each suite as ONE "audit toolkit" cited by a hub, not ~37 top-level skills.
- Broken internal ref: 6+ vendored SEO skills say "see **seo-audit**" but the installed hub is `seo` → rename `seo` → `seo-audit`.

## 7. Marketing consolidation map (SEO+ADS+content: ~55 → ~33)
- **GEO quintuplet → one `seo-geo`:** fold `ai-seo` + `shopify-geo` (mode `--score`) into `seo-geo`; strip GEO section from `seo`. (-2, biggest single bloat)
- **Pure duplicates (mechanical, zero loss):** `schema`⇔`seo-schema`, `programmatic-seo`⇔`seo-programmatic`, `seo-local`⇔`seo-maps`, `ads-test`⇔`ab-testing`. (-4)
- **SEO audit family:** `seo` (rename→`seo-audit`, hub) absorbs `seo-page`, `seo-sitemap`; keep `seo-technical`/`seo-drift`/`site-architecture`/`seo-cluster`/`seo-sxo`/`seo-hreflang` (distinct). `seo-content`+`seo-content-brief`→one; `seo-plan`→`content-strategy`; `seo-flow` redundant→archive. (-5)
- **Budget/math COD-aware:** `budget-simulator` absorbs `ads-math`+`ads-budget`. (-2, see contradiction 2)
- **Archive inapplicable-to-COD / covered:** `ads-attribution` (pixel world), `ads-competitor` (covered by `competitor-ads`), `ads-landing` (covered by `landing-audit`+`cro`). (-3)
- **Copy/creative family:** `ad-copy` absorbs `ad-creative`+`ads-create`; `creative-fatigue` absorbs `ads-creative` (align thresholds); `ads`+`ads-plan`→one. (-3)
- **Image-gen family:** collapse `ads-generate`+`ads-photoshoot`+`seo-image-gen` under one image-gen umbrella (vs `ad-banners` which stays — uses real NAS photos). (-2)
- **Articles ×4 → `articles --store <brand>`** (confirmed from Part 1; labnoir must call `ai-scrub`, not re-implement the RO blocklist). (-3)

## 8. App-factory cluster: healthy, just clarify the pre-submit gates
`app-honesty-gate` (truth + UI works) vs `shopify-app-ux-audit` (UX patterns) vs `library:shopify-app-launch` (113 Partner reqs / rejection classes) vs `shopify-app-go-public-pcd` (PCD/billing). All four are distinct — document the call order (truth-gate → UX-gate → policy-gate → PCD-gate) so they aren't run redundantly before submit. No merges.

## 9. Net
- CS/profit/ops (Part 1): ~25 → ~9. Marketing (Part 2): ~55 → ~33. **Total ~18-22 skills removable without capability loss**, plus 6 contradictions to resolve (these matter more than the count — they produce wrong numbers today).
- Nothing destructive — execute per-item after sign-off, old names as thin aliases for one release. Contradictions (§5) first, then pure duplicates (§7), then the larger folds.
