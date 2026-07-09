---
name: merchant-center-feed
description: Google Merchant Center feed health — which products are DISAPPROVED / not eligible (and why, per reason code) AND which are ELIGIBLE_LIMITED (eligible but reach-reduced — e.g. pending initial policy review on a new account, or missing GTIN) with their reasons. Disapproved/limited products = lost Shopping/PMax impressions and sales, critical for stores that lean on PMax (Grandia) and for new launches (Carpetto/Gento). Read-only, via the new Merchant API; --store accepts a raw merchant ID. Use for "feed health", "disapproved products", "why isn't this product showing in Shopping", "produsele sunt approved?", "pending review", "Merchant Center issues", "produse dezaprobate", "feed Google Shopping", "PMax feed".
argument-hint: "--store <grandia|esteban|belasil> | --all | --account-issues <merchant> | --set-business-info <merchant> [--name .. --cs-uri ..] [--apply]"
---

# merchant-center-feed — Google Shopping/PMax feed health
> Author: Gigi.

Surfaces the products Google has **disapproved or made ineligible** for Shopping/PMax, grouped by reason — the silent leak that throttles PMax (we run Google Ads in-house; Grandia leans on PMax).

> 🛠️ **Suspendare account-level MISREPRESENTATION** (tot feed-ul dezaprobat, `policy_enforcement_account_disapproval`) → cum o scoți ADITIV (identitate/pagini/business-info via Merchant API + scoate produsele flagate DOAR din feed, fără să atingi conversia pe magazine social-first) → playbook: **`reference/misrepresentation-fix.md`** (dovedit pe Ofertele + Bonhaus PL). Business-info se scrie prin Merchant API cu OAuth-ul acestui skill (scope `content`).

```bash
uv run merchant_feed.py --store grandia      # status counts + disapproved products + reasons
uv run merchant_feed.py --all                # all connected stores
```

