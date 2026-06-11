---
name: analytics
description: Pull website traffic & organic-traffic analytics for the team's Shopify stores — primarily Google Analytics 4 (sessions, users, conversions by channel, with Organic Search/Social/Shopping cleanly separated from Paid Search/Shopping/PMax/Social), with a Shopify-analytics fallback (metrics DB) for stores that don't have GA4 history yet. Use for any "traffic analysis", "organic / SEO traffic", "channel mix", "where do sessions come from", "how much is paid vs organic", or "conversions by source" question on Grandia, Esteban, George Talent, Nubra (and other brands as GA4 is connected).
argument-hint: "channels --brand <esteban|grandia|nubra|george-talent> [--start --end]"
---

# Analytics — GA4 traffic & organic analysis
> Author: Gigi.

Two sources, pick per brand:
- **GA4 (preferred)** — clean channel grouping (Organic Search vs Paid Search vs **Cross-network/PMax** vs Paid Shopping vs Organic Shopping vs Paid/Organic Social), per-channel **conversions** (key events), full history. Use whenever the brand has GA4 data.
- **Shopify analytics (fallback)** — `metrics` Postgres DB, only for brands whose GA4 was installed too recently to have history. Coarser attribution, **conversions broken**.

## Credentials (don't print secrets)
GA4 is read with the shared **`looker-sheets` service account** (`looker-sheets@rising-hallway-462906-g7.iam.gserviceaccount.com`, the same SA the Sheets scripts use). Its JSON key lives in the knowledge base secret **`GA4_SA_JSON`**. The SA must be a **Viewer** on each GA4 property (granted per Google account/property in GA4 → Admin → Access Management). Scope used: `analytics.readonly`.

```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
```
All scripts run with `uv` (deps inline). The GCP project `rising-hallway-462906-g7` has **Analytics Data API + Admin API** enabled.

## GA4 — `ga4.py`
```bash
uv run ga4.py properties                                   # every property the SA can read + IDs
uv run ga4.py channels  --brand esteban                    # session mix (sessions/users/conversions), last 90d
uv run ga4.py channels  --all --start 2026-03-01 --end 2026-06-10
uv run ga4.py economics --brand esteban                    # sessions + CVR + revenue + rev/session per channel
uv run ga4.py economics --all                              # the "which channel actually makes money" view
uv run ga4.py landing   --brand esteban                    # top landing pages for Organic Search (+ conv/revenue)
uv run ga4.py landing   --brand grandia --channel "Paid Shopping" --limit 20
uv run ga4.py trend     --brand grandia                    # monthly Organic Search
uv run ga4.py trend     --brand nubra --weekly --channels "Organic Search,Organic Social"
```
Common flags: `--brand <name>` or `--property <id>`; `--all` (channels/economics); `--start/--end` (default last 90 days, yesterday-anchored). `channels --metrics` overrides metric list. `landing --channel`/`--limit`. `trend --weekly` and `--channels "A,B"`.

| Command | What you get | Why it matters |
|---|---|---|
| `channels` | sessions/users/conversions per channel | quick traffic mix, organic vs paid |
| **`economics`** | sessions + **CVR + revenue + revenue-share + rev/session** per channel | the decision view — organic usually has the **highest rev/session** even when it's a small slice of traffic |
| **`landing`** | top landing pages for a channel, with conversions + revenue | *which pages* pull organic (separates brand-search homepage from category/product pages) |
| `trend` | monthly or `--weekly` sessions per channel | organic momentum; watch for a partial final week |

> Conversions (`keyEvents`) and revenue (`purchaseRevenue`, `ecommercePurchases`) come from GA4's ecommerce tracking — populated for Esteban & Grandia. **This is exactly what Shopify could not give** (its per-channel conversion column is all-zero).

### Brand → GA4 property
| Brand | Property ID | GA4 status |
|---|---|---|
| Esteban | `510626424` | ✅ full history |
| Grandia | `510760223` | ✅ full history |
| George Talent | `541255080` | ⏳ installed Jun 2026 — little/no history yet → use Shopify |
| Nubra | `541249929` | ⏳ installed Jun 2026 — little/no history yet → use Shopify |

Each store is a **separate GA4 account**, so granting the SA Viewer is per-account. To connect a new brand: add the SA email as Viewer in that GA4 account, then `uv run ga4.py properties` to grab the new property ID and add it to `BRANDS` in `ga4.py`.

### Reading the channel groups
- **Organic** = `Organic Search` (SEO blue links) + `Organic Social` + `Organic Shopping` (free Google listings). `AI Assistant` = LLM/ChatGPT referrals (emerging, tiny).
- **Paid** = `Paid Social` + `Paid Search` + `Paid Shopping` + **`Cross-network`** (Performance Max — spans Search/Shopping/Display/YouTube) + `Paid Other`.
- **Other** = `Direct`, `Referral`, `Email`, `Unassigned`.
> ⚠️ For PMax-heavy brands (e.g. Grandia) `Cross-network` + `Paid Shopping` are the bulk of "Google" traffic and are **paid** — do not mistake them for organic. This is exactly the trap Shopify falls into (see below).

## Shopify fallback — `metrics` DB (only when GA4 has no history)
Query via the `postgres-metrics` MCP. Table **`shopify_analytics_traffic_daily`** (`brandId, date, utmSource, utmMedium, referrerSource, sessions`). Brand IDs: Grandia `cmo5ulyl80003h1w2xlzfzhvh`, Esteban `cmo5v89380001fzw2jii507fk`, George Talent `cmo8ocp3l000504l7ikr6s94q`, Nubra `cmo8odsm6000804l729wajk3p` (full list in `brands`). Channel CASE (values lowercase):
- Organic search (SEO) = `referrerSource='search' AND COALESCE(utmMedium,'')=''`
- Organic social = `referrerSource='social' AND utmMedium<>'paid'`
- Paid social = `social + utmMedium='paid'`; Paid other = `utmMedium IN ('paid','cpc')`
- `referrerSource='search' AND utmMedium='product_sync'` = Google Shopping feed — **mostly PAID Shopping/PMax** (validated against Google Ads clicks; GA4 confirms it splits into Cross-network + Paid Shopping). **Exclude from organic.**
- Direct = `direct` no UTM; Email = `email`

**Shopify gotchas (state them in any report):**
1. **`converted` column is all-zero (broken)** → no per-channel conversions/CVR from Shopify; sessions only.
2. **Uneven sync coverage** — confirm `MAX(date)` per brand and reconcile vs `shopify_analytics_daily` on overlapping dates before trusting %. (Historically Grandia channel data went stale, Esteban had only an 8-day window; GT/Nubra were current to the prior day.)
3. **`product_sync` ambiguity** — see above; it's paid, not organic.

## Validation note (Jun 2026)
GA4 vs Shopify on Esteban agreed closely (organic share 11.7% vs 11.6%, paid social 78% vs 77.6%) — so the two sources are directionally consistent, but **don't mix them at decimal precision** in one comparison; label the source per brand. GA4's edge: it separates PMax/Shopping correctly and gives conversions.
