---
name: tiktok-ads
description: Read AND operate TikTok Ads for any team brand. Read — accounts, performance reports at account/campaign/adgroup/ad level (spend, purchases, revenue, ROAS, CPA, CTR, CPM), daily trend, and `list` (ids/status/budget). Write — gated mutations: pause/activate a campaign and set its budget (DRY-RUN by default; `--apply` to execute). Brand→accounts uses the canonical Mapping (brand_map.json); the key trick: a single TikTok advertiser (e.g. "ROSSI Nails Romania") runs SEVERAL brands, so spend is attributed by a campaign-name token (e.g. 'GT','APRECIAT','COVORIA'). Creds (advertiser_id + token) from the `metrics` DB. Use to answer "how is brand X doing on TikTok", pull TikTok spend/ROAS, or pause/scale TikTok campaigns. Also `products` — per-product spend split TEST vs SALES (Nomenclator/HA mapping). All amounts in RON (per-day BNR FX from AWBprint.exchange_rates). Companion to `gigi:meta-ads` and `gigi:google-ads-mcc`.
---

# TikTok Ads (read + gated writes)

TikTok ad accounts + tokens live in the **`metrics` DB** (`tiktok_ad_accounts` →
`tikTokAccountId` = advertiser_id, `currency`, `tokenId`; `tiktok_access_tokens` → `accessToken`,
`isActive`, `needsReauth`). Calls go to the **TikTok Business API v1.3**
(`business-api.tiktok.com`, header `Access-Token`). No per-account login.

## The shared-account model (this is the whole point)
Unlike Meta/Google where accounts are mostly dedicated, **one TikTok advertiser can run many brands**.
The canonical **Mapping** sheet captures it; `brandmap.py` resolves a brand to its accounts, and for any
account **also owned by another brand** it attaches the brand's **campaign-name token** (col `Campanie`).
The tool then keeps only campaigns whose name contains that token.

- `george talent` → **ROSSI Nails Romania**, filter **`GT`** → only `…- GT - …` campaigns counted.
- `apreciat` → its own accounts (all campaigns) **+** ROSSI Nails Romania filtered **`APRECIAT`**.
- `belasil` → `Belasil.ro`, `Belasil 2` (dedicated, no filter).

Without this, ROSSI Nails Romania's spend would be wrongly attributed to one brand. Account-level reports
are computed from filtered **campaign** data so totals stay correct on shared accounts.

## Setup (creds/config from the KB — nothing hardcoded)
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export DATABASE_URL_METRICS="$(uv run "$KB" secret-get DATABASE_URL_METRICS)"
uv run brandmap.py sync     # refresh brand_map.json from the Mapping sheet (GA4_SA_JSON + MAPPING_SHEET_ID from KB)
```
`brandmap.py`/`brand_map.json` are shared with `gigi:meta-ads` (same Mapping source); each carries the
TikTok `campaign_token` per brand. Refresh after the sheet changes.

## Read
```bash
uv run tiktok.py accounts "george talent"                         # accounts + which are shared (filter token)
uv run tiktok.py report belasil --level campaign --range last_30d --sort roas
uv run tiktok.py report "george talent" --level campaign           # only GT campaigns on the shared advertiser
uv run tiktok.py report esteban --level account --range last_14d    # per-account totals (filter-correct)
uv run tiktok.py trend belasil --range last_14d                     # daily spend/ROAS
uv run tiktok.py list belasil                                       # campaign ids · status · budget · name
uv run tiktok.py products magdeal --range last_30d                  # spend per product, VÂNZARE vs TEST
```
Ranges: `today`,`yesterday`,`last_7d`,`last_14d`,`last_30d`,`last_90d`,`this_month`, or `"2026-05-01,2026-06-11"`.
Metrics use TikTok `complete_payment` (purchases) and `complete_payment_roas`; revenue = spend×ROAS.
**All output is in RON.** USD/EUR/… accounts are converted **per day** using the dynamic BNR rates in
`AWBprint.exchange_rates` (`rate/multiplier`, forward-filled across weekends — same source as `grandia_pnl`),
read via `DATABASE_URL_AWBPRINT` (from the KB). If that DB is unreachable it falls back to the fixed
`CURRENCY_RATES_RON` KB config. Verified vs the team's "Raport Zilnic 2": Belasil/GT/Magdeal match to the
leu; Nubra (USD) lands within ~0.3% (we use the real daily rate, the sheet a fixed 4.55).

## Products & TEST vs SALES (`products`)
For multi-product accounts, `products` maps each campaign to a **product** (a `HA-<digits>` code in the
campaign name, else the **Nomenclator** rules — `ACCOUNT`/`CAMPAIGN_KEYWORD`/`AD_KEYWORD` accent-insensitive
substring; sync: `uv run prodmap.py sync`) and separates **TEST** (campaign name contains "TEST") from
**VÂNZARE (sales)**. Shared with `gigi:meta-ads` (same `prodmap.py` / Nomenclator). Most TikTok brands map
brand-level (`Unmapped`) — the per-product split is mainly an FB/Reflexino thing — but the command works on
any brand and the totals stay correct.

## Mutations (writes) — DRY-RUN by default, `--apply` to execute
TikTok has no server-side validate-only, so dry-run just **shows current vs intended** and makes no change.
```bash
uv run tiktok.py pause    belasil <campaign_id>                 # DISABLE the campaign
uv run tiktok.py activate belasil <campaign_id>                 # ENABLE
uv run tiktok.py budget   belasil <campaign_id> --daily 300     # set campaign budget (account currency)
#   ...add --apply to actually execute
```
The tool **auto-finds the owning advertiser + token** for the campaign id. `list` first to get ids.

## Guardrails
- **Writes are DRY-RUN by default**; require `--apply` AND user confirmation before changing a live campaign (real spend; some brands do 800+ orders/day).
- **Never print** the access token — read from DB, used in-process.
- **Shared advertisers:** always trust the campaign-token filter; never report/mutate another brand's campaigns on a shared account. If a brand's mapping account name doesn't match a DB account, the tool warns (`cont din mapping fără potrivire în DB`) — fix the sheet/DB name.
- Per-account **currency**; `--daily` is in the account currency.
