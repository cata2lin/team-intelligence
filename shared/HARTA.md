# Hartă: unde găsesc ce (date, rapoarte, scripturi, skilluri)

> Index **hand-maintained** ca ORICE coleg/agent să știe rapid **unde caută și ce folosește**.
> Regula de aur: înainte să improvizezi un query/script, caută aici intenția. Adaugi o capabilitate nouă → **trec-o și aici**.
> ⚠️ Fișier SEPARAT de CLAUDE.team.md fiindcă sync-ul de catalog regenerează CLAUDE.team.md și ștergea Harta. Loadează-l cu `@shared/HARTA.md` sau citește-l direct.

## Bazele de date (ce ține fiecare)
| DB | Acces | Ce conține | Capcane |
|---|---|---|---|
| **AWB Arona** (`awb.arona.ro`) | secret `DATABASE_URL_AWBPRINT` | **Sursa de adevăr livrare + transport REAL**: `orders` (status, `total_price`, `line_items`, `currency`, `store_uid`, **`transport_cost`** = cost transport AUTORITATIV per comandă, gross, ~87% acoperire — UN AWB principal/comandă, exact cum e urcat în AWB Arona), `order_awbs` (detaliu per-AWB; rânduri multiple = în mare DUPLICATE → **NU suma**), `stores` (`uid`,`name`=domeniu public). | folosește **`orders.transport_cost`** (NU suma order_awbs — duplicatele multiplică); e gross → `/1.21` = ex-TVA (TVA transport = RO 21%, curierul e RO); `shopify_domain` e NULL → mapează prefix→domeniu (profit_core.PREFIX_AWB_DOMAIN); internațional în valută locală |
| **metrics** | MCP `postgres-metrics` (RO) | Ad-spend (`*_insights_daily`), **`cache.product_ad_spend`** (spend per-SKU Meta+TikTok+Google), `cache.brand_pnl_monthly` (P&L canonic per brand), `fx_rates`, `brands` | conturi partajate TikTok = split pe token în nume; ad-spend sync parțial |
| **profitability.db** (SQLite, VPS `/root/Scripturi/data/`) | engine `api.profitability` | `profit_orders` (per comandă: revenue=total în moneda magazinului, cogs, status_category, currency), `profit_order_lines` (per linie: sku/qty/line_revenue/line_cogs), `profit_cogs_override`, `profit_exchange_rates` (rate_to_ron/lună), `profit_transport_costs` | revenue/cogs în MONEDA magazinului (convertește cu rate_to_ron!); status livrare se stabilește prin maparea curier |
| **Shopify** (per magazin) | `gigi:shopify-stores` | COGS (unitCost), produse, comenzi LIVE | unitCost în moneda magazinului; CZ/PL n-au unitCost setat; NUB token OAuth (e ok acum, expires 2099) |
| **KB SharedClaude** | `core:knowledge-base` (`kb.py`) | secrete, log activitate, fișiere, **resurse/lecții** (`resource-list`) | secretele DOAR aici |

## PROFITABILITATE — pipeline CANONIC (citește asta înainte de orice calcul de profit)
**Regula unică**: `Contribuție = Venit − COGS − Transport − Marketing`, **ex-TVA** pe venit/COGS/transport (TVA deductibil), marketing NET, **doar comenzi LIVRATE**. Logica e într-un singur loc:

- **`profit_core.py`** (metrics-cache/scripts + `/root/Scripturi/profit_core.py`) = **SINGLE SOURCE**. Funcții: `vat_for_country/prefix` (RO.21/BG.20/CZ.21/PL.23/HU.27/SK.23/HR.25), `cogs_ron` (override+conversie RON), `parcel_transport` (cascadă: **real_cost → media DPD nomenclator → flat**; `real_cost` = **`orders.transport_cost` autoritativ per comandă, NU suma `order_awbs`**), `refusal_transport_multiplier` (orice colet plecat ×1; **NU ×2** pe intl — intl deja mai scump, RO n-are cost retur), `is_revenue`, `allocate_marketing_by_orders` (CPA uniform), `prefix_brandid`, `PREFIX_AWB_DOMAIN`. **Orice motor de profit nou IMPORTĂ asta.**
- **Per SKU / per categorie** → **`profit_by_sku.py 2026-05`** (canonic, gold standard): venit RON ex-TVA reconciliat cu engine-ul, COGS+override, **transport REAL per-AWB**, marketing alocat pe comenzi din `cache.product_ad_spend`, cadou numărat. Rulează din `/root/Scripturi`. (`profit_lines_sync.py <lună> all` populează liniile; cron 6:15.)
- **Per brand (P&L canonic)** → engine `api/profitability.py` (rulează pe VPS; **acum versionat în git: `metrics-cache/engine/profitability.py`** — editezi în git, deployezi pe VPS, vezi `engine/README.md`) → `cache.brand_pnl_monthly` → **`gigi:multi-brand-pnl`**. Refresh: `build_cache.py --table brand_pnl_real --apply`.
- **Grandia P&L** → `core:grandia-pnl` / `grandia_pnl.py` (transport REAL per-AWB, gold standard pe Grandia).
- **HA vs Grandia** → `gigi:ha-grandia-pnl`.
- **Breakeven CPA/ROAS per brand** → `gigi:fulfillment-analytics` → `breakeven.py` (model de PLANIFICARE: COGS% + transport median — NU actuals).
- **Trendyol** (marketplace) → `trendyol_profitability.py` (VPS).
- **Simulator prag/CPA pre-lansare** → `product_profit_calculator.py` (VPS) — NU e profit realizat.
> Tier 2 (multi-brand-pnl, daily-ops, agency-audit, product-matrix, grandia-product-marketing) **moștenesc** engine-ul prin `cache.brand_pnl_monthly`.

