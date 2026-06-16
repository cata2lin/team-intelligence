---
name: google-ads-mcc
description: Read and operate any Google Ads account linked under the team MCC (API v21) — live performance reports, budget/bidding/status/keyword/negative mutations, full Search campaign creation, and the end-to-end video pipeline (upload to YouTube + attach to Performance Max). Plus an optimization playbook and PMax asset-group asset management. Credentials (MCC developer token + OAuth refresh token) come from the `metrics` DB; no per-account login. Read-only by default; mutations are dry-run unless explicitly applied. Use for any live Google Ads work on a brand (Esteban, Belasil, Grandia, …) without screenshots.
---

# Google Ads via the team MCC

The team runs a **Google Ads Manager account (MCC)** whose API credentials live in the
**`metrics` Postgres DB**. One set of MCC credentials reads/writes **every account linked
under the MCC** — you only need the child account's **customer ID**.

> **MCC (login-customer-id): `7467110480`** (NOVOS DIGITAL SRL)

## Connected accounts (verified live)
| Brand | Customer ID |
|---|---|
| Esteban | `5229815058` |
| Belasil | `7566352958` |
| Grandia | `9069610821` |

List all child accounts anytime: `uv run gads.py accounts`.

## Campaign & asset-group map (Esteban + Belasil)

Run `uv run audit_campaigns.py` for a live view with `▶ ENABLED / ⏸ PAUSED / ✗ REMOVED` icons.

**Esteban (5229815058)**
| Status | Campaign ID | Name | Type | Active AGs |
|---|---|---|---|---|
| ▶ | `23924430848` | Performance Max | PMax | Bărbați (6720372855), Unisex (6720373641), Damă (6720398494) |
| ▶ | `23928558931` | Search - Brand | Search | Brand (1 ad) |
| ⏸ | `23918558286` | Campaign #1 | PMax | Asset Group 1 (6720307893) — PAUSED, nu servește |
| ⏸ | `23923794365` | Performance Max-2 | PMax | Performance Max-2 AG — PAUSED |
| ⏸ | `23924003975` | Search - By-original | Search | 20 AGs inspirate după parfum — PAUSED |
| ⏸ | `23924008511` | Search - Inspirate | Search | Generic/Pret/Cadou/Persistenta — PAUSED |
| ⏸ | `23924029121` | Search - Conquesting | Search | Concurenti — PAUSED |

**Belasil (7566352958)**
| Status | Campaign ID | Name | Type | Active AGs |
|---|---|---|---|---|
| ▶ | `22478321481` | All Products | PMax | [ALS] P.Max (6570957552) |
| ▶ | `22485577197` | Brand Protect | Search | Ad group 1 (1 ad) |
| ▶ | `23927269391` | Non-Brand - Detergent | Search | 3 AGs (cantitate/gel/ieftin) |
| ⏸ | `23312943064` | Allsoft P.Max Laveta | PMax | Asset Group 1 (6638306494) — PAUSED |
| ⏸ | `22928099453` | AllSoft Search Brand | Search | Ad group 1 — PAUSED |
| ✗ | `22478291976` | Campaign #1 | PMax | AG1 (6570921716) — REMOVED, nu mai există |

> **ATENȚIE:** `asset_group.status='ENABLED'` în GAQL returnează AGs și din campanii REMOVED/PAUSED.
> Filtrează mereu și după statusul campaniei înainte să faci mutații: `audit_campaigns.py` face asta automat.

