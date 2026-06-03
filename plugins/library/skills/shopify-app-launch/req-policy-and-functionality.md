# Sections 1 + 2: Policy and Functionality

## Section 1 — Policy

### 1.1 Build and operate within Shopify's platform

| # | Requirement | What it means in practice |
|---|---|---|
| 1.1.1 | Use session tokens for authentication | Embedded app must function without third-party cookies. Verified by reviewers in Chrome incognito. App Bridge handles this if loaded correctly (see 2.2.3). |
| 1.1.2 | Use Shopify checkout | Never accept payment outside Shopify Checkout. N/A for most utility apps. |
| 1.1.3 | Don't let merchants download themes | Theme installs must route through Shopify Theme Store only. |
| 1.1.4 | Use only factual information | No fake reviews, fake purchase notifications, fabricated data. |
| 1.1.5 | Create unique apps | Don't publish two near-identical apps under one Partner account. |
| 1.1.6 | Build single-merchant storefronts, not marketplaces | Marketplaces must be Sales Channel apps, not regular apps. |
| 1.1.7 | Payment Gateway apps require Payments API + authorization | Special-case, application-gated. |
| 1.1.8 | POS apps for Shopify POS only | No third-party POS bridges. |
| 1.1.9 | Explicit buyer consent for charges | Can't add upsells silently to cart. |
| 1.1.10 | Cheapest shipping default | Re-ordering shipping rates to push expensive defaults is banned. |
| 1.1.11 | Browser extensions optional only | App can't require a Chrome extension to work. |
| 1.1.12 | Build web-based apps | No desktop-app dependency. |
| 1.1.13 | Duplicate only authorized product info | No "import from any store" Chrome-extension-style scraping. |
| 1.1.14 | Don't broker agencies/freelancers | No connecting merchants to outside contractors. |
| 1.1.15 | Refunds through original processor | `refundCreate` or `returnProcess` only. |
| 1.1.16 | No capital lending | Loans, cash advances, factoring → not allowed on App Store. |

For a typical inventory / catalog / analytics / marketing utility app, **1.1.1 + 1.1.4 are the only ones that affect daily development**. The rest are N/A.

### 1.2 Bill through the Shopify Billing API or Shopify App Pricing

| # | Requirement | Implementation notes |
|---|---|---|
| 1.2.1 | Use Shopify Billing API / App Pricing | Off-platform billing = instant rejection. Even Stripe-only is banned. |
| 1.2.2 | Implement correctly: accept/decline/re-request on reinstall | On reinstall the previous subscription is gone; your `afterAuth` must be able to re-request. |
| 1.2.3 | Allow plan changes self-serve | Merchant must be able to upgrade AND downgrade without contacting support or reinstalling. |

**Workspace billing pattern** (for multi-shop apps): only one shop in the workspace pays. Designate a `primaryBillingShopId` on the workspace. When other shops in the workspace install, they get a "no billing required, inherits from workspace" path. The Shopify Billing API treats each shop as independent — your app abstracts the multi-shop billing on top. See [`shopify-app-patterns/billing.md`](../shopify-app-patterns/billing.md).

**Comp/redeem-code pattern** (for self-hosted free tiers): if you want certain merchants on a free plan without going through Shopify Billing (typically the app owner's own stores), implement an in-app redeem code that flips an `isCompPlan` flag on the billing record. Document this in test instructions so reviewers can use it to access paid features.

---

## Section 2 — Functionality

### 2.1 Create reliable and user-friendly apps

| # | Requirement | Mechanical check |
|---|---|---|
| 2.1.1 | No critical errors blocking review | No 4xx / 5xx pages encountered during a fresh-install walkthrough. Smoke-test the install → onboarding → core feature → uninstall flow. |
| 2.1.2 | No minor errors partially blocking review | Every nav link must work. Every primary button must do something. |
| 2.1.3 | UI-operational | App must do something via UI even if other surfaces exist (CLI, API). |
| 2.1.4 | Synchronize data accurately | If you write data anywhere, it has to match what's in Shopify. This is the requirement that justifies CAS, dedup, reconciliation jobs, etc. |

### 2.2 Use Shopify's APIs and platform tools

| # | Requirement | Implementation notes |
|---|---|---|
| 2.2.1 | Use Shopify APIs | Even minimal use — pure-frontend apps with no Shopify call are rejected. |
| 2.2.2 | Consistent embedded experience | `embedded = true` in TOML. Off-platform features must be reachable from inside the embedded admin (link out is OK; redirect-out as the primary experience is not). |
| 2.2.3 | **Latest App Bridge, loaded first in `<head>`** | `<script src="https://cdn.shopify.com/shopifycloud/app-bridge.js" data-api-key={apiKey}>` must be the FIRST `<script>` element. The CDN URL is unversioned — Shopify pins to the current major. Don't `npm install @shopify/app-bridge`. |
| 2.2.4 | **GraphQL Admin API only** (as of April 2025) | REST is legacy. New apps published with REST calls fail review. Grep your codebase for `admin.rest`, `client.get`, `client.post`, `/admin/api/*.json` → zero hits required. |
| 2.2.5 | Admin extensions feature-complete | If you ship admin UI blocks / actions / links, each must do real work. Stubs are rejected. |
| 2.2.6 | No promotions in admin extensions | Don't use extension surfaces to advertise other apps or request reviews. |
| 2.2.7 | Max modal only on merchant interaction | `app:max-modal` can't auto-launch from nav. |

### 2.3 Provide seamless and secure installation

| # | Requirement | Implementation notes |
|---|---|---|
| 2.3.1 | Install initiated from Shopify-owned surface | No "enter your store URL" input field. Install must originate from the App Store, Partner Dashboard, or another Shopify embed. |
| 2.3.2 | OAuth before any UI interaction | Don't let merchants click anything before tokens are issued. The Shopify SDK's `authenticate.admin(request)` enforces this. |
| 2.3.3 | Redirect to UI after install | After OAuth approval, route the merchant straight to `/app` (your main embedded surface). |
| 2.3.4 | Re-OAuth immediately on reinstall | `afterAuth` must run again. If you cache install state per shop and skip OAuth on the second install, review fails. Mark uninstalled shops as `status="uninstalled"` on `APP_UNINSTALLED`, then flip back to `"active"` in `afterAuth`. |

## afterAuth idempotency (the silent bug)

`afterAuth` can fire **twice** during install due to a cluster-mode race in `@shopify/shopify-app-remix` (initial Admin iframe load + session re-establishment). The losing race can:

- Create a duplicate Workspace if not deduped on shopDomain
- Run auto-import twice — wasting Shopify API quota
- Patch billing twice — usually safe but causes log noise

**Mitigation**: enqueue post-install work (auto-import, welcome email, etc.) to BullMQ with `jobId = shopId` so duplicate enqueues are silently dropped. Don't use `setTimeout` — it doesn't survive cluster-mode worker fork.

See [`shopify-app-patterns/oauth-and-session.md`](../shopify-app-patterns/oauth-and-session.md) for the full pattern.