## Account-level issues & business info (Misrepresentation toolkit)
- **`--account-issues <merchant>`** — print the ACCOUNT-LEVEL issues (title, severity, detail, impacted destinations, docs link) via Merchant API `accounts/v1 …/issues`. This is the **Misrepresentation / account-suspension detector**. Read-only. Ex: `uv run merchant_feed.py --account-issues 5813605780`.
- **`--set-business-info <merchant>`** — write `accountName` + `businessInfo.address` + `customerService` (email/uri) via Merchant API PATCH (updateMask). Sub-args: `--name --street --city --region --postal --country` (default RO) `--cs-email --cs-uri`. **Dry-run by default** (reads current, prints before→after); write only with `--apply`. ⚠️ Does NOT set `businessInfo.phone` (output-only) or `businessIdentity` (RO country-gated) — both skipped with a note. Ex: `uv run merchant_feed.py --set-business-info 5813605780 --name "ARONA SRL" --cs-uri https://ofertelezilei.ro/pages/contact` (add `--apply` to write). Uses this skill's OAuth (scope `content` = read+write).
- **`--set-return-policy <merchant>`** — CREATE an online return policy (fixes the „**missing return policy / return cost**" MC warning that limits/disapproves products). Via Merchant API `POST accounts/v1/…/onlineReturnPolicies`. Sub-args: `--country <CC>` + `--currency <CUR>` + `--uri <return-policy-page>` (obligatorii; uri = pagina reală `.../policies/refund-policy`), `--days N` (fereastră retur, default 14), `--return-fee X` (**costul de retur**; `0` = retur GRATIS, altfel client plătește fix X), `--label L`. **Dry-run by default**; scrie cu `--apply`. Apare în MC UI (Settings → Shipping and returns) în minute–ore (propagare). Ex: `uv run merchant_feed.py --set-return-policy 5815161322 --country CZ --currency CZK --uri https://bonhaus.cz/policies/refund-policy --apply`. ⚠️ `--return-fee` să MATCH-uiască pagina reală (dacă magazinul taxează retur, pune suma — altfel misrepresentation). Dovadă: Bonhaus CZ iul-2026 (feed 75 produse eligibile stătea NEfolosit; return policy lipsă → adăugat → 0 account-issues → lansat PMax).

## Connected stores & auth
Merchant Center accounts: **Grandia `5677157050`, Esteban `5676783307`, Belasil `5582663665`** (GCP project registered with each). Uses the **new Merchant API** (`merchantapi.googleapis.com/reports/v1`, `product_view`) with a **human OAuth token** in KB (`MERCHANT_OAUTH_REFRESH_TOKEN` + `YOUTUBE_OAUTH_CLIENT_ID/SECRET`) — the service account can't self-register the project, so a human (gheorghe@) registered it once. Add a store by registering its account + adding it to `ACCOUNTS`.
> **`--store` accepts a raw merchant ID** too (falls back to the arg if not in `ACCOUNTS`) — handy for newly-launched stores (e.g. Carpetto `5810819833`, Gento `5583322058`) before they're added to the map.

## ELIGIBLE vs ELIGIBLE_LIMITED vs disapproved
- **ELIGIBLE** = serving fully. **ELIGIBLE_LIMITED** = serving but reach-reduced — the skill now prints the *reasons* (🟡 line). **Not eligible** = disapproved (won't serve) → the per-product list.
- Common **ELIGIBLE_LIMITED** reasons:
  - `pending_initial_policy_review_shopping_ads` / `_free_listings` → **a NEW Merchant Center / new feed under Google's initial review**. **No action — clears on its own in a few hours to ~3 days**, then goes full ELIGIBLE. (Don't "fix" it; just wait. Seen on the Carpetto/Gento launch — all 15 Carpetto products were pending review, *not* a GTIN problem.)
  - `missing/invalid GTIN` (`item_id_inconsistent`, identifier issues) → for generic goods (carpets, handbags, no manufacturer barcode) set **`identifier_exists = no`** on the feed (declare "no GTIN") rather than inventing barcodes — Google then stops penalising the missing identifier.

## Real finding (Jun 2026)
Grandia: 842 products, **22 disapproved (3%)** — top reasons `guns_parts_policy_violation ×14` (Google mis-flags kitchen **baterii/faucets** as weapon parts!), `landing_page_error ×6`, `item_missing_required_attribute ×4`, `price_out_of_range ×3`. The 14 faucets are money lost on a wrong policy flag → **appeal in Merchant Center + adjust titles** (avoid "baterie" ambiguity → "robinet/baterie de bucătărie"). `landing_page_error` = broken/redirecting product URLs (fix in Shopify). Missing-attribute = add GTIN/brand/etc.

## How to use
Run weekly → push the disapproved list to **ClickUp** for the catalog team. Fixes: policy false-positives → appeal + retitle; landing_page_error → fix the product URL (pairs with `gigi:shopify-seo`); missing attributes → add via Shopify; price_out_of_range → check feed price vs landing price. Re-run after fixes to confirm re-approval.

## Lecții feed (iun 2026 — diagnoze CZ + Esteban)
- **`product_view` (reports/v1) LAGĂIE** ore–o zi. Pt numărul EFECTIV/curent de produse → `GET products/v1/accounts/{A}/products?pageSize=250` (paginat). Dacă userul a schimbat ceva în app și raportul nu reflectă → e lag, nu eșec; reverifică a doua zi.
- **`offerId shopify_ZZ_…` = MARKET nesetat în app-ul Google & YouTube** (UI Shopify, NU Admin API). DAR **ZZ ≠ dezaprobare** — majoritatea produselor ZZ rămân ELIGIBLE; ZZ devine problemă doar când **sub-sincronizează** catalogul. Simptom dovedit (Bonhaus CZ): 34 produse ACTIVE publicate pe canal, dar doar 5 în feed, toate ZZ → fix = app Google&YouTube → Settings → **Target market = țara** + re-sync (offerId devine `shopify_CZ_`).
- **Produse DRAFT/scoase din Online Store rămân FANTOME în feed** (status DISAPPROVED, `landing_page_error`, URL mort) până la un re-sync — chiar dacă în Shopify sunt deja scoase din canal (`publishedOnPublication(GooglePub)=false`). Verifică cu `gigi:shopify-stores` (`products(query:"status:draft"){…publishedOnPublication(...)}`); dacă produsul real e ACTIV + URL 200, `landing_page_error`-ul e tranzitoriu (se curăță la re-crawl). NU e nevoie de fix pe Shopify pt fantome — doar re-sync.
- **Cross-check sănătate feed:** nr produse în Merchant Center ar trebui ≈ nr produse ACTIVE publicate pe publication-ul „Google & YouTube" în Shopify. Dacă feed-ul e mult mai mic (CZ 5 vs 34) → market nesetat în app. `publishedOnPublication=true` ≠ produs în feed.
- ⚠️ **`ACCOUNTS` acoperă doar grandia/esteban/belasil/casaofertelor/bonhaus_ro/ofertele/bonhaus_cz** — lipsesc GT/Nubra/Carpetto/Gento (de adăugat când avem merchant ID-urile lor).

## Caveats
- `aggregatedReportingContextStatus` aggregates across destinations; a product `ELIGIBLE` for Shopping may still have demotions. The reason codes are the actionable signal.
- Old Content-API host (`shopping.content.googleapis.com`) is blocked in our sandbox — this uses the new `merchantapi.googleapis.com` only.
