---
name: shopify-app-launch
description: How to actually get a Shopify app approved on the App Store. Covers all 113 Partner Program requirements (sections 1-5), what applies to which app category, what causes rejection in practice, listing-asset rules (Screenshots vs Feature media, no pricing in images, etc.), test-credentials best practices, demo screencast specs, privacy policy + GDPR mandatory webhooks, and protected-customer-data Level 0/1/2 classification. Use whenever preparing for review, writing listing copy, debugging a rejection, building a feature that must comply, or auditing whether an existing app would pass.
---

# Shopify App Store Launch & Review

Everything I've learned shipping a Shopify app from build to submission. Distilled from the official requirements (crawled 2026-05-25) **plus** real rejection patterns that aren't in the docs.

## When to load this skill

- About to submit or resubmit an app for App Store review
- Writing or auditing listing copy / screenshots / app card subtitle
- Debugging an App Store rejection
- Adding a feature that touches: scopes, billing, webhooks, customer data, embedded experience, App Bridge, OAuth
- Onboarding a teammate to "what does Shopify actually require"
- Asked "is X compliant?" or "will Shopify reject Y?"

## File map

| Topic | File | When to consult |
|---|---|---|
| Sections 1-2 (Policy + Functionality) — 31 items | [`req-policy-and-functionality.md`](req-policy-and-functionality.md) | Auditing platform behavior: session tokens, API version, App Bridge, OAuth, embedded experience, billing API, REST vs GraphQL |
| Sections 3-4 (Security + Listing) — 27 items | [`req-security-and-listing.md`](req-security-and-listing.md) | TLS, scope justification, listing assets, pricing display rules, screenshots vs feature media, app card subtitle |
| Section 5 (category-specific) decision matrix | [`req-categories.md`](req-categories.md) | Determining if your app falls in a special category (Sales Channel, Payment, Donation, Blockchain, Post-purchase, Purchase option, Online store theme app, Product sourcing, etc.) |
| Privacy + GDPR + protected customer data | [`privacy-and-gdpr.md`](privacy-and-gdpr.md) | Mandatory webhooks, Level 0/1/2 classification, what to declare in Partner Dashboard, what to put in `/privacy` |
| Common rejection reasons + pre-submission checklist | [`rejection-prevention.md`](rejection-prevention.md) | Always re-read before clicking "Submit for review" |

## The one-page top-of-mind list

These are the things that **actually get apps rejected** in practice, in roughly decreasing order of frequency:

1. **REST API usage anywhere in the codebase** — banned for new apps as of April 2025 (requirement 2.2.4). `grep -rn "admin.rest\|/admin/api/.*\.json"` should return zero hits. Use the GraphQL Admin API only.
2. **Pricing info in images or app icon** (requirement 4.2.2) — including sample dollar figures in screenshots. A "$14,328 revenue recovered" mock screenshot is treated as a data claim and gets rejected. Use `$X,XXX` or remove the figure.
3. **Marketing frames uploaded as Screenshots instead of Feature media** (4.4.4) — Screenshots must "primarily show your app's actual user interface" without browser chrome. Stylized hero/problem/solution frames are Feature media, not Screenshots.
4. **Test credentials don't give access to paid features** (4.5.5) — reviewers won't pay to test Enterprise tiers. Either offer a comp/redeem flow you document in test instructions, or set the test merchant on the highest plan.
5. **Demo screencast too long or off-spec** (4.5.3) — keep the App Store listing video to 60-90s. Longer walkthroughs go elsewhere (YouTube linked in description).
6. **App Bridge not loaded first in `<head>`** (2.2.3) — `<script src="https://cdn.shopify.com/shopifycloud/app-bridge.js">` must be the first script element, before any other script tags. Session tokens silently break otherwise.
7. **`read_all_orders` without strong justification** (3.2.1) — only request it if you need >60-day order history. Justification text in Partner Dashboard must explain the analytical purpose (velocity / restock forecasting / trend windows).
8. **Stats / guarantees / "the only" / "the best" in listing copy** (4.3.3, 4.3.4) — including images. Focus on benefits, not claims.
9. **Browser chrome in screenshots** (4.4.4) — no address bars, tabs, OS chrome. Use the embedded UI fullscreen.
10. **Missing mandatory GDPR webhook handlers** — `customers/data_request`, `customers/redact`, `shop/redact` must all return 200 with valid HMAC validation, AND be declared in `[webhooks.privacy_compliance]` in `shopify.app.toml`.
11. **Pricing inconsistency** — the dollar amount in `shopify.app.toml` billing block, in `app/routes/app.billing.tsx` UI, and on the listing must all match exactly.
12. **App name mismatch between TOML and listing** (4.1.1) — must be identical or share common words. "SyncApp — Stock Intelligence" vs "Stockalign" gets rejected as duplicate.
13. **Pricing plan not changeable in-app** (1.2.3) — must support upgrade/downgrade without contacting support and without reinstalling.
14. **Emergency developer contact missing** (4.5.6) — Partner Dashboard → Account settings.
15. **Protected customer data Level mis-declared** — if you don't persist customer fields (name/email/phone/address) but you have `read_orders` scope, declare Level 0 with the justification "order metadata only, no customer fields persisted". Don't over-declare; it triggers an extra data protection review.

