---
name: analytics
description: Pull website traffic, organic-traffic & SEO analytics for the team's Shopify stores from THREE sources — Google Analytics 4 (sessions, users, conversions & revenue by channel, with Organic Search/Social/Shopping cleanly separated from Paid Search/Shopping/PMax/Social), Google Search Console (real search QUERIES/keywords, impressions, clicks, CTR, average POSITION — the true SEO data GA4 can't give, and it works even when a store's GA4 tag is broken), and a Shopify-analytics fallback (metrics DB). Use for any "traffic analysis", "organic / SEO traffic", "channel mix", "where do sessions come from", "how much is paid vs organic", "conversions/orders by source", "what keywords do we rank for", "search queries", "average position", "Search Console" question on Esteban, Grandia, Nubra, George Talent, Belasil, Gento, Covoria, Nocturna, and other team brands.
argument-hint: "channels|economics|landing|trend --brand <name>  ·  gsc.py queries --brand <name>"
---

# Analytics — GA4 traffic + Search Console SEO + organic analysis
> Author: Gigi.

Three sources, pick per question/brand:
- **GA4 — `ga4.py`** — channel grouping (Organic Search vs Paid Search vs **Cross-network/PMax** vs Paid Shopping vs Organic Shopping vs Paid/Organic Social), per-channel **conversions + revenue**. The answer to "how many ORDERS came from organic / Google Ads / each channel".
- **Search Console — `gsc.py`** — real search **queries (keywords), impressions, clicks, CTR, average position**. The answer to "what do we rank for / what keywords bring us traffic". Works even when a store's GA4 tag isn't firing (e.g. George Talent).
- **Shopify analytics (fallback)** — `metrics` Postgres DB, only when a brand has no GA4 history. Coarser attribution, **conversions broken**.

## Credentials (don't print secrets)
All three are read with the shared **`looker-sheets` service account** (`looker-sheets@rising-hallway-462906-g7.iam.gserviceaccount.com`, the same SA the Sheets scripts use). Its JSON key lives in the KB secret **`GA4_SA_JSON`**. Access is granted per-property: **GA4** → add the SA as a *Viewer* (GA4 Admin → Access Management); **Search Console** → add the SA as a *Full* user (Search Console → Settings → Users and permissions — no account-level cascade, do it per site). Scopes used: `analytics.readonly` (GA4) and `webmasters.readonly` (Search Console).

```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
```
All scripts run with `uv` (deps inline). The GCP project `rising-hallway-462906-g7` has **Analytics Data API + Admin API + Search Console API** enabled.

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

### Brand → GA4 property (`ga4.py` BRANDS map)
`esteban` 510626424 · `grandia` 510760223 · `nubra` 541249929 · `george-talent`/`gt` 541255080 · `belasil` 487042770 · `gento` 486992931 · `covoria` 491785347 · `casa-ofertelor` 501613337 · `rossi` 402470642 · `nocturna` 460807314 (+ `nocturna-lux`/`-pl`/`-gr`/`-bg`).
- ✅ Confirmed with data: Esteban, Grandia, Nubra, Belasil (and most others).
- ⚠️ **George Talent: GA4 tag NOT firing** (0 sessions) → for GT use **Search Console** (works) or Shopify, not GA4.

Each store is a **separate GA4 account**, so granting the SA Viewer is per-account. To connect a new brand: add the SA as Viewer in that GA4 account, then `uv run ga4.py properties` to grab the new property ID and add it to `BRANDS` in `ga4.py`.

### Reading the channel groups
- **Organic** = `Organic Search` (SEO blue links) + `Organic Social` + `Organic Shopping` (free Google listings). `AI Assistant` = LLM/ChatGPT referrals (emerging, tiny).
- **Paid** = `Paid Social` + `Paid Search` + `Paid Shopping` + **`Cross-network`** (Performance Max — spans Search/Shopping/Display/YouTube) + `Paid Other`.
- **Other** = `Direct`, `Referral`, `Email`, `Unassigned`.
> ⚠️ For PMax-heavy brands (e.g. Grandia) `Cross-network` + `Paid Shopping` are the bulk of "Google" traffic and are **paid** — do not mistake them for organic. This is exactly the trap Shopify falls into (see below).

## Search Console — `gsc.py` (real SEO: keywords & position)
```bash
uv run gsc.py sites                            # every site the SA can read + permission
uv run gsc.py queries  --brand esteban         # top search queries (keywords), last 28 days
uv run gsc.py queries  --site grandia.ro --days 90 --limit 40
uv run gsc.py pages    --brand esteban         # top landing pages from Google search
uv run gsc.py summary  --brand grandia         # totals: clicks / impressions / CTR / avg position
uv run gsc.py summary  --all                   # one-line totals for every connected site
uv run gsc.py wow      --brand esteban         # WEEK-OVER-WEEK: last 7d vs prior 7d, brand vs non-brand + daily trend
uv run gsc.py wow      --all --days 7          # WoW one-liner for every site — "did our SEO work move anything?"
uv run gsc.py rank     --brand gt --query "parfumuri barbati|parfum barbati"   # OUR position for exact keywords + ranking page
uv run gsc.py rank     --brand esteban --contains "parfum"                      # every keyword we appear for that contains a term
uv run gsc.py opportunities --brand grandia          # non-brand QUICK-WINS: striking-distance (pos 5-20) + low-CTR keywords
uv run gsc.py opportunities --all --min-impr 50      # opportunity scan across every site
uv run gsc.py index    --brand esteban --url "https://esteban.ro/collections/dama|https://esteban.ro/"   # is the page indexed?
```
Flags: `--brand <name>` (mapped to its `sc-domain:`) or `--site <domain>`; `--days` (default 28; `wow` default 7); `--limit`. Data lags ~2–3 days (range ends 3 days ago).