## „Vreau …" → unde mă duc
- **Profit/contribuție per SKU sau categorie** → `profit_by_sku.py` (NU fulfillment-analytics — ăla e breakeven/AWB analytics).
- **P&L per brand / „% din profit"** → `gigi:multi-brand-pnl` (citește cache.brand_pnl_monthly, canonic).
- **Spend ads per SKU/brand** → `cache.product_ad_spend` (Meta+TikTok via reguli KB + Google PMax). Live: `gigi:meta-ads`/`tiktok-ads`/`google-ads-mcc`.
- **Cost transport real per comandă** → AWBprint **`orders.transport_cost`** (autoritativ, UN AWB principal/comandă; gross → `/1.21` = ex-TVA). **NU** suma `order_awbs` (rânduri duplicate). Fallback `MAX(transport_cost_fara_tva)` → flat (vezi profit_core.parcel_transport).
- **Bucăți vândute per produs** → `gigi:product-sales`.
- **Livrabilitate/refuz/COD/transport** → `gigi:fulfillment-analytics` / `gigi:deliverability-monitor`.
- **COGS/preț/stoc Shopify** → `gigi:shopify-stores`.

## Scripturi cheie (toate cu `uv run` / `.venv/bin/python` din folderul lor)
| Script (skill/loc) | Ce face | Exemplu |
|---|---|---|
| `profit_core.py` (metrics-cache) | Biblioteca CANONICĂ vat/cogs/transport/marketing/status — **importă, nu rescrie** | `import profit_core as pc` |
| `profit_by_sku.py` (metrics-cache / VPS) | P&L per SKU + rollup categorie (transport real, marketing CPA) | `profit_by_sku.py 2026-05 --top 25` |
| `profit_lines_sync.py` (metrics-cache / VPS) | Populează profit_order_lines (per linie, din Shopify) | `profit_lines_sync.py 2026-05 all` |
| `build_cache.py` (metrics-cache) | Materializează cache.* (incl. brand_pnl_monthly, product_ad_spend) — **pur upsert, nu mai șterge istoric** | `build_cache.py --table brand_pnl_real --apply` |
| `ad_spend_live.py` (metrics-cache) | Spend Meta+TikTok per-SKU → cache.product_ad_spend (`--platform meta|tiktok|both`) | `ad_spend_live.py --platform tiktok --since 2025-01-01 --apply` |
| `grandia_pnl.py` (core) | P&L Grandia (transport real AWBprint) | vezi `core:grandia-pnl` |

> 📦 **Tot codul de profit + tool-urile VPS pe care le folosim sunt acum în git** (mirror, editezi în git → deployezi pe VPS):
> - **engine P&L** (`api/profitability.py`) + calculatoare (Trendyol, simulator pre-lansare) → `gigi/skills/metrics-cache/engine/` (vezi `engine/README.md`).
> - **tool-uri operaționale** (sync_raport_zilnic, sync_barcodes, sheets_labels, shopify_image_manager, shopify_tag_orders_parallel, sku_to_url, upload_shopify_img) → `shared/scripturi-tools/` (vezi README-ul de acolo).
> - Aplicația web (routes/models/dashboard) + modulele importate (serial_refuser, shipment, validation_service) **rămân pe VPS** — nu se urcă.

## Lecții/capcane salvate (în KB: `kb.py resource-list`)
- **Cache/backfill FAIL-SAFE**: nu face DELETE pe istoric apoi reinsert condiționat — un pull eșuat șterge tot. Pur upsert. Învelește apelurile externe în retry-on-timeout (googleapiclient aruncă TimeoutError brut, nu RequestException).
- **Shopify token permanent (`shpat_`)**: nu-l trece prin OAuth refresh; setează expires_at în viitor.
- **TikTok conturi partajate**: split pe token de brand din numele campaniei + owner; vezi memoria.
- **Meta = single point of failure pe UN token OAuth**: tot spend-ul Meta (toate brandurile — 122 conturi în `meta_ad_accounts`, 25 active) trece printr-un singur token „OAuth — Sabina Radu" din `meta_access_tokens`, long-lived ~60 zile. Când expiră, **TOT** Meta se oprește tăcut: toate `lastSyncAt` blocate în aceeași zi, `cache.product_ad_spend` platform=meta nu mai înaintează, `sync_runs` = FAILED cu „Token for <Account> has expired" (numește un cont la întâmplare, dar pică toate). Incident 2026-06-19 → tot Meta stale. Fix = re-autorizare OAuth (Sabina Radu re-login) + backfill. **Alert proactiv pe cron**: `metrics-cache/scripts/check_token_expiry.py` (prag implicit 7 zile). Capcană înrudită: `campaignFilter` gol pe un cont = tot contul intră pe un singur brand (ex. Reflexino→Magdeal).

## Cum descoperi mai mult
- Skill-uri: catalogul auto din CLAUDE.team.md (sau descrie task-ul). Activitate/resurse: `kb.py recent` / `kb.py resource-list`.
