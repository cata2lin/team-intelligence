# metrics-cache/scripts — profitabilitate & cache (hartă pentru dev)

> Index complet „unde găsesc ce" pentru toată echipa: **`shared/HARTA.md`**.

## Profitabilitate — pipeline CANONIC
Regula unică: `Contribuție = Venit − COGS − Transport − Marketing`, ex-TVA pe venit/COGS/transport (TVA deductibil), marketing NET, doar comenzi LIVRATE.

| Fișier | Rol | Rulare |
|---|---|---|
| **`profit_core.py`** | **SINGLE SOURCE** — funcții canonice: `vat_for_country/prefix`, `cogs_ron` (override+RON), `parcel_transport` (cascadă **real AWBprint → media DPD → flat**), `refusal_transport_multiplier`, `is_revenue`, `allocate_marketing_by_orders` (CPA uniform), `prefix_brandid`, `PREFIX_AWB_DOMAIN`. **Orice motor de profit nou `import profit_core as pc`.** | — (lib) |
| **`profit_by_sku.py`** | P&L per SKU + rollup categorie (gold standard: transport real, marketing CPA, COGS+override, monedă→RON, reconciliat cu engine) | `python profit_by_sku.py 2026-05 --top 25` (din `/root/Scripturi`) |
| **`profit_lines_sync.py`** | Populează `profit_order_lines` (per linie sku/qty/venit/cogs din Shopify); gard fetch-incomplet | `python profit_lines_sync.py 2026-05 all` (cron 6:15) |
| **`profit_by_category.py`** | (vechi — rollup-ul pe categorie din profit_by_sku îl înlocuiește) | — |

## Cache warehouse (`cache.*` în metrics)
| Fișier | Rol | Rulare |
|---|---|---|
| **`build_cache.py`** | Materializează cache.* (PUR UPSERT — nu mai șterge istoric). Tabele cheie: `product_ad_spend` (spend per-SKU), `brand_pnl_monthly` (P&L canonic per brand, din engine), `order_outcome`, `customer_agg`… | `build_cache.py --table brand_pnl_real --apply` · `--all --apply` · `--status` |
| **`ad_spend_live.py`** | Spend Meta+TikTok per-SKU/grup → `cache.product_ad_spend` (`--platform meta\|tiktok\|both`, conturi partajate pe token global+owner) | `ad_spend_live.py --platform tiktok --since 2025-01-01 --apply` |

## Alte motoare de profit (NU aici — vezi HARTA.md)
- Engine per-brand: `/root/Scripturi/api/profitability.py` → `cache.brand_pnl_monthly` → `gigi:multi-brand-pnl`.
- Grandia: `core:grandia-pnl`. HA vs Grandia: `gigi:ha-grandia-pnl`. Breakeven: `gigi:fulfillment-analytics/breakeven.py`. Trendyol/simulator: VPS.

> Lecții/capcane (cache fail-safe, token Shopify permanent, conturi TikTok partajate): `kb.py resource-list`.
