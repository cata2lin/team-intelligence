# Rejection Prevention — Pre-Submission Checklist

What actually gets apps rejected, in priority order, with the mechanical check for each.

## Tier 1 — Auto-rejected without further review

These are deal-breakers. The reviewer doesn't even look at functionality if any of these fail.

- [ ] **No REST API anywhere in code** (req 2.2.4). Check: `grep -rn "admin\.rest\|REST API\|/admin/api/\d{4}-\d{2}/.*\.json" app/` returns empty.
- [ ] **App Bridge from CDN, first script in `<head>`** (req 2.2.3). Check: `<script src="https://cdn.shopify.com/shopifycloud/app-bridge.js" data-api-key={apiKey}>` is the first `<script>` in `app/root.tsx`.
- [ ] **Three GDPR webhook handlers respond 200 + HMAC valid** (privacy). Check: `customers/data_request`, `customers/redact`, `shop/redact` cases in your webhook switch, AND `[webhooks.privacy_compliance]` block in `shopify.app.toml`.
- [ ] **Shopify Billing API for any charges** (req 1.2.1). No Stripe, no PayPal, no off-platform billing.
- [ ] **Valid TLS cert on production URL** (req 3.1.1). Check: `curl -vI https://your-app.example` returns no warnings, valid chain, not expired.
- [ ] **Test credentials grant full feature access** (req 4.5.5). Reviewers won't pay. Document a comp/redeem path or set the test merchant on the highest plan.
- [ ] **`embedded = true` in `shopify.app.toml`** unless explicitly building a standalone app (rare).

## Tier 2 — Likely rejection on first pass

- [ ] **Pricing in screenshots / app icon / non-pricing copy** (req 4.2.2, 4.2.3). Audit every image manually; even mocked `$X,XXX` sample data counts.
- [ ] **Marketing-frame images in the Screenshots slot** (req 4.4.4). Hero/problem/solution stylized frames go in Feature Media slot only. Screenshots slot is for actual UI captures.
- [ ] **Browser chrome in screenshots** (req 4.4.4). No address bars, tabs, OS chrome, macOS traffic-light buttons.
- [ ] **Data claims / superlatives in listing copy** (req 4.3.3). Grep listing for: "the only", "the best", "the first", "the fastest", "guarantee", "proven", any "%" or "X faster" claims.
- [ ] **`read_all_orders` without strong justification** (req 3.2.1). The justification text in Partner Dashboard must explain the analytical purpose explicitly: "needed for >60-day order history used in N-day rolling velocity calculations".
- [ ] **App name mismatch** (req 4.1.1). TOML `name` and listing form name must be identical or share clearly common words.
- [ ] **App icon mismatch** (req 4.1.2). Same image in Dev Dashboard and listing.
- [ ] **Plan upgrade/downgrade requires support contact** (req 1.2.3). Must be self-serve via the in-app billing page.
- [ ] **Emergency developer contact missing** (req 4.5.6). Partner Dashboard → Account settings → Emergency contact.
- [ ] **Demo screencast too long, no English subtitles, or not actually showing setup** (req 4.5.3). 60-90s, English audio or English subtitles, must show install + onboarding + core feature.
- [ ] **Onboarding errors during fresh-install walkthrough** (req 2.1.1, 2.1.2). 404 / 500 / blank screens on any step of: install → OAuth → redirect → onboarding → first feature use.
- [ ] **Language declaration mismatch** (req 4.3.2). Only list languages the embedded UI is actually translated to.

## Tier 3 — Will trigger feedback, may delay rather than reject

- [ ] **Protected customer data Level mis-declared** (privacy). If you don't persist customer fields, declare Level 0 with the precise justification text. Over-declaring triggers a data protection review.
- [ ] **Privacy policy missing sub-processor table** (privacy). List every third party touching the data.
- [ ] **App card subtitle has keyword stuffing or claims** (req 4.4.1). Tight phrase summarizing what the app does, no SEO-stuffing.
- [ ] **Reinstall doesn't re-OAuth properly** (req 2.3.4). On uninstall, mark shop status; on reauthorize, run `afterAuth` again.
- [ ] **Webhook signatures not verified**. Use `authenticate.webhook(request)` from `@shopify/shopify-app-remix` for every webhook handler.
- [ ] **Webhook duplicates not deduplicated**. Shopify can deliver the same event multiple times. Use `X-Shopify-Webhook-Id` header as a Redis SETNX dedup key with reasonable TTL (60-120s).
- [ ] **Onboarding doesn't redirect to UI after install** (req 2.3.3).
- [ ] **Reviews / testimonials in copy or images** (req 4.3.6, 4.3.7).
- [ ] **Shopify trademark misuse in graphics** (req 4.4.3).
- [ ] **Tags don't reflect primary functionality** (req 4.3.5).
- [ ] **Unused scopes declared** (req 3.2). Run `grep -rn "admin.graphql" app/` and verify every scope declared corresponds to a mutation/query actually invoked.

## Final smoke test before submitting

Do this fresh on a never-installed test store:

1. Install from Partner Dashboard install link → OAuth → land in app
2. Walk through entire onboarding without skipping
3. Use the core feature on a real product
4. Open every page in the nav
5. Trigger the redeem code / test plan
6. Uninstall, then reinstall, walk through again
7. Watch every network request — no 4xx, no 5xx, no console errors

If any step fails or shows an error page, fix it before submitting. The reviewer follows roughly this script.

## Resubmission tips when rejected

Shopify rejection emails reference requirement numbers (e.g., "Issue with requirement 4.4.4"). Don't argue, just fix:

1. Identify the specific image / copy / behavior they cite
2. Cross-check against the requirement text (refetch the docs, language drifts)
3. Fix it
4. Re-submit, citing in the resubmission notes: "Updated [X] to address feedback on requirement [N.N.N]. Specifically [what changed]."

Concise, specific resubmission notes get re-reviewed faster than vague "fixed your feedback" replies.
