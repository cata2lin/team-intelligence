---
name: shopify-knowledge-base
description: Bulk-populate the Shopify "Knowledge Base" app (Store FAQs that feed the AI shopping assistant / Storefront MCP) for any store — generate brand-accurate Q&A from real data (shop policies + delivery settings + Richpanel tickets + payment gateways), then write them automatically into the app via a logged-in Chrome (the app has NO write API). Use for "umple Knowledge Base", "FAQ AEO", "populează Store FAQs", "cum apare magazinul în AI", "answer engine optimization Shopify", "bulk add FAQ", on Esteban / GT / Nubra / Lab Noir and other team brands.
argument-hint: "--store <admin-handle> --file faqs.json [--skip-existing] [--dry-run]"
---

# shopify-knowledge-base — populate Store FAQs for AEO
> Author: **Gigi**.

The Shopify **Knowledge Base** app ([apps.shopify.com/shopify-knowledge-base](https://apps.shopify.com/shopify-knowledge-base)) holds the **Store FAQs** that Shopify's AI shopping assistant / **Storefront MCP** (`search_shop_policies_and_faqs`) uses to answer shopper questions. FAQs are **not** shown on the storefront — they're a trusted answer source for AI. This is the *answer-engine* half of AEO; the on-page half (FAQPage/Product schema, policies) lives in `gigi:shopify-seo` + `gigi:shopify-geo`.

## The hard fact (verified)
There is **NO public API** to write these FAQs: no `faqCreate` mutation, no FAQ metaobject (introspected the live Admin schema + a real store). The only write path is the embedded app UI ("Add FAQ"). So we **drive the UI** on a Chrome you're logged into. (Shop *policies*, which also feed the assistant, ARE writable via `shopPolicyUpdate` — do that in `gigi:shopify-seo`.)

## Two steps

### 1) Generate the Q&A from REAL data (don't invent answers)
Build `faqs.json` (`{ "faqs": [ { "q": "...", "a": "..." } ] }`) grounded in:
- **Shop policies** (Admin API, via `gigi:shopify-seo`'s `Store`): `shop{ shopPolicies{ type body } }` → return window, who pays return shipping, hygiene/sealed rule, contact.
- **Delivery settings**: `deliveryProfiles{…methodDefinitions{ rateProvider{…price} methodConditions }}` → real shipping cost, free-shipping threshold, which countries (international), express or not.
- **Payment gateways** (metrics DB): `SELECT lower(g) FROM orders o JOIN brands b ON o."brandId"=b.id, unnest(o."paymentGatewayNames") g WHERE b.name ILIKE '%<brand>%' GROUP BY 1` → COD vs card.
- **Real recurring questions** (metrics `richpanel_tickets`): top `category` for `resolved_store='<Brand>'` (e.g. `presale_intrebare`, `livrare_wismo`, `retur`, `modificare_comanda`, `plata_factura`) + sample `first_message` → phrase questions the way shoppers actually ask.
- **Shopify's own "Top unanswered questions"** shown on the app Overview (retail locations, bulk/wholesale, discounts, contact, express, international, free shipping).

Style: questions phrased like shoppers ask; answers 1-2 warm, complete sentences (Shopify's field hint = "brief answer in 1 or 2 sentences"); accurate per brand voice. **Get merchant sign-off before pushing — these become the AI's verbatim answers.** De-AI with `gigi:ai-scrub` if desired.

See `examples/esteban.faqs.json` (20 FAQs, battle-tested on esteban.ro 2026-06).

### 2) Push them automatically
```bash
# a) start a Chrome with remote debugging and log into the Shopify admin ONCE (Google SSO):
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir="$HOME/.kb-chrome" >/dev/null 2>&1 &
# (log in at admin.shopify.com in that window)

# b) push:
node scripts/kb_push.mjs --store 6f9e22-9d --file examples/esteban.faqs.json --skip-existing
#   --store = the ADMIN handle (subdomain of the *.myshopify.com domain), NOT the public domain.
#             find it: gigi:shopify-seo Store("<brand>").admin  ->  "<handle>.myshopify.com"
#   --skip-existing : page through the Custom tab and skip questions already present (safe re-runs)
#   --dry-run       : list what would be added, write nothing
```
The script connects to your logged-in Chrome (no credentials needed), opens the app, and adds each FAQ.

## Why a script and not the Admin API (and the gotchas it encodes)
All learned from a verified manual run; the script bakes them in so you don't re-learn them:
1. **Cross-origin iframe** — the app is served from `qa-pairs-app.shopify.prod.shopifyapps.com` inside the admin. Find that frame; act inside it.
2. **React controlled inputs** — setting `.value` does NOT enable Save. Must use **real key events** (`frame.type()`); the script types char-by-char. (Filling via DOM left the field empty on re-render.)
3. **Two Save buttons** — the App Bridge contextual "save bar" (outside the iframe) + the in-form one. Click the **in-form** Save (inside the iframe). DOM `.click()` is fine for the button (only *typing* needed real events).
4. **Transient "Application Error: Failed to fetch"** mid-save → FAQ NOT saved. Script detects it and retries (3×), reloading a fresh form each time.
5. **Success signal** = text "FAQ created". After save the app shows "Add another FAQ" (light SPA nav) or stays on the form with a toast — either way, reload `…/app/pairs/new` for a guaranteed-empty next form.
6. **macOS select-all is Cmd+A** (Ctrl+A is a no-op; a synthetic Cmd+A via CDP also doesn't select) — so NEVER reuse a dirty form; always start from an empty `/pairs/new`. The script does.
7. **Verify** on the **Custom** tab (`…/app?faqs_tab=custom`); 10 rows/page with Next pagination.

## Caveats
- Login is manual (Google SSO + 2FA) — do it once in the debugging Chrome; the script never handles credentials.
- The app auto-seeds some **Default** FAQs (Source "Shopify") from your store data; a few can be templated/wrong (e.g. contact "email@domain.com", odd return-policy text). Review/override them in the UI — they're separate from our Custom ones.
- Selector strategy is label-text/button-text based (Polaris ids are generated); if Shopify restyles the app, adjust `fieldSelectors()` / `clickSave()` in the script.
