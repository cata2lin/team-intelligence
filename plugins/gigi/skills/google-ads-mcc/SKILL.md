---
name: google-ads-mcc
description: Read and operate any Google Ads account linked under the team MCC (API v21) — live performance reports, budget/bidding/status/keyword/negative mutations, full Search campaign creation, and the end-to-end video pipeline (upload to YouTube + attach to Performance Max). Plus an optimization playbook and PMax asset-group asset management. Credentials (MCC developer token + OAuth refresh token) come from the `metrics` DB; no per-account login. Read-only by default; mutations are dry-run unless explicitly applied. Use for any live Google Ads work on a brand (Esteban, Belasil, Grandia, …) without screenshots.
---

# Google Ads via the team MCC

The team runs a **Google Ads Manager account (MCC)** whose API credentials live in the
**`metrics` Postgres DB**. One set of MCC credentials reads/writes **every account linked
under the MCC** — you only need the child account's **customer ID**.

> **MCC (login-customer-id): `7467110480`** (NOVOS DIGITAL SRL)

> 🚀 **Lansare Google Ads pe un magazin NOU** (cont + link MCC + conversie COD/native + Brand+PMax + cablare „CPA și financiar") → playbook end-to-end + gotcha-uri: **`reference/store-launch-playbook.md`** (dovedit pe Bonhaus PL / Gento / Carpetto / CZ).

## Connected accounts (verified live)
| Brand | Customer ID |
|---|---|
| Esteban | `5229815058` |
| Belasil | `7566352958` |
| Grandia | `9069610821` |

List all child accounts anytime: `uv run gads.py accounts`.

## ⚠️ ROAS-ul raportat de Google e UMFLAT — folosește `real_roas.py`
App-ul „Google & YouTube" de Shopify raportează un purchase pe **last-click** care **supra-creditează**
Google (1,4–1,8× observat pe toate brandurile). NU decide scalări pe cifra din Google Ads.
`uv run real_roas.py [--days 30] [--brand X]` = per brand **spend Google + ROAS pretins** vs
**venit GA4 din canalele Google** (Paid Search+Shopping+Cross-network) → **ROAS REAL** + factor de umflare.
Auto-descoperă property-urile GA4 accesibile SA-ului `looker-sheets`. Capcană: nici GA4 nu e 100%
incremental — campania **Brand** culege cerere creată de Meta/organic; incremental real = test geo/pauză brand.
Going-forward, măsură și mai precisă: comenzile Shopify cu `utm_source=google` (UTM pus pe toate conturile).

## ⚠️ Magazine cu COD FORM → purchase NU se trackuiește → `cod_tracking.py`
Magazinele COD (deals) folosesc un **formular custom** (Releasit „COD Form & Upsells", EasySell ș.a.) care
**ocolește checkout-ul nativ Shopify** — exact pagina pe care app-ul „Google & YouTube" trage pixelul de
purchase. Rezultat: **0 conversii purchase în Google Ads deși există comenzi reale** (simptom: Page View /
View Item se trackuiesc, Purchase = 0; comenzi reale în Shopify). Max Conversions rămâne ORB → nu optimizează.
**Diagnostic:** `SELECT segments.conversion_action_name, metrics.all_conversions FROM customer WHERE segments.date DURING LAST_30_DAYS` — dacă PURCHASE=0 dar PAGE_VIEW>0 și magazinul are comenzi → bug-ul ăsta.
**Fix:** `uv run cod_tracking.py --cid <CID> --ga4 <hint> --apply` — întâi **AUTO-DETECTEAZĂ** dacă magazinul
are COD form (scanează storefront-ul public, rezolvat din final_urls: recunoaște Releasit/EasySell; pe magazine cu
checkout NATIV refuză cu un avertisment, dacă insiști `--force`). Apoi creează o conversie WEBPAGE PURCHASE
„COD Purchase" (a noastră, nu cea app-managed), o face primary + PURCHASE goal biddable, și scoate cele 3
valori de pus în tab-ul **Conversion/Pixel tracking** al app-ului de COD form: **Google Ads Conversion ID
(`AW-…`) + Purchase Label + GA4 Measurement ID (`G-…`)**. Releasit/EasySell au câmp Google Ads built-in →
trag singure `gtag('event','conversion', send_to, value, currency, transaction_id)` pe thank-you-ul inline —
**fără cod în temă**. Conversiile apar în 24-48h. *Capcană atribuire:* Releasit prinde UTM nu gclid → atribuire
prin cookie auto-tagging, **same-session** (ok pt majoritatea); pt 100% etanș = captură gclid + Offline Conversion Import.
Carpetto (4069952156) reparat iun 2026: 14 comenzi reale / 0 conversii → COD Purchase `AW-18249884743/SoloCO7ckMUcEMfInP5D`.
- ⚠️ **Înainte să zici „PMax e orb pe COD", verifică dacă magazinul CHIAR are formular.** Multe magazine de parfum (ex. **Nubra, GT**) folosesc **checkout NATIV** (n-au Releasit) → „Google Shopping App Purchase" trage normal → conversiile Google mici = **cold-start / cerere reală mică, NU gaură de tracking**. Doar magazinele cu COD form (deals: Carpetto/Ofertele) au nevoie de „COD Purchase". `cod_tracking.py` auto-detectează și refuză pe checkout nativ — nu forța degeaba.

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
- **Trend zilnic la nivel de CONT** (recovery, scalare, anomalii): `--query "SELECT segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM customer WHERE segments.date DURING LAST_14_DAYS ORDER BY segments.date"`. (`LAST_7_DAYS` etc. EXCLUD ziua curentă — azi e parțial; nu te mira că lipsește.)
- **Capcană GAQL:** `metrics.search_budget_lost_impression_share` NU se poate selecta în același query cu `campaign_budget` pe toate tipurile (PMax) → query-uri SEPARATE per câmp.
- **Capcană shell:** NU face `gads.py … --format json | uv run -` cu un heredoc — `-` citește scriptul din stdin și ajunge să interpreteze JSON-ul ca cod (`name 'false' is not defined`). Salvează JSON-ul într-un fișier, apoi rulează formatter-ul pe fișier.

## 1b. Keyword research — Keyword Planner (`kw_ideas.py`) — și pentru SEO
Volume REALE de căutare lunară (RO) via `generateKeywordIdeas`. Read-only. **Util mai ales pt SEO** (gigi:shopify-seo): ce cuvinte au cerere → ce colecții/articole merită. Geo RO=2642, **limbă RO=1032 PESTE TOT** (și Keyword Planner ȘI targeting campanii — language constants sunt universale). 🔴 **NU 1038 = Catalană** (bug istoric care a lansat Gento/GT/Nubra/Carpetto/Ofertele pe catalană).
```bash
DATABASE_URL_METRICS=... uv run kw_ideas.py --customer 9069610821 --seeds "mobilier,canapea,lustra led"
uv run kw_ideas.py --customer 9069610821 --url https://grandia.ro/collections/mobilier --page   # idei din pagină
```
**Regulă SEO (vezi gigi:shopify-seo):** volumul = CEREREA, dar colecție creezi DOAR unde ai stoc real — termen cu volum mare fără marfă = pagină goală, nu task SEO.

## 2. Mutations — **dry-run by default, add `--apply` to execute**
Treat a write to a live ad account like a destructive DB write: dry-run, confirm with the user, then `--apply`.
```bash
uv run gads.py set-budget    --customer C --campaign ID --daily 200            # RON/day
uv run gads.py set-troas     --customer C --campaign ID --roas 4.7             # 470% (auto-detects Max-conv-value/PMax vs standalone TARGET_ROAS/Shopping)
uv run gads.py set-tcpa      --customer C --campaign ID --cpa 30               # switches to Max conversions + tCPA
uv run gads.py set-status    --customer C --campaign ID --status PAUSED|ENABLED
uv run gads.py add-negatives --customer C --campaign ID --terms "a,b,c" --match PHRASE
uv run gads.py add-keywords  --customer C --adgroup AGID --terms "a,b,c" --match PHRASE
uv run gads.py set-keyword-status --customer C --campaign ID --text "kw" --match BROAD --status PAUSED   # pauză/enable pe un keyword (sau --resource customers/X/adGroupCriteria/AG~CRIT). --text fără --match prinde toate variantele de match.
uv run gads.py add-shared-negative --customer C --shared-set SSID --text "grandia" --match PHRASE
uv run gads.py link-account  --client CID                                        # invită un client sub MCC (manager-link PENDING; el acceptă în Admin → Access & security → Managers)
uv run gads.py create-search --customer C --name "Brand" --budget 20 --geo 2616 --lang 1030 --keywords "a,b" --headlines "H1|H2|H3" --descriptions "D1|D2" --final-url https://x.ro   # Search PAUSED, atomic (buget+campanie+geo/lang+adgroup+kw+RSA)
uv run gads.py create-pmax   --customer C --name "PMax" --budget 30 --merchant MCID --geo 2642 --final-url https://x.ro   # Shopping-led PMax PAUSED, skeleton (fără assets — adaugă creative apoi enable)
```
> **CPA ⇄ ROAS:** target ROAS = AOV / target_CPA. (AOV = conv_value ÷ conversions.) e.g. AOV 142, CPA 30 → tROAS 4.7.
> **Negative pe SHARED list vs pe campanie:** `add-negatives` adaugă negativul pe UNA campanie (campaignCriteria).
> `add-shared-negative` adaugă negativul într-o **listă partajată** (`sharedSet`, endpoint `sharedCriteria:mutate`) →
> lovește TOATE campaniile care folosesc lista dintr-o mișcare (**inclusiv Shopping + PMax**, unde negativele
> pe campanie nu se pot pune direct). După `--apply` re-verifică singur (query pe `shared_criterion`) + printează resourceName.
> Găsești id-ul listei cu: `report --customer C --query "SELECT shared_set.id, shared_set.name, shared_set.type FROM shared_set"`.

## 3. Create a Search campaign (atomic)
Pattern: one `googleAds:mutate` with **temporary resource names** (negative ids) creating budget →
campaign → geo/language criteria → negatives → ad groups → keywords → RSAs, all in one request.
Template: **`build_belasil_nonbrand.py`** (adapt CID, names, keywords, RSAs). Run dry-run, then `--apply`.

**Gotchas (cost real time):**
- Campaign create **requires** `containsEuPoliticalAdvertising: "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"`.
- Ad text: **no `~` or stray symbols** → `policyFindingError: SYMBOLS` (PROHIBITED). Keep headlines ≤30, descriptions ≤90.
- Geo Romania = `geoTargetConstants/2642`; language RO = `languageConstants/1032` (🔴 **NU `1038` = Catalană!** bug istoric), EN = `1000`, PL = `1030`, CZ = `1021`.
- Search-only network: `targetGoogleSearch:true, targetSearchNetwork:false, targetContentNetwork:false`.
- Create **PAUSED** so a human reviews before enabling (or enable via `set-status`).

PMax is **not** built well via API — create it in the UI; use the API to attach assets (below) and optimize.

## 4. Video pipeline — upload to YouTube + attach to Performance Max
Google Ads does **not** host raw video. Video assets are **YouTube videos** referenced by id. Two ways onto YouTube:
upload in the Ads UI Asset library (Google auto-hosts), **or** upload via API (below) once the channel OAuth is set up.

### 4a. One-time GCP / YouTube setup (per channel)
1. **GCP project** → APIs & Services → **enable "YouTube Data API v3"**.
2. **OAuth consent screen**: set **User type = External** (Internal blocks accounts outside the org → "can only be used within its organization"); add the `…/auth/youtube.upload` **and** `…/auth/youtube.readonly` scopes (readonly = ca să poți confirma pe ce canal a aterizat consimțământul cu `channels.list mine=true`; upload-only dă 403 la listare); add the **channel's Google account as a Test user**.
3. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → download JSON (client_id + secret).
4. Store: `kb.py secret-set YOUTUBE_OAUTH_CLIENT_ID …` / `YOUTUBE_OAUTH_CLIENT_SECRET …`.
5. **Consent once** (browser, logged in as the channel account):
   ```bash
   YT_CLIENT_ID=$(uv run "$KB" secret-get YOUTUBE_OAUTH_CLIENT_ID) \
   YT_CLIENT_SECRET=$(uv run "$KB" secret-get YOUTUBE_OAUTH_CLIENT_SECRET) \
     python3 yt_oauth.py        # prints a consent URL, catches the loopback, writes the refresh token
   ```
   On "Google hasn't verified this app" → Advanced → proceed (it's your project, Testing mode).
   Then store the printed token: `kb.py secret-set YOUTUBE_<BRAND>_REFRESH_TOKEN "$(cat /tmp/yt_refresh_<brand>.txt)"` (`YT_OUT=/tmp/yt_refresh_<brand>.txt`).
   (Testing-mode refresh tokens for a sensitive scope expire in ~7 days — fine for a batch; re-consent if needed.)

   **Modelul „un canal per brand" (Brand Accounts):** de regulă TOATE canalele de brand sunt **Brand Accounts sub UN singur cont Google** (mailul operatorului). Un singur app OAuth (`YOUTUBE_OAUTH_CLIENT_ID/_SECRET`) acoperă toate. Fiecare canal cere **un consimțământ separat** unde, la ecranul **„Choose a channel" / „Continuă ca…"**, alegi canalul acelui brand → iese un refresh token dedicat (`YOUTUBE_GT_REFRESH_TOKEN`, `YOUTUBE_NUBRA_REFRESH_TOKEN`, …). Un token e legat de canalul ales la consimțământ; NU poți urca pe alt canal cu același token. Confirmă canalul după consimțământ: `channels.list(mine=true)` cu tokenul nou. Canale verificate (toate Brand Accounts sub mailul operatorului): **Belasil** `UCtlE0KfiNEY-osyWkxZ2h3g`, **GT by George Talent** `UCYOqiJSNLhqAmW_ERwRQMSw`, **Nubra** `UCYm-4E8NmoTqT157UkpaDwQ`, **Carpetto** `UC5J2u9x4SSqrwS1Q4gkK3oQ`, **Gento** `UCkBvkpi3O1h6Dy33XJDoAaA`, **Maison D'Esteban** (`@maisondesteban`) `UCQKJNKZfEa_7kRVBkdLhPMg`. Tokenuri în KB: `YOUTUBE_<BRAND>_REFRESH_TOKEN`.

### 4b. Upload (quota: ~6 videos/day — `videos.insert` = 1600 units, daily cap 10,000)
```bash
export YOUTUBE_OAUTH_CLIENT_ID=… YOUTUBE_OAUTH_CLIENT_SECRET=… YOUTUBE_<BRAND>_REFRESH_TOKEN=…
uv run yt_upload.py --brand GT --check                          # verify auth (alege brandul = canalul)
uv run yt_upload.py --brand GT --dir /tmp/gt_videos --url https://george-talent.ro   # batch (unlisted) → youtu.be/<id>
```
`--brand` alege `YOUTUBE_<BRAND>_REFRESH_TOKEN` + descrierea/URL-ul default (BELASIL/GT/NUBRA/ESTEBAN în map). Don't bulk-upload everything — pick the **proven winners** (see §5). PMax wants a handful, not dozens.

### 4d. De unde iei videourile (surse) — **shared drive „ARONA:NAS"**
Creative-urile reale stau pe **Google Shared Drive „ARONA:NAS"** (driveId `0AKkB0AV7_E-bUk9PVA`) → folder **`Projects/<Brand>/Ads`** (ex. `_George Talent/Ads`, `Nubra/Ads`; root-ul brandului are și hero-uri 16:9). Acolo ajungi și când NAS-ul local nu e montat (alt network). Citește/descarcă cu SA `looker-sheets` (DWD, scope `drive.readonly`, `corpora=drive`, `supportsAllDrives=True`, `includeItemsFromAllDrives=True`). Curatează: **≥11s** (sub 10s → `YOUTUBE_VIDEO_TOO_SHORT` la atașare), mix **vertical 9:16 + orizontal 16:9**, un pumn (5–9), nu zeci.

### 4c. Attach the YouTube videos to a PMax asset group — `attach_videos.py` (generic) / template `fix_attach.py`
Script gata: `uv run attach_videos.py --cid <CID> --ag customers/<CID>/assetGroups/<AGID> --videos vids.json --apply` (JSON = `[["videoId","nume"], …]`, sau `--video ID:Nume` repetabil; dry-run fără `--apply`).
**Gotchas (all real, all cost time):**
- The asset group **must have a Final URL** or you get `assetGroupError: FINAL_URL_REQUIRED`. Set it first: `assetGroups:mutate` update `{finalUrls:["https://brand.ro/"]}`, `updateMask:"final_urls"`.
- Link field type is **`YOUTUBE_VIDEO`**, NOT `VIDEO` (→ `assetLinkError: FIELD_TYPE_INCOMPATIBLE_WITH_ASSET_TYPE` / `UNSUPPORTED_FIELD_TYPE`).
- Do it in **two steps**: `assets:mutate` (create `youtubeVideoAsset{youtubeVideoId}`) → `assetGroupAssets:mutate` (link, fieldType `YOUTUBE_VIDEO`).
- **`CONCURRENT_MODIFICATION`** pe asset group nou (review în curs) la link în batch → leagă **unul câte unul cu retry/backoff** (attach_videos.py face deja asta).
- **`YOUTUBE_VIDEO_TOO_SHORT`** = videoul are <10s → exclude-l (curatează ≥11s la sursă, §4d). Asset-ul rămâne creat dar nelegat (orphan inofensiv).
- **Verifică pe ce canal e găzduit** un video atașat (audit „e pe canalul brandului?"): `youtube_video_id` din `asset_group_asset` → `videos.list(part=snippet)` cu tokenul readonly → `channelTitle`. Dacă e pe canal greșit (ex. „Video ad upload channel"), re-urcă pe canalul brandului + atașează noile + **scoate vechile** (`{"remove": assetGroupAsset.resource_name}`).
- **GAQL numără și REMOVED:** la verificat câte videouri are un asset group, filtrează `asset_group_asset.status='ENABLED'` (altfel apar și cele scoase → pari că ai dublu). `remove` pe unul deja scos → `OPERATION_NOT_PERMITTED_FOR_REMOVED_RESOURCE` (= era deja scos, nu eroare reală).
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
- **Premium cutout banners (preferred over copy-on-a-box):** use the **`gigi:ad-banners`** skill to background-remove a real product photo from the NAS (rembg) and place it on a dark "cutout + glow" layout, captured at native size (no sips padding). Upload any local PNGs with **`add_pmax_images.py`** (env `CIDARG` / `AGARG` / `DIRARG` / `IMGSARG=[["file.png","MARKETING_IMAGE"],…]`, dry-run → `--apply`). At the 20-image cap, do the remove-then-add swap in one atomic `assetGroupAssets:mutate` and look up the new image assets by `asset.name` to avoid duplicates.
- **Account-level Search extensions** (sitelinks + callouts + structured snippet) lift every RSA's ad strength + ad rank at once: **`add_search_extensions.py`** (per-account DATA, `CIDARG`, `customerAssets:mutate`). **Extend RSAs to 15 headlines**: **`add_rsa_headlines.py`**.
- **Image assets are content-addressed (deduped by bytes).** Creating an asset from a PNG that is byte-identical to an existing one returns the **existing** asset's resource name (no new asset). So a banner with identical copy + identical bottle across two asset groups resolves to **one shared asset** linked to both — harmless, but it means: to *replace* banners, look the current ones up by `asset.name` and `remove` their links explicitly (re-rendering with the same filename does NOT overwrite the asset). Give re-rendered versions a distinct on-image change (or just accept the share). Template for a full replace across groups: `esteban-creatives/_reswap_site.py`.
- Verify: `SELECT asset_group_asset.field_type FROM asset_group_asset WHERE asset_group.id=… AND asset_group_asset.status='ENABLED'` (count per type).

## 5. Optimization playbook (what to actually do)
- **Scale budget-constrained winners.** `primary_status_reasons = BUDGET_CONSTRAINED` + healthy ROAS → raise budget (start +50–100%), repeat every 2–3 days while CPA holds.
- **Cap CPA while scaling.** Put a **tROAS = AOV/targetCPA** on the main PMax (e.g. 470% ≈ CPA 30). Looser-than-current target lets it chase volume down to the CPA ceiling; tighter target protects efficiency.
- **Kill waste.** Pull `search_terms`; `add-negatives` for competitor/irrelevant 0-conversion terms (e.g. "dero", "profesional", "job").
- **Brand coverage.** "Missing relevant keywords" / low optimization score on a brand campaign → `add-keywords` with brand variations (brand, brand+product, brand+price, brand+pareri…).
- **Pause / fold underperformers.** A separate PMax below target (e.g. a low-AOV product at CPA ≫ target) → pause it and let the main "All Products" PMax cover those products (check listing groups: a single root filter = whole catalog).
- **Cold-start rescue (PMax nou care nu SERVEȘTE = 0 impresii).** Un PMax proaspăt pe **value-bidding** (`MAXIMIZE_CONVERSION_VALUE`/tROAS) cu 0 conversii istorice **se sufocă singur** → 0 impresii (dovedit iul-2026: Nocturna + Bonhaus PL). Fix: **`gads.py set-maxconv --customer X --campaign Y --apply`** = comută pe **Maximize Conversions PUR (fără target)** → cheltuie bugetul + adună date. (`create-pmax` **de acum default `--bidding maxconv`** exact ca să nu se mai repete.) NU pune tROAS/tCPA până ai 15-30 conversii.
- **Cold-start rescue (PMax nou care DRENEAZĂ, nu 0).** Alt simptom: un PMax care scoate multe clickuri dar ~0 conversii cumpără trafic prost (display/partners) fără semnal. Fix: **CAP buget agresiv**; dacă un **Search-Brand care convertește** e capped, MUTĂ bugetul acolo (el e câștigătorul). Real (iun 2026): GT PMax 180→50 (ROAS 0.9) + Search-Brand 40→100 (ROAS 10.5). `set-budget` / `set-maxconv` (dry-run by default).
- 🎯 **Target CPA per magazin (canonic, aprobat user) — Google MAI MIC decât Social.** Folosește target-urile din `brandref` (`target_cpa_google` / `target_cpa_social`; vezi și `shared/HARTA.md` → „Target CPA per magazin"). **REGULĂ: Google rulează la CPA mai mic decât Meta/TikTok** (~65%), fiindcă prinde brand + intenție mare (ieftin) și un tCPA mic disciplinează exact PMax/non-brand-ul scump (Brand Search e oricum sub prag → lasă-l să culeagă tot, nu-l plafona). Caveat: conversiile Google-s umflate (last-click + Shopping App) → tCPA-ul mușcă pe PMax/non-brand, nu pe brand. Setezi cu `set-tcpa` DOAR după 15-30 conv (sub asta = cold-start → doar cap buget). Ex.: parfumuri Google 15 / Social 25; deals Google 18-20 / Social 30.
- **Feed real video** (§4) — PMax with strong video beats asset-only; competitors in most niches run video-heavy.
- **Don't reset learning needlessly:** big budget jumps / new bid targets re-enter learning; move in steps, leave ~2 weeks.
- **Check budget UTILISATION before scaling.** Pull daily spend vs budget (last 7d) — only raise a budget if it's actually **capping** (spend ≥ ~90% of budget on the busy days). A campaign spending 30 of a 45 budget won't use more (it's bid/quality-limited, not budget-limited). Real wins this session: Esteban PMax capping at 700/day @ ROAS 37 (no tROAS) → raised to 1200; Belasil Brand Protect capping on demand days @ ROAS 9 → 45→65.
- **Two separate "learnings".** *Bidding* learning resets on budget/tROAS changes (Belasil PMax: a 10-Jun tROAS change → ROAS −65% for ~2 weeks → **HOLD, don't keep touching it**). *Ad-strength* recompute (PENDING) is triggered by asset/text edits and is separate from bidding. Use `change_history.py` to see which change caused a drop before reacting.
- **Ad Strength ≠ performance — don't chase EXCELLENT.** It's a Google guidance label; an AVERAGE asset group can print ROAS 37. Assets maxed on every lever (15/5/5 text + images + video + extensions, all diverse) still sit at AVERAGE because EXCELLENT is Google's opaque call — not always attainable. Rewriting copy on a *performing* campaign for the badge resets learning and can lower real ROAS. Fix genuinely repetitive/off-keyword copy (see **`gigi:ad-copy`**); leave the winners alone and optimise **profit**.

## 🚦 Rețetă COLD-START → SCALARE pe ETAPE (canonic — ce faci la fiecare stadiu al contului)
> Fundamentat pe Google Ads Help + consens practicieni (2024-26) + datele noastre reale. **Regula de aur: „learning-ul" se măsoară în CONVERSII, nu zile** (~50 conv / 3 cicluri ca să se calibreze). O campanie cu <15 conv/lună poate sta în learning SĂPTĂMÂNI. **Toate pragurile = fereastră 30 zile, la nivel de campanie.**

**Ce RESETEAZĂ learning** (pierzi semnalul, ~1-2 săpt volatil): (1) strategie de bidding nouă/schimbată, (2) ORICE schimbare de setare a strategiei, (3) compoziție (adaugi/scoți campanii/keywords), (4) **buget ±>20%** sau **target ±>20%**. → **Mișcă în pași de 10-15%, ~1 săptămână între ei. Grupează editările într-o fereastră, nu ciupi zilnic.**

| Etapă | Intri când | CE FACI | CE NU ATINGI | Treci mai departe când |
|---|---|---|---|---|
| **0 · Setup/Lansare** | nimic live | billing APPROVED · conversie PURCHASE-only trage · feed MC ELIGIBLE (PMax) · **bidding = Max Conversions FĂRĂ target** (`create-pmax --bidding maxconv` = deja default) · buget ≥ câteva CPA-uri țintă/zi | tROAS/tCPA · target strâns · over-segmentare | aprobată + **servește impresii în 48-72h** |
| **1 · Cold-start/Learning** | servește, <15 conv/30z | **LAS-O ÎN PACE** ~7-14 zile / până la ~15-30 conv · repari DOAR blocaje (tracking/feed/policy) · negatives dacă e clar aiurea | strategie · target · buget >20% (fiecare **resetează ceasul**) | **≥15 conv/30z (ideal 30)** + label learning dispărut |
| **2 · Stabilizare** | 15-30 conv/30z, CPA ~stabil | comută **Max Conv → tCPA** (`set-tcpa`, la sau **+10-20% peste CPA-ul mediu recent**, ancorat pe `brandref`) · taie risipa (`search-terms`→`add-negatives`) · brand coverage | tROAS încă NU · editări >20% · schimbări multiple deodată | CPA/ROAS la target + **profitabil ~2-4 săpt** + Lost IS Budget >0 |
| **3 · Scalare** | profitabil + loc | **levierele jos** (citește IS) | mișcări >20% · schimbi strategia la mijloc · scalezi când CPA e deja la plafon | IS ~60-80% + volumul se aplatizează |
| **4 · Matur/Apără** | saturat, stabil | ține buget/target · **apără brandul** (Brand Search prioritar, nelimitat) · igienă (negatives, refresh creative la fatigue, feed) · teste mici ±10-15% · extindere DOAR orizontală | să ciupești ce merge · IS ~100% · reacții mari pe zgomot zilnic | reintri în Et.3 doar la un levier NOU (piață/produs/sezon) |

**Levierele de scalare (Etapa 3) — citește Impression Share ca să alegi levierul CORECT:**
- **Lost IS (Budget) mare (>~50%) SAU cheltuie ≥90% din buget** → **buget-limitat** → `set-budget` **+10-20%/pas, la 3-7 zile**, cât timp CPA ține. *(ex. real: Nubra PMax pierdea 37% pe buget la CPA 7 → urcat 80→130.)*
- **Lost IS (Rank) mare + cheltuie SUB buget** → **NU buget** (n-are ce face cu el) → **relaxează targetul** (`set-tcpa` în sus / `set-troas` în jos) **10-15%/pas**, sau bid mai agresiv. *(ex. GT Non-Brand: IS 10%, 87% lost rank → tCPA 15→22; Grandia Shopping-New tROAS 4 > ROAS real 3,2 = sufoca → 3,2.)*
- **Orizontal ÎNAINTE de vertical** când e saturat (IS ~100% + volum plat): keywords non-brand noi, produse/asset groups noi, geo-uri, campanie **New-Customer** separată. Buget în plus pe o campanie saturată împinge spend în query-uri slabe → **CPA urcă**.
- **Duplici un winner** doar pt teritoriu NOU (geo/segment): copia **reintră în learning de la zero** + pot canibaliza aceeași licitație. Preferă **buget-raise când e buget-limitat; duplicare doar când e saturat/teritoriu nou**.

**Praguri de conversii ca să STRÂNGI bidding-ul (30 zile, per campanie):**
- **Max Conversions fără target** = start (merge de la ~15-20 conv/lună). **tCPA**: 15 minim / **30 recomandat** (`set-tcpa`). **tROAS Search**: 15 minim / **50+ de încredere** (`set-troas`).
- 🔴 **tROAS PMax: 30-50 ca să pornești, dar de încredere abia la 150+/lună** (studiu smec 14k campanii: <30 = ratează, 60-90 = 50/50, 150+ = ținește). **NU strânge PMax pe tROAS sub ~150 conv/lună** — ține-l pe Max Conversions. (Minimul Google de 15 e înșelător de mic.)

**Checklist „NU CHELTUIE / 0 impresii" (ordinea probabilității):**
1. **Billing** (plată respinsă / cont suspendat) — verifică ÎNTÂI. 2. **Ceva pauzat / dată-program / device −100%**. 3. **Buget < CPC**. 4. **Cold-start bidding / conversie care nu trage** — value-bidding/tROAS pe cont fără date **servește 0** → `set-maxconv`; confirmă că PURCHASE chiar se înregistrează. 5. **Target prea strâns** → relaxează. 6. **Feed/Merchant Center** (dezaprobate / ELIGIBLE_LIMITED / cont nou în review) → `gigi:merchant-center-feed`. 7. **Asset/policy în review** („Eligible" ≠ servește; imagini/video ~ până la 5 zile). 8. **Targeting prea îngust / cerere mică**.
> ⏱️ **0 impresii după 48-72h = ceva e stricat → investighează, NU aștepta.** *(Dovezi la noi, iul-2026: Nocturna + Bonhaus PL PMax = 0 impresii pe value-bidding cu 0 conversii → reparate cu `set-maxconv`.)*

**⚠️ Nuanțe:** „20% resetează learning" = consens practicieni (Google zice doar „orice schimbare de setare intră în learning") — plafon prudent, nu regulă oficială. Din **iun 2026 UI-ul a redenumit**: „Max conv + tCPA" → **„Target CPA"**, „Max conv value + tROAS" → **„Target ROAS"**.

## 📚 Principii avansate + schimbări de platformă 2026 (research aprofundat, iul-2026)
> Coroborat din 2 rapoarte deep-research (Gemini + ChatGPT) + research-ul nostru. Extinde rețeta pe etape de mai sus.

**Arhitectura conversiilor (ÎNAINTE de bidding — poluarea semnalului = cauza #1 de learning eșuat):**
- **Max 1-3 conversii PRIMARE/cont** (biddable), doar macro-venit (achiziție / lead validat). Micro-acțiunile (add-to-cart, begin-checkout, view-item, newsletter) = **SECUNDARE** (observare, ignorate de bidding). `fix_conversion_goals.py` face exact asta (PURCHASE-only biddable).
- ⚠️ **Custom Goal gotcha**: bagi o acțiune secundară într-un Custom Goal → ea **REINTRĂ** în semnalul primar de bidding pt campaniile cu acel goal. Auditează goal-urile înainte de lansare.
- **NU folosi evenimente GA4-importate ca primar** (întârzieri + atribuire diferită → subraportare); tag native Google Ads via GTM. Enhanced Conversions Web nu merge pe conversii doar-din-GA4.

**📏 Regula 10× buget la cold-start:** buget zilnic ≥ **10× tCPA-ul țintă**, ca algoritmul să aibă spațiu de licitat. Buget meschin raportat la CPA = campanie stagnantă. (țintă CPA 20 → buget ≥ ~200/zi; dacă n-ai, țintește un produs/CPA mai ieftin.)

**🔴 SCHIMBARE PLATFORMĂ „Bidding Target Optimization" (anunțat 15-iun-2026, AUTO de la 17-AUG-2026) — ne afectează direct:**
- Până acum o campanie **„Limited by budget" + tCPA/tROAS SUPRA-performa** (ex. tCPA 50 livra la 30, licitând în jos ca să stoarcă volum din buget mic) — **exact trucul „tROAS ca gardă pe winneri budget-limited"** (vezi Optimization playbook + Grandia).
- **După 17-aug** Google forțează optimizarea spre targetul EFECTIV → campania de la 30 urcă spre 50. **Deci trucul „target ca frână pe budget-limited" MOARE**; scalarea devine predictibilă (mărești buget pe target corect → cost stabil).
- **DE FĂCUT înainte de aug** pe campaniile budget-limited cu target: „Bid Target Adjustment Tool" (iul-2026) → **Opțiunea A: aliniază targetul la performanța reală recentă** (ex. tCPA 50→30) ca să nu-ți urce costul peste noapte. (Sau: C=accepți creșterea, D=treci pe Max Conversions, E=mărești bugetul.) ⚠️ Verifică datele exacte în cont — sunt anunțuri viitoare.

**Stair-stepping la STRÂNGEREA targetului:** NU seta targetul direct la nivelul dorit dacă e sub performanța istorică (→ sufocă, se retrage din licitații). Setează **+5-10% peste media recentă**, apoi coboară în trepte fine ~1 săpt/pas.

**Structura Hagakure (Search):** consolidează — **1 URL / ad group**, broad match + Smart Bidding + RSA. Ține SEPARAT (campanie/ad group propriu) doar paginile cu **≥3.000 impresii/săpt (~12.000/lună)**; long-tail-ul → **DSA** (nu diluează bugetul). NU SKAG-uri (fragmentează datele → learning etern). Asset groups PMax = pe **temă produs / marjă**, NU pe audiență.

**Ads Power Pairing (scalare orizontală):** Broad Match Search (Smart Bidding) + PMax rulate ÎMPREUNĂ — broad prinde cereri noi atipice, PMax prinde utilizatorii pe rețelele adiacente (YT/Gmail/Discover). + Customer Match (High-LTV) → lookalike / optimized targeting. Evită suprapunerea audiențelor (auto-canibalizare).

**Hybrid PMax + Standard Shopping (e-commerce — relevant GRANDIA):** PMax favorizează bestsellerii și neglijează long-tail-ul („zombie products"). Prioritatea obligatorie PMax > Standard Shopping **a fost ELIMINATĂ (2025/26) → decide Ad Rank** → poți rula hibrid ca corecție chirurgicală:
  - **Zombie Resurrection** — scoți SKU-urile invizibile din PMax → Shopping „Maximize Clicks" (cumperi densitate de date) → le readuci în PMax.
  - **Brand Guardian** — excluzi brandul din PMax (nu mai umflă ROAS fals) → Shopping cu CPC controlat.
  - **Margin Defender** — produse cu marjă mică scoase din PMax (AI caută ROAS brut, nu profit).
  - **Clearance** — lichidare stoc → Shopping High-Priority + Manual CPC agresiv.
  - Tranziție graduală (~20% stoc/pas + urci tROAS PMax ca să cedeze marginea).

**POAS > ROAS (maturitate):** PMax vânează ROAS BRUT (repetă produse ieftine cu marjă mică). Migrează spre PROFIT: `custom_labels` de marjă în feed + Offline Conversion Import cu valori de profit + **New Customer Acquisition Value Mode** (bid mai mare pe clienți NOI vs listele de remarketing).

**Conversion lag = nu judeca ferestre scurte:** costul se declară instant, conversiile vin la zile-săptămâni (COD/Shopping = și mai mult). Evaluează pe **30 zile** + coloana **„Conversion Value (By Time)"** (atribuie la momentul conversiei, nu al clickului). Evaluarea prematură = cauza #1 de oprire abuzivă a unui Smart Bidding sănătos (deja lecția noastră Grandia COD-lag).

**Alte schimbări 2026:** call-only ads scoase din creare (feb-2026), opresc afișările (feb-2027) → RSA + call assets. Enhanced Conversions Web+Leads unificate (iun-2026) + migrare spre Data Manager API. Advanced Consent Mode > Basic (ping-uri anonime modelate recuperează conversii — prag ~1000 ev/zi).

## 6. Change history — track an agency / who changed what
For accounts an **external agency** runs (e.g. Grandia = SkilledPPC, `matei@skilledppc.com`)
or to audit any change, use **`change_history.py`** — reads the `change_event` resource and
prints *when · who (+ client WEB/API/BULK) · operation · resource · changed fields old→new*,
with campaign names resolved and `*Micros` shown in RON.
```bash
uv run change_history.py --customer 9069610821                 # last 14 days, detailed
uv run change_history.py --customer 9069610821 --days 30        # max Google retains
uv run change_history.py --customer 9069610821 --summary        # who/what/operation counts
uv run change_history.py --customer 9069610821 --by matei@skilledppc.com
```
- `client_type`: **`GOOGLE_ADS_API`** = the MCC/our scripts; **`GOOGLE_ADS_WEB_CLIENT` / `BULK` / `EDITOR`** = a human in the UI. Quick way to prove "did *we* touch this account?" — filter API vs not.
- **Google only keeps 30 days** of change history. To track longer, run this on a cron and snapshot to a table/file (e.g. the metrics DB or NAS) — otherwise older agency moves are lost.
- Reads only; safe on any account, including ones we don't manage.

## Launch playbook — cont nou (testat: Gento, Carpetto, GT, Nubra)
Pașii repetabili pentru a lansa un brand pe Google Ads de la zero. Template scripturi: `gt-creatives/` și `nubra-creatives/` (`build_all.py` = PMax+Brand+Non-Brand atomic, citește `spec.json` + `out/`).

1. **Link MCC** — `customerClientLinks:mutate` (**operation SINGULAR**, `status:"PENDING"`) de la MCC-ul nostru `746-711-0480` → contul client. Proprietarul **acceptă** în Admin→Access&security→Managers. Verifică ACTIV cu `customer_client_link`. (Script: `esteban-creatives/_link_mcc.py`.)
2. **Readiness** — verifică: campanii existente, `conversion_action` (există PURCHASE?), `billing_setup.status=APPROVED`, `product_link.merchant_center` (Merchant Center linkat = obligatoriu pt PMax Shopping).
3. **Economia** — `gigi:fulfillment-analytics/breakeven.py --store X` (AOV/COGS/**breakeven CPA+ROAS**). Top produse: AWBprint `line_items` (SKU-uri hero) → le împingi în asset group.
4. **Conversion goals** — `fix_conversion_goals.py --customer X --apply` → **doar PURCHASE biddable** (PAGE_VIEW/ADD_TO_CART/BEGIN_CHECKOUT biddable = bug care umflă conversiile, optimizează aiurea).
   - **De-dup PURCHASE**: dacă există 2+ acțiuni PURCHASE `primary_for_goal=true` (ex. „Google Shopping App Purchase" + generic „Purchase"), lasă UNA primary. Pe **magazine COD** (form Releasit) → „COD Purchase" (a noastră, via `cod_tracking.py`) e cea care numără; pune „Google Shopping App Purchase" `primary_for_goal=false`.
   - **Capcană `Calls from ads`**: poate fi `primary_for_goal=true` (moștenit) → campaniile optimizează pe APELURI, nu vânzări. Scoate-l din primary pe magazine ecommerce (descoperit la ROSSI).
   - **REST gotcha**: `conversionActions:mutate` → `updateMask` merge **în interiorul** operației (`{"operations":[{"update":{...},"updateMask":"primary_for_goal"}]}`), NU top-level (altfel „Unknown name updateMask").
5. **Feed** — `gigi:merchant-center-feed --store <merchantId>` (ai nevoie de produse ELIGIBLE pt Shopping).
   - 🟢 **ÎNTREABĂ MEREU userul la lansare: în Shopify → Sales channels → „Google & YouTube" → Settings e bifat să folosească SEO title + SEO description?** Feed-ul trebuie să tragă **titlul SEO (page title) + descrierea SEO (meta description)**, NU titlul/descrierea brută a produsului. Titlurile SEO sunt optimizate pe cuvinte-cheie cu cerere reală (`kw_ideas.py`) → relevanță + CTR mult mai bune în Shopping/PMax; titlul brut (ex. „L'Essence No. 124") nu conține termenul căutat. **Pune întrebarea explicit ÎNAINTE de build** și, dacă nu e bifat, cere userului să-l activeze (e o setare din UI-ul Shopify, nu din API). Capcană înrudită: `landing_page_error` în feed (ex. Esteban 13% produse respinse) — verifică-l tot la pasul ăsta.
   - 🟢 **Publică TOATE produsele ACTIVE în canalul „Google & YouTube" + confirmă MARKET-ul în app.** Verifică câte produse ACTIVE sunt publicate pe publication-ul Google: `shopify_gql.py --prefix <X> --query 'query{products(first:100){edges{node{status publishedOnPublication(publicationId:"<GoogleYouTubePublicationId>")}}}}'`. Publică ce lipsește cu `publishablePublish` (vezi `reference/mutations.md` → publications). **Capcană dovedită (Bonhaus CZ):** 34/34 produse ACTIVE erau publicate pe canal, dar în Merchant Center ajungeau doar 5 — fiindcă **market-ul țintă NU era setat în app-ul Google & YouTube** → offerId `shopify_ZZ_` (ZZ = market unset) → app-ul nu împinge catalogul. Fix = în app-ul Google & YouTube (UI Shopify) setează **Target market/country** = țara magazinului (ex. Czech Republic) + re-sync. `publishedOnPublication=true` ≠ produs în feed; verifică ÎNTOTDEAUNA și cu `gigi:merchant-center-feed` că numărul din Merchant Center ≈ nr produse active.
6. **Copy** — generat (workflow cu verificare adversarială, sau direct) + **self-verificat**: ⚠️ **ZERO mărci înregistrate** (nume de parfum/brand premium → dezaprobare!), limite char (HL≤30, desc≤90, long≤90, business name≤25, **min 1 desc ≤60**), diacritice RO corecte, **fără superlative** neverificabile ("îmbatabil/de top" → "excelent").
7. **Imagini** — poze hero → crop la **1:1 / 1.91:1 / 4:5** + logo. Sursă: Shopify (`shopify_gql`) SAU **warehouse `products.featuredImageUrl`** (token-free — folosește când tokenul magazinului e mort/rotativ, ex. **Nubra**).
8. **Build** (`build_all.py`): PMax skelet atomic (budget + campanie `maximizeConversions{targetCpaMicros}` + `shoppingSetting.merchantId` + geo RO `2642` + **lang RO `1032`** + asset group + listing group `UNIT_INCLUDED/SHOPPING`) → assets (text + imagini; **BN+LOGO la nivel CAMPANIE** fiindcă Brand Guidelines ON, restul la asset group) → Brand Search → Non-Brand Search → enable PMax.
   - 🔴 **LIMBĂ: RO = `languageConstants/1032`. NU `1038` = CATALANĂ!** Bug istoric în acest playbook: Gento/GT/Nubra/Carpetto au fost lansate pe `1038` (catalană) → Search sufocat (reach ≈0 în RO; PMax tolerează). Reparate iun 2026 (adăugat 1032). Verifică mereu: `SELECT language_constant.name FROM language_constant WHERE language_constant.id=<X>`. (CZ = `1021`, EN = `1000`.)
   - 🔴 **v21: câmp OBLIGATORIU pe `campaign.create`** → `containsEuPoliticalAdvertising: "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"` (altfel `REQUIRED` error).
   - **Fără feed / stoc parțial → Search, NU PMax.** Dacă Merchant Center feed-ul nu e gata (sau catalogul are stoc parțial — ex. ROSSI: doar 1 kit pe stoc), lansează **Brand Search + Non-Brand Search cu finalUrl pe PAGINA produsului in-stock** (control total, zero risc de a promova epuizate), ori **DSA** (crawl domeniu) pt catalog random fără feed (ex. Bonhaus CZ). PMax îl adaugi când feed-ul/stocul e ok. Template: `cz-creatives/build_search.py`, `rossi-creatives/build_search.py`.
9. **Bidding cont NOU = cold-start**: PMax pe **Maximize Conversions PUR (fără target)** — `create-pmax` pune asta by default (`--bidding maxconv`); value-bidding cold-startează la 0 impresii (lecția Carpetto/Nocturna/Bonhaus PL). Un PMax deja creat pe value-bidding → repară cu `set-maxconv`. **NU pune tCPA/tROAS până ai 15-30 conversii** (nici tCPA strâns — sufocă servirea la cont fără istoric). După prag → `set-tcpa` ≈ target, apoi `set-troas` ~2,5. Rețeta completă pe etape mai jos.
10. **UTM** — `customer.final_url_suffix = utm_source=google&utm_medium=cpc&utm_campaign={campaignid}&utm_content={creative}&utm_term={keyword}` (gclid acoperă GA4 nativ; UTM acoperă warehouse/Shopify). Setat pe toate conturile.
11. **Gotchas**: PMax auto-creează un „Asset Group 1" gol + uneori „Performance Max-1" (inofensive, lasă-le paused); asset group nou = **PENDING review ~ore** apoi servește.
12. **Ora ideală** = dimineața (~07:30 RO) zi lucrătoare. Dar **review-ul PMax întârzie servirea spre dimineață oricum**, deci lansarea de seara nu strică PMax-ul; Search-urile pornesc imediat. Scheduling = vezi memoria [[gads-skill-suite]] (rutine cloud, capcană: NU văd tooling-ul local).

## Real ROAS — adevărul pe canal (vs cifra umflată Google)
- **`real_roas.py`** — per brand: spend Google + ROAS pretins vs venit GA4 din canalele Google
  (Paid Search+Shopping+Cross-network) ÷ spend = **ROAS REAL** + factor umflare (~1,4-1,8×).
- **`real_roas_unified.py`** — extinde la **TOATE canalele**: per brand × {Google, Meta, TikTok},
  spend din `cache.daily_ad_spend_ron` (RON) vs venit GA4 atribuit canalului (Google pe channel-group,
  Meta/TikTok pe `sessionSource`) → ROAS real per canal vs **breakeven** (🔴 sub-breakeven). Răspunde
  „pe ce canal pierdem, pe ce brand?". `uv run real_roas_unified.py --days 30`.
  - ⚠️ GA4 last-click **sub-evaluează social** (Meta/TikTok = creare cerere, view-through) → ROAS-ul
    social e un PLANȘEU. Magazinele **COD** (Ofertele/Casa Ofertelor) au purchase GA4 subraportat
    (form-ul ocolește evenimentul) → venit subevaluat acolo. Adevărul cel mai bun: comenzi Shopify cu
    `utm_source`. Breakeven per brand = aprox în `BRANDS` (calibrează din `breakeven.py`).

## Guardrails / hard rules
- **Never print** the developer token, OAuth secret, or refresh tokens. Read from DB/secret store, use in-process.
- **Reports are read-only.** Mutations are **dry-run by default**; require `--apply` AND user confirmation before touching a live account.
- The MCC can touch *every* linked brand — always pass the correct `--customer`.
- Human-only: billing, advertiser identity verification, and the one-time YouTube OAuth **consent** (browser login).

## Unghiuri noi (skilluri adoptate MIT — folosește-le împreună cu acest skill)
- **gigi:ads-server-side-tracking** — sGTM + Meta CAPI + Enhanced Conversions: recuperează 30-40% din conversiile pierdute (iOS/ITP/adblock) + **datele de pe form-urile COD care nu trag pixelul** (fix-ul real pt flag-ul CODGAP din gads-audit). Nivelul următor după gclid/UTM.
- **gigi:ads-attribution** — sănătatea modelului de atribuire cross-platform (AdAttributionKit iOS view-through, deduplicare).
- **gigi:ads-creative** + **gigi:ad-copy** + **gigi:ad-banners** — framework-uri de creative/RSA. **gigi:ads-budget** + **gigi:budget-simulator** — alocare + forecast.
- **gigi:gads-audit** — sweep multi-cont (limbă/conversii/COD/capped/drainere). **real_roas_unified.py** (aici) — ROAS real per canal vs breakeven.


## Portofoliu + populare PMax asset-group prin API (adăugate iul-2026)
- **`gads_portfolio.py`** — performanță pe TOATE conturile MCC dintr-o rulare (spend/conv/CPA/ROAS 30z + trend 7z) vs target CPA per magazin. Scoate rapid winners-ii SUB-SCALAȚI (CPA < target + budget-lost mare → urcă buget cu `set-budget`) și conturile peste target. `uv run gads_portfolio.py`.
- **`pmax_assets.py`** — populează asset group-ul unui PMax creat cu `create-pmax` (care lasă SKELETON fără assets) + îl ENABLE. ⚠️ **GOTCHA Brand Guidelines**: `create-pmax` creează campania cu `brand_guidelines_enabled=TRUE` care e **IMMUTABLE** → business name + logo TREBUIE legate ca **`campaignAssets`** (nivel campanie), nu `assetGroupAssets`, altfel `BRAND_ASSETS_NOT_LINKED_AT_CAMPAIGN_LEVEL`. Restul (headlines/long/desc/marketing+square img) = `assetGroupAssets`. Asset-urile se creează în **2 pași** (create toate → link), NU atomic (validarea dă false `NOT_ENOUGH_*`). Imagini = bytes inline base64. API v21. Ref: bonhaus-pl-google-launch.
