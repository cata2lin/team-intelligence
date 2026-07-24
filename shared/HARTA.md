# Hartă: unde găsesc ce (date, rapoarte, scripturi, skilluri)

> 🎧 **Task de Customer Service?** (comandă/client după telefon/nume/order#/AWB, status, profil, anulare/AWB/adresă/factură, print depozit, tichete) → **mergi direct la `shared/CS.md`** (hartă CS dedicată, cu exemple). Aici e doar partea non-CS (date/profitabilitate/scripturi).

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
> ⚠️ **DATELE: cache-urile derivate MINT per-brand — la verdict pe BANI confirmă la SURSĂ** (dovedit iun-2026, vezi [[profit-data-sources-truth]]):
> - **LIVRATE / venit livrat** → **AWBprint** (`gigi:fulfillment-analytics`). Engine-ul/`multi_brand_pnl --range` SUB-numără livratele pe fereastră recentă (~1.6×: Esteban 5.713 engine vs 10.079 real) → profit FALS-negativ. NU judeca luna curentă la mijloc pe „livrate" din engine.
> - **SPEND Meta/TikTok per brand** → `cache.daily_ad_spend_ron` (warehouse) e **CORECT** — aplică Mapping-ul (cont/token din sheet „CPA și financiar", tab Mapping). ⚠️ **NU re-deriva din spend BRUT pe cont** (Graph API): conturile-s PARTAJATE (contul „Esteban 3" = OFERTELE, nu Esteban!) → suma pe cont ≠ atribuirea pe brand. ✅ **Grandia Meta = REPARAT 29-iun** (era `×curs_USD_zilnic` spurios pe FB în tabul „Grandia" din „CPA si financiar" — FB e RON, nu USD; scos ×curs pe 232 rânduri + MDC/cache refresh → warehouse acum **51k corect**; durabil via Curs valutar **D17='DA'**+**E17='DA'**, vezi [[profit-data-sources-truth]]). Excepție rămasă: **Belasil TikTok token-GOL** înghite Esteban (split, suma neschimbată). Verificare = `meta-ads`/`tiktok-ads` sau aplică Mapping-ul, NU spend brut pe cont.
> - **COGS** → Shopify `variants.costPerItem` (SKU în `line_items→inventory_item→>'sku'`; engine sub-numără: 37% vs real 46%).
> - Regula de aur: **livrare→AWBprint, COGS→Shopify, transport→cascadă profit_core, spend→warehouse (aplică Mapping-ul, NU spend brut pe cont)**. Portofoliu iun 1-15 = **+513k** (engine −201k = greșit din sub-numărare livrate + Grandia over).

**Regula unică**: `Contribuție = Venit − COGS − Transport − Marketing`, **ex-TVA** pe venit/COGS/transport (TVA deductibil), marketing NET, **doar comenzi LIVRATE**. Logica e într-un singur loc:

- **`profit_core.py`** (metrics-cache/scripts + `/root/Scripturi/profit_core.py`) = **SINGLE SOURCE**. Funcții: `vat_for_country/prefix` (RO.21/BG.20/CZ.21/PL.23/HU.27/SK.23/HR.25), `cogs_ron` (override+conversie RON), `parcel_transport` (cascadă: **real_cost → media DPD nomenclator → flat**; `real_cost` = **`orders.transport_cost` autoritativ per comandă, NU suma `order_awbs`**), `refusal_transport_multiplier` (orice colet plecat ×1; **NU ×2** pe intl — intl deja mai scump, RO n-are cost retur), `is_revenue`, `allocate_marketing_by_orders` (CPA uniform), `prefix_brandid`, `PREFIX_AWB_DOMAIN`. **Orice motor de profit nou IMPORTĂ asta.**
- **Per SKU / per categorie** → **`profit_by_sku.py 2026-05`** (canonic, gold standard): venit RON ex-TVA reconciliat cu engine-ul, COGS+override, **transport REAL** (`orders.transport_cost` autoritativ /1.21), marketing alocat pe comenzi, cadou numărat. Rulează din `/root/Scripturi`. (`profit_lines_sync.py <lună> all` populează liniile; cron 6:15.)
  - **Marketing per-SKU TOKEN-INDEPENDENT** (când tokenul Meta pică): de la cutover **2026-06-19** = sheet-ul WMS (FB+TikTok per-ad, conector direct) prin `wms_ad_spend_sync.py` (cron orar) → `wms_marketing.py` (SKU exact HA-#### → keyword produs din campanie+ad fără diacritice → cont brand, alocat pe comenzi); **istoric < cutover + Google = `cache.product_ad_spend`**. ~99.6% acoperire. Vezi memoria [[wms-per-sku-marketing]].
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
- **Segmente clienți / RFM / LTV / retenție / churn / forecast cerere** → `gigi:data-analytics` (pe AWBprint *delivered* = venit COD real, identitate=email, per magazin/monedă; NU pe Shopify brut care include refuzurile).
- **Livrabilitate/refuz/COD/transport** → `gigi:fulfillment-analytics` / `gigi:deliverability-monitor`.
- **Cât/când reaprovizionez (PO planner)** → `gigi:reorder-planner` (viteză din inventory_daily_snapshots, reorder_qty + dată stockout per SKU; stock-restock-alerts = doar alertă, ăsta = cantitate).
- **Ce marfă vine, ÎN CE CONTAINER, câte bucăți / „a mai fost comandat?"** → `gigi:inbound-containers`. ⚠️ **TOM nu e sursa de adevăr pt CONȚINUT**: liniile trecute pe tabele de mărimi ajung `CANCELLED` („Use of tables in multiple sizes") și marfa e totuși produsă — packing list-ul real e **fișierul de containere KDocs** (o foaie/container, `#9`…`#59`; view-only + WASM → se citește vizual prin chrome-devtools). TOM îți dă doar scheletul (`shipments` = „Container 43-44-45", toate `DRAFT`, fără date). Vezi [[container-pipeline-kdocs]].
- **Pacing buget ads + MER pe lună** → `gigi:spend-pacing` (spend din cache.daily_ad_spend_ron token-independent, proiecție run-rate, MER per brand/canal). Snapshot zilnic = daily-ops-briefing.
- **Saturație audiență / refresh creative (Meta+TikTok)** → `gigi:creative-fatigue` (freq↑+CTR↓/CPA↑ la nivel de cont; drill per-creativ via meta-ads/tiktok-ads).
- **Promo COD (2+1) face bani?** → `gigi:promo-profitability` (contribuție netă/comandă pe unități/comandă, AWBprint delivered, COGS pe toate unitățile incl gratis).
- **COGS/preț/stoc Shopify** → `gigi:shopify-stores`.

## 🎯 Target CPA + VERDICT PE PROFIT per magazin
> ⚠️ **Reașezat pe PROFIT (9-iul-2026):** CPA-target agresiv (15/20) eticheta greșit PMax profitabil drept „scump". Pt **scale/cut pe PMax/Shopping/all-channel judecă pe PROFIT vs breakeven REAL**, nu pe CPA-target.

**Verdict pe profit (canonic):** `gigi:google-ads-mcc/profit_verdict.py` → SCALE/HOLD/CUT per campanie, cu breakeven real din `brandref` (`breakeven_cpa`/`scale_cpa`/`breakeven_roas`, populat din `gigi:fulfillment-analytics/breakeven.py --store all`). Zone: **CPA ≤ `scale_cpa`(=BE_CPA×0.7)=SCALE** · scale<CPA≤`breakeven_cpa`=HOLD · CPA>BE=CUT. ⚠️ ROAS Google umflat ~1.5× → gate-ul robust = CPA. Dovadă: Gento/GT PMax „unprofitable" pe CPA-target dar SCALE pe profit; Carpetto = CUT real.

**Target CPA (aprobat 2026-06-27) = DOAR pt Search NON-BRAND** (eficiență). Pe **CPA / comandă PLASATĂ**. **Regulă: Google = CPA MAI MIC decât Social** (prinde brand + intenție mare, ieftin). Sursă: `brandref` (`target_cpa_social`/`target_cpa_google`). Detalii + breakeven în [[target-cpa-per-store]].

| Magazin | Social (Meta/TikTok) | Google |
|---|--:|--:|
| esteban.ro · georgetalent.ro · nubra.ro · apreciat.ro | 25 | 15 |
| reduceribune.ro · belasil.ro | 28 | 18 |
| ofertelezilei.ro · magdeal.ro · casaofertelor.ro · gento.ro · carpetto.ro | 30 | 20 |

> Google ≈ 65% din target-ul social. Brand Search = lasă-l să culeagă tot (nu plafona). Grandia = agenție (rula ~140 vs breakeven ~69 = pierde; doar observăm). Implementare: Google `gads.py set-tcpa` (după 15-30 conv); Meta/TikTok cost-cap (`gigi:meta-ads`/`tiktok-ads`) sau prin agenție (`gigi:agency-audit` — ei dau 75-95% din spend).

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
> - **subsistemul e-Transport ANAF / SmartBill** (CLI `python -m etransport.main`, 47 module: parsers/services/exporters/catalogs) → `shared/etransport/`. Credențiale din env (`SMARTBILL_EMAIL/TOKEN/CIF`, OpenAI key ca param), DB = SQLite local. Deploy: `scp -r shared/etransport $VPS:/root/Scripturi/`.
> - Aplicația web (routes/models/dashboard) + modulele importate (serial_refuser, shipment, validation_service) + `data/` (utilitare one-off) **rămân pe VPS** — nu se urcă.

## 🛡️ Monitorizare + operare (VPS) — „ce se strică tăcut" prins automat
> De ce: eșecurile ARONA erau TĂCUTE (token Meta mort 11 zile, cron TikTok oprit necunoscut, `profit_orders`
> nesincronizat 23 zile → brand_pnl iulie −1,5M fals). Acum sistemul se plânge singur. **Email DOAR pe erori reale/noi.**

| Tool (`shared/scripturi-tools/`, rulează pe VPS) | Ce face | Cron |
|---|---|---|
| `data_health.py` | Watchdog prospețime DATE (verifică ieșirea pipeline-urilor vs SLA, nu „a rulat jobul"): spend/brand_pnl/fx/tokenuri/sync_runs/AWBprint/WMS/profit_orders + **heartbeat cronuri**. Email pe roșu. | `15 9 *` |
| `reconcile_sources.py` | Reconciliere 3-surse (engine↔AWBprint livrate, sheet↔warehouse marketing) + istoric drift în `recon_history`. Email DOAR pe drift NOU. | `30 9 *` |
| `deploy_parity.py` | Paritate cod git(origin/main)↔fișiere flat VPS (`check`/`deploy`). Email pe fișier nou-divergent. Prinde cauza bombelor de drift. | `45 9 *` |
| `heartbeat.py` | Dead-man-switch: cronurile pinguie pe SUCCES (`&& heartbeat.py <nume>`); `data_health` semnalează „n-a rulat deloc". Cablat pe 9 cronuri pipeline. | — |
| `backup_profitdb.py` | Backup CONSISTENT (SQLite online-backup API) + gzip + rotație(7) al `profitability.db` (333MB→60MB). | `30 3 *` |
| `deploy.sh` | Deploy GIT-DRIVEN sigur: `git fetch` + sync flat (via parity, cu `.bak`) + `pull --ff-only` checkout. Înlocuiește scp manual. | `ssh <vps> 'bash /root/Scripturi/deploy.sh --apply'` |

> **Deploy corect = `deploy.sh --apply`**, NU scp manual (scp-ul manual = cauza divergențelor git↔VPS). Vezi memoria [[data-health-watchdog]].

## Lecții/capcane salvate (în KB: `kb.py resource-list`)
- **Cache/backfill FAIL-SAFE**: nu face DELETE pe istoric apoi reinsert condiționat — un pull eșuat șterge tot. Pur upsert. Învelește apelurile externe în retry-on-timeout (googleapiclient aruncă TimeoutError brut, nu RequestException).
- **Shopify token permanent (`shpat_`)**: nu-l trece prin OAuth refresh; setează expires_at în viitor.
- **TikTok conturi partajate**: split pe token de brand din numele campaniei + owner; vezi memoria.
- **Meta = single point of failure pe UN token OAuth**: tot spend-ul Meta (toate brandurile — 122 conturi în `meta_ad_accounts`, 25 active) trece printr-un singur token „OAuth — Sabina Radu" din `meta_access_tokens`, long-lived ~60 zile. Când expiră, **TOT** Meta se oprește tăcut: toate `lastSyncAt` blocate în aceeași zi, `cache.product_ad_spend` platform=meta nu mai înaintează, `sync_runs` = FAILED cu „Token for <Account> has expired" (numește un cont la întâmplare, dar pică toate). Incident 2026-06-19 → tot Meta stale. Fix = re-autorizare OAuth (Sabina Radu re-login) + backfill. **Alert proactiv pe cron**: `metrics-cache/scripts/check_token_expiry.py` (prag implicit 7 zile). Capcană înrudită: `campaignFilter` gol pe un cont = tot contul intră pe un singur brand (ex. Reflexino→Magdeal).

## Cum descoperi mai mult
- Skill-uri: catalogul auto din CLAUDE.team.md (sau descrie task-ul). Activitate/resurse: `kb.py recent` / `kb.py resource-list`.
