# Sections 3 + 4: Security and App Store Listing

## Section 3 — Security

### 3.1 Valid TLS/SSL certificate

| # | Requirement | Implementation notes |
|---|---|---|
| 3.1.1 | Valid TLS/SSL cert without errors | Let's Encrypt is acceptable; just make sure auto-renewal works. Reviewers will reject on any cert warning, expired chain, or HTTP fallback. Run `curl -vI https://your-app-url` before submit. |

### 3.2 Request only necessary access scopes

| # | Scope | When justification is REQUIRED |
|---|---|---|
| 3.2.1 | `read_all_orders` | Always — and only if you need orders >60 days old. Velocity / restock / trend analytics over 90+ day windows is the standard justification. |
| 3.2.2 | `write_payment_mandate` | Payment apps only. Justification must be explicit. |
| 3.2.3 | `write_checkout_extensions_apis` | Checkout-customization apps only. |
| 3.2.4 | `read_advanced_dom_pixel_events` | Heatmap or session-recording functionality on checkout pages only. |
| 3.2.5 | `read_checkout_extensions_chat` | Chat-in-checkout extensions only. |

**Scope-minimality principle**: only request scopes the code actually uses. Run `grep -rn "admin.graphql\|@shopify/shopify-app-remix" app/` and confirm every mutation/query you make is covered by the scopes you declared — and conversely, that you don't declare any scope you don't use. Common over-requests to remove:

- `write_products` — only if you create/edit products. Reading + tagging doesn't need it.
- `write_locations` — only if you create locations. Reading doesn't need it.
- `read_customers` — only if you store customer fields. Order webhooks send customer data alongside but you don't NEED `read_customers` to receive `orders/create`.
- `write_fulfillments` — only if you create fulfillments.

Use the `[access_scopes]` block in `shopify.app.toml`:

```toml
[access_scopes]
scopes = "read_products,write_inventory,read_locations,read_orders,read_all_orders"
```

**Optional scopes**: for scopes that aren't required for ALL merchants, use the optional-scopes API. Don't bundle them with required scopes.

---

## Section 4 — App Store Listing

This is the section that **causes the most review delays**. Listing assets live in the Partner Dashboard, not in the codebase, so most checks are manual.

### 4.1 Brand your app consistently

| # | Requirement | Check |
|---|---|---|
| 4.1.1 | App name fields must be similar | Name in `shopify.app.toml` `name = "..."`, name in Dev Dashboard, name in App Submission form — all three must match exactly, or share clearly common words. "Stockalign" vs "Stockalign - Multi-Store Sync" is fine. "SyncApp - Stock Intelligence" vs "Stockalign" is NOT fine and gets rejected as duplicate. |
| 4.1.2 | App icon uploaded + identical | Same image in Dev Dashboard and listing form. |

### 4.2 Pricing — accurate and in designated areas

| # | Requirement | Check |
|---|---|---|
| 4.2.1 | Pricing accurate + complete | Include free trial length, all plan tiers, currency, recurring interval. |
| 4.2.2 | **No pricing in images** | Including app icon. Even a "$99/mo Enterprise" screenshot caption is grounds for rejection. |
| 4.2.3 | **No pricing elsewhere in listing** | Pricing only lives in the dedicated Pricing details section of the listing form. Don't repeat in description or screenshots. |

The full set of pricing facts must match across: `shopify.app.toml` billing block → `shopify.server.ts` billing config → seeded `plans` table → listing pricing details. Mismatch is a guaranteed delay.

### 4.3 Accurate and truthful listing information

| # | Requirement | Check |
|---|---|---|
| 4.3.1 | Indicate Online Store sales channel requirement | Only check "Online Store required" if your app embeds in the storefront theme. Inventory / analytics / utility apps shouldn't check this. |
| 4.3.2 | Only claim languages you fully support | The languages list in your listing must match the languages your embedded UI actually has translations for. Romanian-in-marketing-copy-only doesn't count. |
| 4.3.3 | **No stats / data / claims in listing copy** | Banned phrases: "the only", "the first", "the best", "saves up to X%", "syncs in 0.5s", "10,000 stores trust us", "99.9% uptime". Focus on benefits + features. |
| 4.3.4 | **No stats / data / claims in images** | Including mocked dollar figures. A stockout-impact screenshot showing "$14,328 revenue recovered" gets rejected as a data claim. Use `$X,XXX` placeholder or move that frame to Feature Media (rules are looser there but still applicable). |
| 4.3.5 | Accurate tags | Tags must reflect primary functionality. Don't keyword-stuff. |
| 4.3.6 / 4.3.7 | **No reviews / testimonials anywhere** | Reviews are auto-collected by Shopify after launch. Don't paste "★★★★★ - Acme Co" in screenshots or copy. |
| 4.3.8 | Geographic requirements | Set if your app only works for merchants in specific countries (e.g., shipping rules apps). |