## Credentials & prerequisites (don't print secrets)
`metrics` DB, table **`google_ads_connections`** (active row): `developerToken`,
`loginCustomerId` (MCC), `oauthClientId`, `oauthClientSecret`, `refreshToken`. Scope is full
`…/auth/adwords` (read+write). Helpers read these in-process and never print them.
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export DATABASE_URL_METRICS="$(uv run "$KB" secret-get DATABASE_URL_METRICS)"
```
All scripts run with `uv` (deps declared inline).

---

## 1. Reports (read-only)
```bash
uv run gads.py report --preset campaigns   --customer 7566352958 --range TODAY
uv run gads.py report --preset ad_groups   --customer 7566352958 --range LAST_7_DAYS
uv run gads.py report --preset keywords    --customer 7566352958 --range LAST_7_DAYS
uv run gads.py report --preset search_terms --customer 7566352958 --range LAST_30_DAYS   # → negative mining
uv run gads.py report --preset ads         --customer 7566352958                          # → approval status
uv run gads.py report --customer 7566352958 --query "SELECT campaign.name, campaign_budget.amount_micros, campaign.primary_status_reasons FROM campaign WHERE campaign.status='ENABLED'" --format json
```
`costMicros` is shown ÷1e6 (RON). Ranges: TODAY, YESTERDAY, LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, THIS_MONTH.
Useful signals: `campaign.primary_status_reasons` = `BUDGET_CONSTRAINED` (→ scale), bidding strategy, asset group listing groups.

## 2. Mutations — **dry-run by default, add `--apply` to execute**
Treat a write to a live ad account like a destructive DB write: dry-run, confirm with the user, then `--apply`.
```bash
uv run gads.py set-budget    --customer C --campaign ID --daily 200            # RON/day
uv run gads.py set-troas     --customer C --campaign ID --roas 4.7             # 470% (Max-conv-value campaigns)
uv run gads.py set-tcpa      --customer C --campaign ID --cpa 30               # switches to Max conversions + tCPA
uv run gads.py set-status    --customer C --campaign ID --status PAUSED|ENABLED
uv run gads.py add-negatives --customer C --campaign ID --terms "a,b,c" --match PHRASE
uv run gads.py add-keywords  --customer C --adgroup AGID --terms "a,b,c" --match PHRASE
```
> **CPA ⇄ ROAS:** target ROAS = AOV / target_CPA. (AOV = conv_value ÷ conversions.) e.g. AOV 142, CPA 30 → tROAS 4.7.

## 3. Create a Search campaign (atomic)
Pattern: one `googleAds:mutate` with **temporary resource names** (negative ids) creating budget →
campaign → geo/language criteria → negatives → ad groups → keywords → RSAs, all in one request.
Template: **`build_belasil_nonbrand.py`** (adapt CID, names, keywords, RSAs). Run dry-run, then `--apply`.

**Gotchas (cost real time):**
- Campaign create **requires** `containsEuPoliticalAdvertising: "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"`.
- Ad text: **no `~` or stray symbols** → `policyFindingError: SYMBOLS` (PROHIBITED). Keep headlines ≤30, descriptions ≤90.
- Geo Romania = `geoTargetConstants/2642`; language RO = `languageConstants/1038`, EN = `1000`.
- Search-only network: `targetGoogleSearch:true, targetSearchNetwork:false, targetContentNetwork:false`.
- Create **PAUSED** so a human reviews before enabling (or enable via `set-status`).

PMax is **not** built well via API — create it in the UI; use the API to attach assets (below) and optimize.

## 4. Video pipeline — upload to YouTube + attach to Performance Max
Google Ads does **not** host raw video. Video assets are **YouTube videos** referenced by id. Two ways onto YouTube:
upload in the Ads UI Asset library (Google auto-hosts), **or** upload via API (below) once the channel OAuth is set up.

### 4a. One-time GCP / YouTube setup (per channel)
1. **GCP project** → APIs & Services → **enable "YouTube Data API v3"**.
2. **OAuth consent screen**: set **User type = External** (Internal blocks accounts outside the org → "can only be used within its organization"); add the `…/auth/youtube.upload` scope; add the **channel's Google account as a Test user**.
3. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → download JSON (client_id + secret).
4. Store: `kb.py secret-set YOUTUBE_OAUTH_CLIENT_ID …` / `YOUTUBE_OAUTH_CLIENT_SECRET …`.
5. **Consent once** (browser, logged in as the channel account):
   ```bash
   YT_CLIENT_ID=$(uv run "$KB" secret-get YOUTUBE_OAUTH_CLIENT_ID) \
   YT_CLIENT_SECRET=$(uv run "$KB" secret-get YOUTUBE_OAUTH_CLIENT_SECRET) \
     python3 yt_oauth.py        # prints a consent URL, catches the loopback, writes the refresh token
   ```
   On "Google hasn't verified this app" → Advanced → proceed (it's your project, Testing mode).
   Then store the printed token: `kb.py secret-set YOUTUBE_<BRAND>_REFRESH_TOKEN "$(cat /tmp/yt_refresh_belasil.txt)"`.
   (Testing-mode refresh tokens for a sensitive scope expire in ~7 days — fine for a batch; re-consent if needed.)

### 4b. Upload (quota: ~6 videos/day — `videos.insert` = 1600 units, daily cap 10,000)
```bash
export YOUTUBE_OAUTH_CLIENT_ID=… YOUTUBE_OAUTH_CLIENT_SECRET=… YOUTUBE_<BRAND>_REFRESH_TOKEN=…
uv run yt_upload.py --check                              # verify auth
uv run yt_upload.py --dir "/path/Creative Belasil 2"     # batch (unlisted) → prints youtu.be/<id>
```
Don't bulk-upload everything — pick the **proven winners** (see §5). PMax wants a handful, not dozens.

### 4c. Attach the YouTube videos to a PMax asset group — template `fix_attach.py`
**Gotchas (all real, all cost time):**
- The asset group **must have a Final URL** or you get `assetGroupError: FINAL_URL_REQUIRED`. Set it first: `assetGroups:mutate` update `{finalUrls:["https://brand.ro/"]}`, `updateMask:"final_urls"`.
- Link field type is **`YOUTUBE_VIDEO`**, NOT `VIDEO` (→ `assetLinkError: UNSUPPORTED_FIELD_TYPE`).
- Do it in **two steps**: `assets:mutate` (create `youtubeVideoAsset{youtubeVideoId}`) → `assetGroupAssets:mutate` (link, fieldType `YOUTUBE_VIDEO`).
- Only send `partialFailure` when you actually want partial (bulk creates); omit it on single updates (some endpoints reject it).
- Verify: `SELECT asset.name FROM asset_group_asset WHERE asset_group.id=… AND asset_group_asset.field_type='YOUTUBE_VIDEO'`.
- **Images** (unlike video) CAN be uploaded raw via API (`imageAsset.data` = base64 bytes) and linked with field types `MARKETING_IMAGE` (1.91:1) / `SQUARE_MARKETING_IMAGE` (1:1) / `PORTRAIT_MARKETING_IMAGE` (4:5).

### 4d. Pick the winners from Meta — `meta_top_ads.py`
The Meta API (token in `metrics` DB, accounts by name `ILIKE '%brand%'`) ranks ads by ROAS/purchases so you upload
only the proven creatives. `meta_resolve.py` maps ad → creative video → source title (note: heavy creative fields can 500 — request `creative{video_id}` only, small page size).

## 4e. PMax asset groups — text, images, logos (Brand Guidelines)
A PMax asset group needs a full set before it serves beyond Shopping. Build with `assets:mutate`
(create) then `assetGroupAssets:mutate` / `campaignAssets:mutate` (link). Template: **`fix_brand_guidelines.py`** + **`unblock_assets.py`** + **`build_belasil_assets.py`**.

**Field types & where they go:**
- Asset group: `HEADLINE` (≤30, max **15**), `LONG_HEADLINE` (≤90, max **5**), `DESCRIPTION` (≤90, max **5**, one ≤60), `MARKETING_IMAGE` (1.91:1), `SQUARE_MARKETING_IMAGE` (1:1), `PORTRAIT_MARKETING_IMAGE` (4:5), `YOUTUBE_VIDEO`.
- **Campaign** (when Brand Guidelines is ON): `BUSINESS_NAME` (text ≤25), `LOGO` (**exact 1:1**), `LANDSCAPE_LOGO` (4:1). Via `campaignAssets:mutate`, NOT `assetGroupAssets:mutate`.

**Gotchas (each cost real time on Belasil + Esteban):**
- **Brand Guidelines enabled** → the campaign requires a `BUSINESS_NAME` + square `LOGO` linked as **CampaignAssets** before *any* asset-group asset will link (`REQUIRED_BUSINESS_NAME_ASSET_NOT_LINKED` / `REQUIRED_LOGO_ASSET_NOT_LINKED`). `brandGuidelinesEnabled` **cannot be turned off via API** (400). So satisfy it.
- **Brand Guidelines ON → LOGO/BN/LANDSCAPE_LOGO sunt BLOCATE la nivel asset group.** Dacă încerci `assetGroupAssets:mutate` cu aceste tipuri, primești `Brand Guidelines is enabled. Performance Max campaigns with Brand Guidelines enabled must link business name and logo assets as CampaignAssets.` Soluție: folosește `campaignAssets:mutate` (același pattern create/remove, dar cu `campaign` în loc de `assetGroup`). Template: `fix_esteban_logos_videos.py`.
- **Swap BN la nivel campanie** (atomic, partialFailure=False): `campaignAssets:mutate` cu `[{"remove": "customers/{cid}/campaignAssets/{camp_id}~{asset_id}~BUSINESS_NAME"}, {"create": {"campaign": camp_rn, "asset": new_bn_rn, "fieldType": "BUSINESS_NAME"}}]`. O singură cerere, fără intermediate state cu 0 BN-uri.
- **Logo must be EXACTLY 1:1** (a 2304×2400 screenshot fails `ASPECT_RATIO_NOT_ALLOWED`). Pad to square: `sips --padToHeightWidth 2400 2400 --padColor FFFFFF logo.png --out logo.png`.
- After logo+name are in, the asset group still needs **≥1 `MARKETING_IMAGE` + ≥1 `SQUARE_MARKETING_IMAGE`** (`NOT_ENOUGH_MARKETING_IMAGE_ASSET`) before text links.
- Images upload raw via API (`imageAsset.data` = base64). The Ads UI **"Generate images" (Gemini)** is UI-only (no API) — the fastest way to get on-brand images; have a human click it, then add text via API.
- Errors hide in `partialFailureError.details` (the call returns 200). Read it. Send `partialFailure` only on bulk creates; omit it on single updates (some endpoints reject the field).
- **Image cap = 20 per asset group** (all ratios combined). Over it → `resourceCountLimitExceededError: RESOURCE_LIMIT` (the whole add silently no-ops under partialFailure). To add more, **remove some first** (`{"remove":"<asset_group_asset resource_name>"}`) in a prior call, then create. Aim for a balanced mix (~8 landscape / 8 square / 3 portrait), not 14 of one ratio. Template: **`swap_belasil_images.py`**.
- Removed links still appear in reports — filter **`asset_group_asset.status='ENABLED'`** to count what's live.
- **`asset_group.status='ENABLED'` nu e suficient** — AGs dintr-o campanie REMOVED/PAUSED tot apar. Join pe `campaign.status` sau rulează `audit_campaigns.py` ca să știi ce servește cu adevărat.
- **API version**: folosim **v21** (v20 deprecat iunie 2026). `API="v21"` în `gads.py` și toate scripturile.
- **Python 3.14 argparse**: `%` în help strings trebuie escaped ca `%%` (altfel `ValueError: badly formed help string`).
- The UI **"Generate images" (Gemini)** is the fast way to satisfy the image minimum (UI-only, on-brand from the site); a human clicks it, you add text via API. Branded copy-on-image banners (Chrome-rendered, exact 1.91:1 / 1:1 / 4:5 — pad with `sips --padToHeightWidth`) complement Gemini's product shots: template **`add_belasil_banners.py`** + `belasil-creatives/banners.html`.
- Verify: `SELECT asset_group_asset.field_type FROM asset_group_asset WHERE asset_group.id=… AND asset_group_asset.status='ENABLED'` (count per type).

## 5. Optimization playbook (what to actually do)
- **Scale budget-constrained winners.** `primary_status_reasons = BUDGET_CONSTRAINED` + healthy ROAS → raise budget (start +50–100%), repeat every 2–3 days while CPA holds.
- **Cap CPA while scaling.** Put a **tROAS = AOV/targetCPA** on the main PMax (e.g. 470% ≈ CPA 30). Looser-than-current target lets it chase volume down to the CPA ceiling; tighter target protects efficiency.
- **Kill waste.** Pull `search_terms`; `add-negatives` for competitor/irrelevant 0-conversion terms (e.g. "dero", "profesional", "job").
- **Brand coverage.** "Missing relevant keywords" / low optimization score on a brand campaign → `add-keywords` with brand variations (brand, brand+product, brand+price, brand+pareri…).
- **Pause / fold underperformers.** A separate PMax below target (e.g. a low-AOV product at CPA ≫ target) → pause it and let the main "All Products" PMax cover those products (check listing groups: a single root filter = whole catalog).
- **Feed real video** (§4) — PMax with strong video beats asset-only; competitors in most niches run video-heavy.
- **Don't reset learning needlessly:** big budget jumps / new bid targets re-enter learning; move in steps, leave ~2 weeks.

## Guardrails / hard rules
- **Never print** the developer token, OAuth secret, or refresh tokens. Read from DB/secret store, use in-process.
- **Reports are read-only.** Mutations are **dry-run by default**; require `--apply` AND user confirmation before touching a live account.
- The MCC can touch *every* linked brand — always pass the correct `--customer`.
- Human-only: billing, advertiser identity verification, and the one-time YouTube OAuth **consent** (browser login).