| Command | What you get |
|---|---|
| `queries` | the actual keywords people Google → clicks, impressions, CTR, **avg position** |
| `pages` | which pages rank / earn organic clicks |
| `summary` | site-level SEO health (clicks/impr/CTR/position), `--all` to compare every brand |
| **`wow`** | last N days vs prior N — clicks/impr/position deltas **split brand vs non-brand**, daily trend, top non-brand queries. The "did last week's SEO work move anything?" view. Non-brand = the real SEO signal; brand-name typos (estaban/numbra/berasil…) are fuzzy-matched into *brand* so they don't fake non-brand wins. |
| **`rank`** | **OUR position** for given keywords: `--query "a\|b"` (exact, returns avg position + clicks + the page that ranks) or `--contains "term"` (every keyword we appear for containing that term). Answers "how do we situate on keyword X". *Caveat:* GSC only shows keywords we already get impressions for — `(nu apărem / 0 impr)` means we're not ranking high enough to register, NOT that we're #1. For the full SERP / who outranks us / keywords we don't appear for, you need a SERP source (see below). |
| **`opportunities`** | non-brand SEO **quick-wins**: STRIKING-DISTANCE keywords (position 5–20 with real impressions → nudge to page 1 = traffic) + LOW-CTR-despite-top-rank (pos ≤5 but CTR <20% → rewrite title/meta). Brand/typos excluded. The "where do I get the most SEO for the least effort" view. `--all`, `--min-impr`. |
| **`index`** | URL index status via GSC **URL Inspection** — `--url "https://…\|…"` → coverage state (indexed / crawled-not-indexed / excluded) + last crawl date. Use to confirm new/optimized pages got indexed before expecting them to rank. |

> **GSC `rank` vs the live SERP:** `rank` is free + authoritative for *our* position on keywords we already surface for (e.g. GT is pos ~11 on "parfumuri barbati", Grandia pos ~8–12 on product terms — page 1–2 "striking distance"). It does NOT show competitors or keywords where we don't appear. For that you need a SERP API (DataForSEO/SerpApi — paid, none configured) or a live browser SERP scrape (chrome-devtools, fragile, spot-checks only).

> ⚠️ **Reading `wow` honestly:** at perfume/brand stores ~all organic is **brand search** (incl. misspellings) driven by ad demand, not on-page SEO — judge SEO on **non-brand** clicks/impressions + position. SEO changes take 1–4 weeks; a few days post-change is an early read (impressions/position move before clicks). Grandia is the exception with real non-brand product queries.

Connected sites (all **Domain** properties, Full access): esteban, grandia, nubra, george-talent, belasil, gento, covoria, carpetto, labnoir, apreciat, casa-ofertelor (casaofertelor.ro), oriceredus, reduceribune, bonhaus.bg/cz/pl, nocturna.bg.
> **Brand vs non-brand:** GSC reveals how much "organic" is just people Googling the brand name. E.g. Esteban's top queries are ~all "esteban / esteban parfum" (position ~1, CTR 80%+) → most organic clicks are **brand search**, not non-brand SEO. Use `queries` to size that split before claiming SEO wins. **GT note:** GT has real GSC organic (thousands of clicks) even though its GA4 is dead — so GSC is the way to measure GT's organic.

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

## Domain authority — `authority.py` (Open PageRank, free)
Backlink/authority **proxy** (not a referring-domains list). Key in KB secret `OPENPAGERANK_API_KEY`.
```bash
export OPENPAGERANK_API_KEY="$(uv run "$KB" secret-get OPENPAGERANK_API_KEY)"
uv run authority.py --ours --vs notino.ro,sephora.ro,douglas.ro    # our 5 stores vs competitors
```
Returns Open PageRank score (0–10) + global rank per domain. **Finding (Jun 2026):** our stores return OPR ~0/n/a while competitors sit ~2.5 — i.e. near-zero measurable domain authority (real backlink gap) AND Open PageRank barely indexes `.ro`/newer sites, so for an actual backlink graph we still need DataForSEO/Ahrefs (paid). Use this for competitor authority benchmarking, not as our own backlink truth.

## DataForSEO — `dataforseo.py` (PAID: live SERP, competitor keywords, backlinks)
Fills the SERP + backlinks + competitor gaps. Creds in KB `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` (Basic auth, **no IP whitelist needed**). Pay-as-you-go — every call costs money; default market Romania/Romanian.
```bash
export DATAFORSEO_LOGIN="$(uv run "$KB" secret-get DATAFORSEO_LOGIN)"
export DATAFORSEO_PASSWORD="$(uv run "$KB" secret-get DATAFORSEO_PASSWORD)"
uv run dataforseo.py serp      --keyword "parfumuri barbati"   # who ranks top in Google RO (+ flags our stores)
uv run dataforseo.py keywords  --domain notino.ro --limit 40   # what a competitor ranks for (keyword mining / gap)
uv run dataforseo.py backlinks --domain esteban.ro             # backlinks + referring domains summary
uv run dataforseo.py balance                                   # account balance
```
⚠️ **Account state:** the account must be **verified** (app.dataforseo.com) and **funded** before data endpoints work — otherwise calls return `40104 "Please verify your account"` / insufficient balance (auth + `balance` still work). Run **monthly via cron + cache results in metrics** to pay per query, not per look.

## Validation note (Jun 2026)
GA4 vs Shopify on Esteban agreed closely (organic share 11.7% vs 11.6%, paid social 78% vs 77.6%) — so the two sources are directionally consistent, but **don't mix them at decimal precision** in one comparison; label the source per brand. GA4's edge: it separates PMax/Shopping correctly and gives conversions.