### 4.4 Clear assets and descriptions

| # | Requirement | Check |
|---|---|---|
| 4.4.1 | Effective app card subtitle | Concise phrase summarizing what + why. <100 chars. No data claims. No keyword stuffing. Don't say "best multi-store sync app"; do say "Sync inventory across multiple Shopify stores by barcode". |
| 4.4.2 | Clear app details | The long-form description must walk through actual features with enough info to install confidently. Avoid pure bullet lists. |
| 4.4.3 | **No Shopify trademark in graphics** | No Shopify logo, no "S" mark in icon/banner/screenshots. Allowed: "Works with Shopify" badge per their brand guidelines. |
| 4.4.4 | **Clear focused images** | Screenshots must "primarily show your app's actual user interface". **No browser chrome** (address bar, tabs, OS chrome). **No logo-only frames.** No desktop backgrounds. |
| 4.4.5 | Unique images | No duplicate or near-identical images. Each screenshot must show a different feature/view/state. |

### Screenshots vs Feature media — the distinction that gets people

The Partner Dashboard has TWO image-upload slots:

- **Screenshots** — must be actual UI captures of your app (no browser chrome, no marketing overlays, no logo-only frames). Strict interpretation of 4.4.4.
- **Feature media** — your hero / problem / solution / pricing marketing frames. Allowed to be stylized. Allowed to have brand colors, lifestyle imagery, abstract visualizations.

If you generated 12 marketing frames and 5 actual UI screenshots, put the marketing frames in Feature media and the UI captures in Screenshots. Uploading marketing frames to the Screenshots slot is a top-5 rejection reason in 2026.

### 4.5 Complete and accurate submission

| # | Requirement | Check |
|---|---|---|
| 4.5.1 | Submit Sales Channel apps in their category | If your app is a Sales Channel (marketplace, social commerce), follow the Sales Channel app rules. Most utility apps are NOT Sales Channels. |
| 4.5.2 | Submit non-Sales-Channels as regular apps | Don't accidentally enable Sales Channel mode if you're not one. |
| 4.5.3 | **Demo screencast** | 60-90 second video. English audio or English subtitles. Step-by-step setup of your core feature. The listing video slot is short; longer walkthroughs belong on YouTube linked from the description. |
| 4.5.4 | Test credentials provided | Login the reviewer can use. Keep updated. |
| 4.5.5 | **Test credentials must access full feature set** | Reviewers will NOT pay $99/mo to test Enterprise features. Either set the test merchant to the highest plan, or provide a comp/redeem-code flow in the test instructions. Document the path step-by-step. |
| 4.5.6 | Emergency developer contact | In Partner Dashboard → Account settings. Use a monitored email; Shopify uses this for critical security or compliance notices. |

### Test instructions template (paste this into Partner Dashboard)

```
TEST CREDENTIALS
- Install URL: <provided automatically by Shopify reviewer>
- Test merchant email: <your test account>
- Test merchant password: <your test password>

ACCESSING PAID FEATURES
The app is workspace-billed: one subscription covers all connected stores.
To access Enterprise-tier features without payment during review, after install:
1. Open the app from the test store admin
2. Navigate to Billing
3. Click "Have a code?"
4. Enter redeem code: <YOUR_REDEEM_CODE>
5. Enterprise tier activates immediately for this workspace.

CORE FEATURE WALKTHROUGH (5 minutes)
1. From the dashboard, click "Connect Store" → "Generate invite code"
2. Install the app on a second test store (we recommend Shopify's dev stores)
3. On the second store, open the app → "Connect Store" → paste the invite code
4. Return to the first store → Barcode Groups (products auto-matched)
5. Make a stock change in Shopify Admin on store 1 → watch Live Activity feed
6. Navigate to Reports → Stockout Impact for the analytics view

EMERGENCY CONTACT
<your monitored email>
Response time: <X> hours business days.
```

### Listing copy review patterns

Before submitting, grep your listing description for:

- `\b(the\s+)?(only|first|best|fastest|most|leading|top)\b` — usually data claims
- `\b\d+\s*(%|times|x)\b` — quantitative claims need to go
- `\b(guarantee|guaranteed|prov(en|ide))\b` — promises need to go
- `★★★|⭐` — testimonials/reviews need to go
- Sample pricing in any non-pricing section