## The pre-submission checklist (run through every time)

Mechanical checks any agent can verify:

- [ ] `shopify.app.toml`: `embedded = true`, `api_version = "2026-04"` (or current latest), all webhook subscriptions present, `[webhooks.privacy_compliance]` block with all three URLs
- [ ] `app/root.tsx`: App Bridge script tag is the **first** script element in `<head>`, sourced from `https://cdn.shopify.com/shopifycloud/app-bridge.js`, with `data-api-key={apiKey}` attribute
- [ ] `app/shopify.server.ts`: `apiVersion: ApiVersion.AprilXX` matches TOML; `billing` block with `BillingInterval.Every30Days` and `trialDays` for every plan
- [ ] No REST API: `grep -rn "admin\.rest\|REST API\|/admin/api/.*\.json" app/` returns empty
- [ ] All three GDPR webhook handlers exist + return 200 with HMAC verification: `CUSTOMERS_DATA_REQUEST`, `CUSTOMERS_REDACT`, `SHOP_REDACT`
- [ ] Plan upgrade + downgrade work from inside the app, without contacting support
- [ ] OAuth re-runs on reinstall (uninstalled shops flip back to active on reauthorize)
- [ ] Token storage encrypts access tokens at rest (AES-256-GCM via env-derived key)
- [ ] HTTPS valid: production app URL responds with a non-expired TLS cert
- [ ] Static asset checks: no Shopify trademark in app icon, no browser chrome in screenshots, no pricing in any image
- [ ] Listing-form fields filled: app card subtitle (no stats, under ~100 chars), accurate tags, geographic and language declarations match reality
- [ ] Test instructions in Partner Dashboard include: how to install, login credentials, **how to access paid features without payment**, any invite-code/2nd-store workflow
- [ ] Emergency developer contact set in Partner Dashboard → Account settings

See [`rejection-prevention.md`](rejection-prevention.md) for the full pre-flight checklist with the things that bite in practice.

## Partner Dashboard fields that matter at review

Things you can ONLY see/edit in the Partner Dashboard (not in the codebase). When auditing, ask the user explicitly:

- Protected customer data level (0/1/2) and justification text
- `read_all_orders` justification (if requested)
- App listing screenshots vs Feature media (separate upload slots)
- Test instructions for the review team
- Pricing plans matching billing block
- Emergency contact email
- App category + tags + supported languages + geographic availability
- App icon (must be identical to what TOML/Dev Dashboard shows)

## Source of truth

Crawled directly from these pages on 2026-05-25:

- `shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements`
- `shopify.dev/docs/apps/launch/privacy-requirements`
- `shopify.dev/docs/apps/launch/protected-customer-data`
- `shopify.dev/docs/apps/launch/billing`
- `shopify.dev/docs/apps/launch/deployment`

When in doubt, re-fetch — Shopify updates these pages without notice and the canonical numbers (`2.2.4`, `4.4.4`, etc.) sometimes shift.
