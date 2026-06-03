# Shopify Admin API — Webhooks Reference

> Authoritative reference for SyncApp on Shopify Admin GraphQL API **2026-04**.
> Sourced from shopify.dev (fetched 2026-05-24). When in doubt, this file > training data.
> Covers: subscription model, HMAC verification, headers, retry policy, deduplication, per-topic payload schemas, mutations/queries, compliance topics, protected customer data, and `shopify.app.toml` config.

---

## Table of Contents

1. [Subscription Model Overview](#1-subscription-model-overview)
2. [HMAC Verification](#2-hmac-verification)
3. [Headers Shopify Sends](#3-headers-shopify-sends)
4. [Retry Policy](#4-retry-policy)
5. [Deduplication Strategy](#5-deduplication-strategy)
6. [Per-Topic Deep Dive](#6-per-topic-deep-dive)
7. [Subscription Mutations & Queries](#7-subscription-mutations--queries)
8. [Compliance Topics (GDPR / Privacy)](#8-compliance-topics-gdpr--privacy)
9. [Protected Customer Data](#9-protected-customer-data)
10. [App Config (`shopify.app.toml`)](#10-app-config-shopifyapptoml)
11. [SyncApp-Specific Gotchas](#11-syncapp-specific-gotchas)

---

## 1. Subscription Model Overview

A webhook subscription is `(shop, topic, endpoint)` — Shopify pushes a payload to your endpoint every time `topic` fires on `shop`. Subscriptions are **per-shop**: even though you declare them once in `shopify.app.toml`, Shopify materializes one subscription per shop installation.

### Delivery transports

Shopify supports three delivery transports. Choose at subscription-create time; you cannot mix transports in a single subscription.

| Transport | When to use | URI format | Latency | Cost |
|---|---|---|---|---|
| **HTTPS** | Default for self-hosted apps with a public TLS endpoint. SyncApp uses this. | `https://app.example.com/webhooks` | Sub-second usually; 5s timeout (see [Retry Policy](#4-retry-policy)). | Free; you pay for compute. |
| **Amazon EventBridge** | High-throughput apps already on AWS. Avoids the timeout/retry headaches of HTTPS — EventBridge buffers, your consumer pulls. | `arn:aws:events:<region>::event-source/aws.partner/shopify.com/<account-id>/<source-name>` | Sub-second to EventBridge; downstream is your problem. | EventBridge pricing applies. |
| **Google Pub/Sub** | High-throughput on GCP. Same buffering advantage as EventBridge. | `pubsub://<project-id>:<topic-id>` | Sub-second to Pub/Sub. | Pub/Sub pricing applies. |

### Scoping rules

- One subscription = one topic + one URI (per shop).
- You can have multiple subscriptions to the *same* topic with *different* URIs (e.g., production endpoint + audit-log endpoint). Each fires independently — same `X-Shopify-Event-Id`, different `X-Shopify-Webhook-Id`.
- Subscriptions are created/updated/deleted per shop. App-config subscriptions (in `shopify.app.toml`) get auto-provisioned on install and removed on uninstall.
- The Admin API version of a subscription is **inherited from the app's current API version** at subscription-create time. You cannot pin a different version per subscription.
- Subscription identifiers are GraphQL global IDs: `gid://shopify/WebhookSubscription/<numeric>`.

### Two ways to declare subscriptions

| Method | Where | Trade-offs |
|---|---|---|
| **App config (`shopify.app.toml`)** | `[[webhooks.subscriptions]]` blocks. Shopify auto-syncs on `shopify app deploy`. | Recommended. Uniform across all shops, declarative, no per-shop API calls, free management. Mandatory for compliance topics. |
| **API (`webhookSubscriptionCreate` GraphQL mutation)** | Called from your app's `afterAuth` hook or admin endpoints. | Required when subscriptions differ per shop (e.g., shop-specific URIs, custom topic enables based on plan, optional integrations). Returns a queryable subscription ID. |

You can mix the two: compliance topics in config, optional integrations via API. **Don't double-subscribe** (same topic + same URI both in config and via API) — Shopify will reject the API call with a "already subscribed" `userError`.

---

## 2. HMAC Verification

Every HTTPS webhook delivery includes an HMAC signature in the `X-Shopify-Hmac-Sha256` header. You **must** verify it before trusting the payload. The signature is computed by Shopify using:

```
HMAC-SHA256(message = raw_request_body, key = client_secret) → base64
```

### Exact algorithm

1. Grab the raw request body **as bytes**, before any JSON parsing, middleware, or normalization. Even a trailing newline difference breaks the comparison.
2. Compute `hmac-sha256(client_secret, raw_body)`.
3. Base64-encode the digest.
4. Read the `X-Shopify-Hmac-Sha256` header.
5. Compare with a **constant-time** comparison (`crypto.timingSafeEqual` in Node, `hmac.compare_digest` in Python). Never use `===` / `==` — exposes you to timing attacks.

### What the secret is

- For app-managed (HTTPS) webhooks declared in `shopify.app.toml` or created via the Admin API, the key is your **app's client secret** (a.k.a. API secret). Same secret used for OAuth.
- For App Bridge / managed installation flows, this is `SHOPIFY_API_SECRET`.
- For EventBridge / Pub/Sub, no HMAC — auth is handled by AWS/GCP IAM.

### Node.js reference implementation

```ts
import crypto from "node:crypto";

function verifyShopifyHmac(rawBody: Buffer | string, signatureHeader: string, secret: string): boolean {
  if (!signatureHeader) return false;
  const digest = crypto.createHmac("sha256", secret).update(rawBody).digest("base64");
  const a = Buffer.from(digest);
  const b = Buffer.from(signatureHeader);
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}
```

### Encoding gotchas

- It's **base64**, not hex. `0xA1B2…` is hex (used for OAuth callback verification in older flows). Webhooks use base64.
- The body **must** be UTF-8 bytes. If your framework rewrites the body (e.g., JSON-parses then re-serializes), recomputed HMAC won't match. In Express, use `express.raw({ type: "application/json" })` for the webhook route; in React Router 7 use `request.text()` (not `request.json()`) before verification.
- A rotated client secret takes **up to one hour** to propagate. During rotation, accept either the old or new secret.
- Return **HTTP 401** when HMAC fails. Returning anything 2xx tells Shopify the webhook was delivered successfully and dedupe state on their side advances; you'd silently swallow events.

---

## 3. Headers Shopify Sends

Every HTTPS webhook request includes the following headers. Treat all as untrusted strings until HMAC is verified.

| Header | Type | Example | Purpose |
|---|---|---|---|
| `X-Shopify-Topic` | string | `inventory_levels/update` | Topic identifier in snake_case slash format. Don't rely on the JSON body to identify topic — the header is canonical. |
| `X-Shopify-Shop-Domain` | string | `acme.myshopify.com` | The myshopify subdomain of the shop firing the webhook. Use this (not the body) to load shop credentials. |
| `X-Shopify-Hmac-Sha256` | string (base64) | `XWmrwMey6OsLMeiZKwP4FppHH3cmAiiJJAweH5Jo4bM=` | The HMAC signature. See [HMAC Verification](#2-hmac-verification). |
| `X-Shopify-Webhook-Id` | UUID | `b54557e4-c249-4b8c-b3d5-2ad5b8c5ad2a` | **Canonical event identifier** for dedup. Unique per delivery attempt-set (retries share the same ID). See [Deduplication Strategy](#5-deduplication-strategy). |
| `X-Shopify-Event-Id` | UUID | (same format as webhook-id but different value) | Shared across multiple subscriptions to the same underlying event. If two subscriptions both listen to `orders/create`, they share `X-Shopify-Event-Id` but have distinct `X-Shopify-Webhook-Id`. |
| `X-Shopify-Triggered-At` | ISO 8601 | `2026-05-24T10:11:12.345Z` | Timestamp the source event happened in Shopify's system. Useful for last-write-wins reconciliation. |
| `X-Shopify-Api-Version` | string | `2026-04` | API version Shopify serialized this payload with. Matches the app's configured webhook API version. |
| `Content-Type` | string | `application/json` (or `application/xml` if `format = "XML"`) | Body format. |
| `User-Agent` | string | `Shopify-Captain-Hook` | Diagnostic. Not auth. |

**Important:** Shopify is case-insensitive on header names per RFC 7230, but framework casing can vary. Always read headers via case-insensitive accessors (`request.headers.get(...)` in Fetch API).

---

## 4. Retry Policy

### Success criteria

Shopify considers a webhook **delivered** only on **HTTP 2xx**. Specifically:

- ✅ `200 OK`, `201`, `204` → delivered.
- ❌ Anything 3xx (including 301/302) → **failure**, retried.
- ❌ Anything 4xx (401, 403, 404, 422) → **failure**, retried.
- ❌ Anything 5xx → **failure**, retried.
- ❌ Connection refused / TLS error → **failure**, retried.
- ❌ Timeout (see below) → **failure**, retried.

### Timeouts

- **TCP/TLS connect**: 1 second.
- **Full response**: 5 seconds. Your endpoint must read the body, do the work (or queue it), and write a 2xx response within 5s. **Always queue and return 200 immediately — never do real work synchronously in the webhook handler.**

### Retry schedule

After a failure, Shopify retries **up to 19 times over ~48 hours** with exponential backoff. The schedule is roughly:

| Attempt | Time after first failure | Cumulative |
|---|---|---|
| 1 (original) | 0s | 0s |
| 2 | ~1m | ~1m |
| 3 | ~5m | ~6m |
| 4 | ~10m | ~16m |
| 5 | ~30m | ~46m |
| 6 | ~1h | ~1h46m |
| 7–10 | ~2h apart | ~9h |
| 11–14 | ~4h apart | ~25h |
| 15–19 | ~6h apart | ~49h |

Documentation worded variously across sources: "8 retries over 4 hours" (older HTTPS reference), "19 retries over 48 hours" (current behavior in 2026-04). Treat 48h as the effective hard ceiling — design dedupe windows around that.

### Auto-removal of failing subscriptions

If a subscription accumulates failures continuously and crosses the **48-49 hour** mark with no successful delivery, Shopify automatically **removes** the subscription. App-config subscriptions (`shopify.app.toml`) are re-provisioned on the next `shopify app deploy`, but API-created subscriptions are gone — your app must re-create them via `webhookSubscriptionCreate` after detecting the gap.

**SyncApp implication**: maintain a periodic reconciliation job that lists `webhookSubscriptions` per shop and re-creates anything missing. The `reconciliation` worker is a natural home.

### Ordering guarantees

**Webhooks are not ordered.** A retry of an earlier event can arrive after a later event delivered on the first try. Always use `X-Shopify-Triggered-At` plus the resource's `updated_at` field to decide whether to apply an update — last-write-wins by timestamp, not by arrival order.

---

## 5. Deduplication Strategy

### The canonical event ID

The header **`X-Shopify-Webhook-Id`** is the deduplication key. It is:

- A UUID.
- **Identical across retries** of the same event delivery to the same subscription.
- **Different across subscriptions** even for the same underlying event (compare `X-Shopify-Event-Id` to detect that case).

Persist `X-Shopify-Webhook-Id` in a dedup store (Redis with 72h TTL covers all retries plus margin). On every request: check the ID; if seen, return 200 immediately without re-processing.

### Why you can't dedupe by payload `id`

Most topics include an `id` field in the payload (e.g., `orders/create` has `id: 820982911946154508`). But:

- For the same underlying event, retries share the payload `id` *and* the webhook id, so payload id would also work as dedup key — **for those topics**.
- **`inventory_levels/*`, `inventory_items/*`, `locations/*`, `fulfillments/*`, `refunds/*`, `order_transactions/*`, `app_subscriptions/*`, `variants/in_stock`, `variants/out_of_stock`, `bulk_operations/finish`, `shop/redact`, `customers/redact`, `customers/data_request`, `product_feeds/update` have NO root `id` field.** Payloads are wrapped in a resource object (`{ "inventory_level": {...} }`, `{ "fulfillment": {...} }`, etc.) — the id lives inside, and it's not unique per event (the same inventory level can update many times).
- Falling back to `payload?.id || Date.now()` (a real bug pattern) silently breaks dedup: every retry gets a fresh `Date.now()` and is treated as new.

### The fix: composite event key for unwrapped topics

For topics with no root id, build a composite key in the **same place that decides BullMQ `jobId`** and feed it BOTH to dedup and to job naming. SyncApp uses:

```
event_key = "<topic>:<x-shopify-webhook-id>"
```

This is **always** unique per delivery, regardless of payload shape. Use `X-Shopify-Webhook-Id` as your single source of truth; only fall back to payload-derived keys if you need application-level idempotency on resource state (e.g., "this inventory level was already at 5 when we processed an earlier webhook — skip"), which is a separate concern from delivery dedup.

### Test / live mode

Webhooks fired in test mode (Shopify CLI's `shopify webhook trigger`) and live mode share the same id space. Production dedup keys should include a `mode` segment if you replay test events into production.

---

## 6. Per-Topic Deep Dive

For each topic below:
- **Trigger**: when Shopify fires it.
- **Root id?**: whether the JSON payload has an `id` field at the top level (critical for dedup and for handler code that expects `payload.id`).
- **Protected customer data?**: whether handling this topic requires Level 1 / Level 2 protected-data approval (see [Protected Customer Data](#9-protected-customer-data)).
- **Mandatory?**: whether all apps must subscribe to this topic.
- **Sample payload**: real JSON from Shopify docs.

> Note on wrapping: Shopify's webhook payloads use **snake_case** field names (REST representation), unlike the **camelCase** of the Admin GraphQL API. The same resource will be `inventoryItemId` in GraphQL and `inventory_item_id` in webhook JSON. SyncApp must keep this in mind when feeding webhook data into a GraphQL-shaped service.

### 6.1 App lifecycle

#### `APP_UNINSTALLED` / `app/uninstalled`

- **Trigger**: A merchant uninstalls your app from their store.
- **Root id?**: **Yes** — payload has `id` (the shop id).
- **Protected customer data?**: No.
- **Mandatory?**: Effectively mandatory — without it you keep credentials for shops that revoked them. Most app frameworks subscribe by default.
- **What to do**: Mark the shop as uninstalled in your DB, clear/scrub access tokens, stop background workers for that shop. Don't delete data yet — `shop/redact` (48h later) is the signal for that.

**Sample payload:**
```json
{
  "id": 548380009,
  "name": "Super Toys",
  "email": "super@supertoys.com",
  "domain": null,
  "province": "Tennessee",
  "country": "US",
  "address1": "190 MacLaren Street",
  "zip": "37178",
  "city": "Houston",
  "source": null,
  "phone": "3213213210",
  "latitude": null,
  "longitude": null,
  "primary_locale": "en",
  "address2": null,
  "created_at": null,
  "updated_at": null,
  "country_code": "US",
  "country_name": "United States",
  "currency": "USD",
  "customer_email": "super@supertoys.com",
  "timezone": "(GMT-05:00) Eastern Time (US & Canada)",
  "iana_timezone": null,
  "shop_owner": "John Smith",
  "money_format": "${{amount}}",
  "money_with_currency_format": "${{amount}} USD",
  "weight_unit": "kg",
  "province_code": "TN",
  "taxes_included": null,
  "auto_configure_tax_inclusivity": null,
  "tax_shipping": null,
  "county_taxes": null,
  "plan_display_name": "Shopify Plus",
  "plan_name": "enterprise",
  "has_discounts": false,
  "has_gift_cards": true,
  "myshopify_domain": null,
  "google_apps_domain": null,
  "google_apps_login_enabled": null,
  "money_in_emails_format": "${{amount}}",
  "money_with_currency_in_emails_format": "${{amount}} USD",
  "eligible_for_payments": true,
  "requires_extra_payments_agreement": false,
  "password_enabled": null,
  "has_storefront": true,
  "finances": true,
  "primary_location_id": 655441491,
  "checkout_api_supported": true,
  "multi_location_enabled": true,
  "setup_required": false,
  "pre_launch_enabled": false,
  "enabled_presentment_currencies": ["USD"],
  "marketing_sms_consent_enabled_at_checkout": false,
  "transactional_sms_disabled": false
}
```

The payload **is the shop resource** — same shape as a REST `GET /admin/api/.../shop.json`.

#### `APP_SUBSCRIPTIONS_UPDATE` / `app_subscriptions/update`

- **Trigger**: An app's billing subscription state changes (created/approved/declined/expired/frozen/cancelled, or capped amount adjusted).
- **Root id?**: **No** — payload is wrapped in `{ "app_subscription": {...} }`. The id is inside.
- **Protected customer data?**: No.
- **Mandatory?**: No, but strongly recommended if you use Shopify Billing.
- **What to do**: Sync your local plan-state for the shop. SyncApp uses this to track plan tier (Starter/Growth/Business/Enterprise) for paywall logic.

**Sample payload:**
```json
{
  "app_subscription": {
    "admin_graphql_api_id": "gid://shopify/AppSubscription/1029266952",
    "name": "Webhook Test",
    "status": "PENDING",
    "admin_graphql_api_shop_id": "gid://shopify/Shop/548380009",
    "created_at": "2021-12-31T19:00:00-05:00",
    "updated_at": "2021-12-31T19:00:00-05:00",
    "currency": "USD",
    "capped_amount": "20.0",
    "price": "10.00",
    "interval": "every_30_days",
    "plan_handle": "plan-123"
  }
}
```

Possible `status` values: `PENDING`, `ACTIVE`, `DECLINED`, `EXPIRED`, `FROZEN`, `CANCELLED`. `interval` is one of `every_30_days` (monthly) or `annual`.

### 6.2 Compliance (GDPR / Privacy)

These three topics are **mandatory for every public app on the App Store**, regardless of whether your app touches customer data. Declare them in `shopify.app.toml` (config-only, not API-created — see [section 8](#8-compliance-topics-gdpr--privacy)).

#### `SHOP_REDACT` / `shop/redact`

- **Trigger**: 48 hours after a shop uninstalls your app, Shopify fires this requesting you delete *all* of that shop's data.
- **Root id?**: No (no wrapper either — just flat fields).
- **Protected customer data?**: Compliance topic, not protected-data.
- **Mandatory?**: **Yes**, for all App Store apps.
- **SLA**: Respond 200 within 5s; complete actual deletion within 30 days.
- **What to do**: Delete every row tied to `shop_id` / `shop_domain`. Unless you have a legal obligation to retain (e.g., financial records, fraud logs), do it. SyncApp must cascade-delete: shop, variants, inventory snapshots, barcode-group memberships, sync history, etc.

**Sample payload:**
```json
{
  "shop_id": 954889,
  "shop_domain": "{shop}.myshopify.com"
}
```

Some older docs list `fields_to_redact: [<string>]`. In the 2026-04 version only `shop_id` and `shop_domain` appear in the reference payload.

#### `CUSTOMERS_REDACT` / `customers/redact`

- **Trigger**: A merchant requests deletion of a specific customer's data (typically because that customer asked them to under GDPR / CCPA / etc.).
- **Root id?**: No.
- **Protected customer data?**: Compliance topic. You receive it only if your app has scopes that touched customer data.
- **Mandatory?**: **Yes**.
- **Timing**: If the customer hasn't placed an order in 6 months: webhook fires 10 days after the merchant's request. Otherwise withheld until 6 months elapsed since their last order.
- **SLA**: 200 within 5s; complete redaction within 30 days unless legally required to retain.
- **What to do**: Delete or anonymize all data about that customer + the listed orders. SyncApp doesn't store customer PII directly, but if you've cached order-line-item history that mentions a customer email, scrub it.

**Sample payload:**
```json
{
  "shop_id": 954889,
  "shop_domain": "{shop}.myshopify.com",
  "customer": {
    "id": 191167,
    "email": "john@example.com",
    "phone": "555-625-1199"
  },
  "orders_to_redact": [299938, 280263, 220458]
}
```

#### `CUSTOMERS_DATA_REQUEST` / `customers/data_request`

- **Trigger**: A merchant requests, on a customer's behalf, that your app provide all the data your app has stored about that customer.
- **Root id?**: No.
- **Protected customer data?**: Compliance topic.
- **Mandatory?**: **Yes**.
- **SLA**: 200 within 5s. Provide the data to the merchant (typically email) within 30 days.
- **What to do**: Gather every record you hold about `customer.id` / `customer.email` / `customer.phone` and any of the listed `orders_requested`, then email the merchant a summary or attach a structured export.

**Sample payload:**
```json
{
  "shop_id": 954889,
  "shop_domain": "{shop}.myshopify.com",
  "customer": {
    "id": 191167,
    "email": "john@example.com",
    "phone": "555-625-1199"
  },
  "orders_requested": [299938, 280263, 220458],
  "data_request": {
    "id": 9999
  }
}
```

### 6.3 Products

#### `PRODUCTS_CREATE` / `products/create`

- **Trigger**: New product is created (admin UI, REST/GraphQL API, bulk import).
- **Root id?**: **Yes** — top-level `id` is the product id.
- **Protected customer data?**: No.
- **Mandatory?**: No.

**Payload shape** (matches REST `Product` resource):
```json
{
  "id": 788032119674292922,
  "admin_graphql_api_id": "gid://shopify/Product/788032119674292922",
  "title": "Example T-Shirt",
  "handle": "example-t-shirt",
  "body_html": "<p>Description</p>",
  "vendor": "Acme",
  "product_type": "Apparel",
  "created_at": "2026-05-24T10:11:12-04:00",
  "updated_at": "2026-05-24T10:11:12-04:00",
  "published_at": "2026-05-24T10:11:12-04:00",
  "template_suffix": null,
  "tags": "summer, sale",
  "status": "active",
  "published_scope": "global",
  "variants": [
    {
      "id": 39072856,
      "product_id": 788032119674292922,
      "title": "Default Title",
      "price": "19.99",
      "sku": "TSHIRT-001",
      "position": 1,
      "inventory_policy": "deny",
      "compare_at_price": null,
      "fulfillment_service": "manual",
      "inventory_management": "shopify",
      "option1": "Default Title",
      "option2": null,
      "option3": null,
      "created_at": "2026-05-24T10:11:12-04:00",
      "updated_at": "2026-05-24T10:11:12-04:00",
      "taxable": true,
      "barcode": "0123456789012",
      "grams": 200,
      "image_id": null,
      "weight": 0.2,
      "weight_unit": "kg",
      "inventory_item_id": 39072856,
      "inventory_quantity": 100,
      "old_inventory_quantity": 100,
      "requires_shipping": true,
      "admin_graphql_api_id": "gid://shopify/ProductVariant/39072856"
    }
  ],
  "options": [
    { "id": 891, "product_id": 788032119674292922, "name": "Title", "position": 1, "values": ["Default Title"] }
  ],
  "images": [
    {
      "id": 850703190,
      "product_id": 788032119674292922,
      "position": 1,
      "created_at": "2026-05-24T10:11:12-04:00",
      "updated_at": "2026-05-24T10:11:12-04:00",
      "alt": null,
      "width": 1024,
      "height": 1024,
      "src": "https://cdn.shopify.com/...",
      "variant_ids": [],
      "admin_graphql_api_id": "gid://shopify/ProductImage/850703190"
    }
  ],
  "image": null
}
```

#### `PRODUCTS_UPDATE` / `products/update`

- **Trigger**: Product itself is updated **or** variants added/removed/updated **or** product is re-ordered within collections. **Very chatty** — expect bursts.
- **Root id?**: Yes.
- **Mandatory?**: No.
- **Payload**: Same shape as `products/create`.

#### `PRODUCTS_DELETE` / `products/delete`

- **Trigger**: Product is deleted.
- **Root id?**: Yes.
- **Payload**: Minimal — only `id` and `admin_graphql_api_id`.

```json
{
  "id": 788032119674292922,
  "admin_graphql_api_id": "gid://shopify/Product/788032119674292922"
}
```

#### `PRODUCT_FEEDS_UPDATE` / `product_feeds/update`

- **Trigger**: A product feed (used for sales-channel/marketing-channel publication, e.g., Google, Facebook) is updated.
- **Root id?**: No (wrapped in `product_feed`).
- **Use case for SyncApp**: Generally not needed; only relevant if you're syncing per-channel availability rather than raw inventory.

```json
{
  "product_feed": {
    "shop_id": 12345,
    "id": "gid://shopify/ProductFeed/abc123",
    "language": "en",
    "country": "US"
  }
}
```

### 6.4 Inventory items

Inventory items are the granular SKU-level resource Shopify uses to track stock. Every variant has exactly one `InventoryItem` (1:1). Quantities live on `InventoryLevel` (one per item × location).

#### `INVENTORY_ITEMS_CREATE` / `inventory_items/create`

- **Trigger**: A new inventory item is created (e.g., a new variant was added that tracks inventory).
- **Root id?**: **No** — wrapped in `inventory_item`.
- **Payload shape**:

```json
{
  "inventory_item": {
    "id": 39072856,
    "admin_graphql_api_id": "gid://shopify/InventoryItem/39072856",
    "sku": "TSHIRT-001",
    "created_at": "2026-05-24T10:11:12-04:00",
    "updated_at": "2026-05-24T10:11:12-04:00",
    "requires_shipping": true,
    "cost": "5.00",
    "country_code_of_origin": "CA",
    "province_code_of_origin": "ON",
    "harmonized_system_code": "611020",
    "tracked": true,
    "country_harmonized_system_codes": [
      { "country_code": "US", "harmonized_system_code": "6110200000" }
    ]
  }
}
```

#### `INVENTORY_ITEMS_UPDATE` / `inventory_items/update`

- **Trigger**: Inventory-item metadata changes (cost, country of origin, HS code, tracking on/off, SKU edit). Quantity changes do **not** fire this — those fire `inventory_levels/update`.
- **Root id?**: No.
- **Payload**: Same shape as `inventory_items/create`.

#### `INVENTORY_ITEMS_DELETE` / `inventory_items/delete`

- **Trigger**: Inventory item is deleted (usually as a side effect of variant or product deletion).
- **Root id?**: No.
- **Payload** (minimal):

```json
{
  "inventory_item": {
    "id": 39072856,
    "admin_graphql_api_id": "gid://shopify/InventoryItem/39072856",
    "sku": "TSHIRT-001"
  }
}
```

### 6.5 Inventory levels

The **heart of SyncApp's webhook ingest**. Every quantity change at every location fires one of these. Expect ~10–100× the volume of `orders/create`.

#### `INVENTORY_LEVELS_UPDATE` / `inventory_levels/update`

- **Trigger**: An inventory level's `available` quantity changes (or other tracked quantity states like `committed`, `incoming`, `on_hand`, depending on what your shop has enabled).
- **Root id?**: **NO**. This is the canonical "no root id" webhook. Dedup must use `X-Shopify-Webhook-Id`. See [Deduplication Strategy](#5-deduplication-strategy).
- **Protected customer data?**: No.
- **Self-push echo**: When *you* push inventory to Shopify (via `inventorySetQuantities` / `inventoryAdjustQuantities`), Shopify fires this webhook back to you. SyncApp suppresses these via `markAsSelfPush` + `isSelfPushAsync` (Redis-backed, 60s TTL keyed by `item_gid + location_gid + qty`). See [SyncApp-Specific Gotchas](#11-syncapp-specific-gotchas).

**Payload shape:**
```json
{
  "inventory_item_id": 39072856,
  "location_id": 655441491,
  "available": 5,
  "updated_at": "2026-05-24T10:11:12-04:00",
  "admin_graphql_api_id": "gid://shopify/InventoryLevel/12345?inventory_item_id=39072856"
}
```

Some shops with multi-state inventory enabled receive richer payloads (the 2024-10+ shape) with per-state quantities. The classic `available` form is still emitted alongside for backwards compat:

```json
{
  "inventory_item_id": 39072856,
  "location_id": 655441491,
  "available": 5,
  "on_hand": 7,
  "committed": 2,
  "incoming": 0,
  "reserved": 0,
  "damaged": 0,
  "quality_control": 0,
  "safety_stock": 0,
  "updated_at": "2026-05-24T10:11:12-04:00",
  "admin_graphql_api_id": "gid://shopify/InventoryLevel/12345?inventory_item_id=39072856"
}
```

> **Critical for SyncApp**: This webhook fires on **every** quantity change — including the echo of our own writes. Always check self-push suppression in the webhook worker before enqueuing `markDirty(workspaceId, groupId, "inventory_levels/update")`.

#### `INVENTORY_LEVELS_CONNECT` / `inventory_levels/connect`

- **Trigger**: An inventory item is **connected** to a location (initial creation of the per-location stock record).
- **Root id?**: No.
- **Payload**: Same shape as `inventory_levels/update` minus the `available` value (or `available: 0`).

```json
{
  "inventory_item_id": 39072856,
  "location_id": 655441491,
  "available": 0,
  "updated_at": "2026-05-24T10:11:12-04:00"
}
```

#### `INVENTORY_LEVELS_DISCONNECT` / `inventory_levels/disconnect`

- **Trigger**: An inventory item is **disconnected** from a location (the stock record is removed; the SKU is no longer stocked there).
- **Root id?**: No.
- **Payload**: Minimal — `inventory_item_id` and `location_id`.

```json
{
  "inventory_item_id": 39072856,
  "location_id": 655441491
}
```

SyncApp uses these to re-evaluate the per-group location set. A disconnect on a location reduces the central pool's contributor count; a connect adds one.

### 6.6 Variants

#### `VARIANTS_IN_STOCK` / `variants/in_stock`

- **Trigger**: A variant transitions from out-of-stock to in-stock at *any* location.
- **Root id?**: No (wrapped in `variant`).
- **Use case**: Re-enable a variant in your catalog UI, send a back-in-stock notification, etc.

```json
{
  "variant": {
    "id": 39072856,
    "product_id": 788032119674292922,
    "sku": "TSHIRT-001",
    "barcode": "0123456789012",
    "admin_graphql_api_id": "gid://shopify/ProductVariant/39072856"
  }
}
```

#### `VARIANTS_OUT_OF_STOCK` / `variants/out_of_stock`

- **Trigger**: A variant transitions to fully out-of-stock across all locations.
- **Root id?**: No.
- **Payload**: Same shape as `variants/in_stock`.

> Note: These are aggregate-state webhooks. If a variant goes from 5 → 0 → 3, you may receive `out_of_stock` then `in_stock`. They are **not** a replacement for `inventory_levels/update` — they don't tell you which location changed or by how much.

### 6.7 Orders

Orders are the second-highest volume topic (after inventory). All order webhooks have a top-level `id`.

#### `ORDERS_CREATE` / `orders/create`

- **Trigger**: A new order is created. This includes draft order conversion, manual order creation, and customer checkout.
- **Root id?**: **Yes**.
- **Protected customer data?**: **Yes** — payload contains `customer.email`, `billing_address`, `shipping_address`, `customer.phone`. You need Level 1 + Level 2 access if you read those fields.
- **Mandatory?**: No, but required by SyncApp's `aggressive` allocation mode to decrement pool immediately on order.

**Payload shape** (matches REST `Order` resource):
```json
{
  "id": 820982911946154508,
  "admin_graphql_api_id": "gid://shopify/Order/820982911946154508",
  "order_number": 1001,
  "name": "#1001",
  "email": "jon@doe.ca",
  "phone": null,
  "currency": "USD",
  "presentment_currency": "USD",
  "total_price": "109.98",
  "total_price_set": {
    "shop_money": { "amount": "109.98", "currency_code": "USD" },
    "presentment_money": { "amount": "109.98", "currency_code": "USD" }
  },
  "subtotal_price": "99.98",
  "total_tax": "10.00",
  "total_discounts": "0.00",
  "total_line_items_price": "99.98",
  "total_outstanding": "0.00",
  "total_tip_received": "0.00",
  "total_weight": 200,
  "taxes_included": false,
  "financial_status": "paid",
  "fulfillment_status": null,
  "confirmed": true,
  "confirmation_number": "ABCD1234",
  "buyer_accepts_marketing": false,
  "cancel_reason": null,
  "cancelled_at": null,
  "closed_at": null,
  "created_at": "2026-05-24T10:11:12-04:00",
  "updated_at": "2026-05-24T10:11:12-04:00",
  "processed_at": "2026-05-24T10:11:12-04:00",
  "checkout_id": 901414060,
  "checkout_token": "bd5a8aa1ecd019dd3520ff791ee3a24c",
  "cart_token": "68778783ad298f1c80c3bafcddeea02f",
  "client_details": {
    "browser_ip": "0.0.0.0",
    "accept_language": "en-US",
    "user_agent": "Mozilla/...",
    "session_hash": null,
    "browser_height": null,
    "browser_width": null
  },
  "customer": {
    "id": 115310627314723954,
    "email": "jon@doe.ca",
    "phone": null,
    "first_name": "John",
    "last_name": "Smith",
    "state": "disabled",
    "note": null,
    "verified_email": true,
    "multipass_identifier": null,
    "tax_exempt": false,
    "tags": "",
    "currency": "USD",
    "default_address": { "...": "..." },
    "admin_graphql_api_id": "gid://shopify/Customer/115310627314723954"
  },
  "billing_address": {
    "first_name": "Bob",
    "last_name": "Norman",
    "address1": "Chestnut Street 92",
    "address2": null,
    "city": "Louisville",
    "company": null,
    "country": "United States",
    "country_code": "US",
    "phone": "555-625-1199",
    "province": "Kentucky",
    "province_code": "KY",
    "zip": "40202",
    "name": "Bob Norman",
    "latitude": null,
    "longitude": null
  },
  "shipping_address": {
    "first_name": "Bob",
    "last_name": "Norman",
    "address1": "Chestnut Street 92",
    "address2": null,
    "city": "Louisville",
    "company": null,
    "country": "United States",
    "country_code": "US",
    "phone": "555-625-1199",
    "province": "Kentucky",
    "province_code": "KY",
    "zip": "40202",
    "name": "Bob Norman",
    "latitude": null,
    "longitude": null
  },
  "line_items": [
    {
      "id": 466157049,
      "variant_id": 39072856,
      "product_id": 788032119674292922,
      "title": "T-Shirt",
      "name": "T-Shirt - Default Title",
      "variant_title": null,
      "vendor": "Acme",
      "quantity": 1,
      "sku": "TSHIRT-001",
      "price": "19.99",
      "total_discount": "0.00",
      "fulfillment_status": null,
      "fulfillment_service": "manual",
      "fulfillable_quantity": 1,
      "requires_shipping": true,
      "taxable": true,
      "gift_card": false,
      "grams": 200,
      "properties": [],
      "tax_lines": [],
      "discount_allocations": [],
      "duties": [],
      "admin_graphql_api_id": "gid://shopify/LineItem/466157049"
    }
  ],
  "fulfillments": [],
  "refunds": [],
  "shipping_lines": [
    {
      "id": 271878346596884015,
      "title": "Generic Shipping",
      "price": "10.00",
      "code": "Generic Shipping",
      "source": "shopify",
      "phone": null,
      "carrier_identifier": null,
      "requested_fulfillment_service_id": null,
      "delivery_category": null,
      "discount_allocations": [],
      "tax_lines": []
    }
  ],
  "discount_codes": [],
  "discount_applications": [],
  "note_attributes": [],
  "payment_gateway_names": ["bogus"],
  "processing_method": "direct",
  "source_name": "web",
  "source_identifier": null,
  "source_url": null,
  "tags": "",
  "note": null,
  "user_id": null,
  "location_id": null,
  "device_id": null,
  "browser_ip": "0.0.0.0",
  "landing_site": "/",
  "referring_site": "",
  "test": false,
  "tax_lines": [],
  "current_total_price": "109.98",
  "current_subtotal_price": "99.98",
  "current_total_tax": "10.00",
  "current_total_discounts": "0.00",
  "estimated_taxes": false
}
```

> Use `includeFields` in the subscription if you only need a subset — minimizes protected-data exposure and reduces payload size.

#### `ORDERS_UPDATED` / `orders/updated`

- **Trigger**: Any field on an existing order changes (line item edit, tag change, shipping address edit, fulfillment progress, refund, …). **Very chatty.**
- **Root id?**: Yes.
- **Payload**: Same shape as `orders/create`.

> Note: the topic is **`orders/updated`** with a `d` — the enum is `ORDERS_UPDATED`. Misspelling as `ORDERS_UPDATE` is a common bug.

#### `ORDERS_CANCELLED` / `orders/cancelled`

- **Trigger**: Order is cancelled. Includes the `cancel_reason` field (`customer`, `fraud`, `inventory`, `declined`, `other`, `staff`).
- **Root id?**: Yes.
- **Payload**: Same shape as `orders/create` but with `cancelled_at` set and `financial_status` typically transitioning to `voided` or `refunded`.
- **SyncApp gotcha**: If the cancellation includes `restock: true` (visible in `refunds[*].refund_line_items[*].restock_type === "cancel"`), Shopify will return inventory to stock — which fires `inventory_levels/update` webhooks for each affected location. Make sure your cancellation handler doesn't double-count.

#### `ORDERS_FULFILLED` / `orders/fulfilled`

- **Trigger**: All line items in an order are fulfilled (`fulfillment_status: "fulfilled"`).
- **Root id?**: Yes.
- **Payload**: Same shape as `orders/create`.

#### `ORDERS_PARTIALLY_FULFILLED` / `orders/partially_fulfilled`

- **Trigger**: Some line items fulfilled, others not (`fulfillment_status: "partial"`).
- **Root id?**: Yes.
- **Payload**: Same shape as `orders/create`.

#### `ORDERS_PAID` / `orders/paid`

- **Trigger**: Order's `financial_status` transitions to `paid` (full payment received).
- **Root id?**: Yes.
- **Payload**: Same shape as `orders/create`.

### 6.8 Refunds & transactions

#### `REFUNDS_CREATE` / `refunds/create`

- **Trigger**: A refund is created on an order (without errors).
- **Root id?**: **No** — wrapped in `refund`.
- **Payload shape**:

```json
{
  "refund": {
    "id": 929361462,
    "admin_graphql_api_id": "gid://shopify/Refund/929361462",
    "order_id": 820982911946154508,
    "created_at": "2026-05-24T10:11:12-04:00",
    "processed_at": "2026-05-24T10:11:12-04:00",
    "note": "Customer changed mind",
    "user_id": 548380009,
    "restock": true,
    "duties": [],
    "total_duties_set": { "shop_money": {"amount": "0.00", "currency_code": "USD"}, "presentment_money": {"amount": "0.00", "currency_code": "USD"} },
    "refund_line_items": [
      {
        "id": 104689539,
        "quantity": 1,
        "line_item_id": 466157049,
        "location_id": 655441491,
        "restock_type": "return",
        "subtotal": 19.99,
        "total_tax": 0,
        "subtotal_set": { "shop_money": {"amount": "19.99", "currency_code": "USD"}, "presentment_money": {"amount": "19.99", "currency_code": "USD"} },
        "total_tax_set": { "shop_money": {"amount": "0.00", "currency_code": "USD"}, "presentment_money": {"amount": "0.00", "currency_code": "USD"} },
        "line_item": { "id": 466157049, "variant_id": 39072856, "product_id": 788032119674292922, "title": "T-Shirt", "quantity": 1, "sku": "TSHIRT-001", "...": "..." }
      }
    ],
    "transactions": [
      {
        "id": 1068278476,
        "order_id": 820982911946154508,
        "kind": "refund",
        "gateway": "bogus",
        "status": "success",
        "message": null,
        "created_at": "2026-05-24T10:11:12-04:00",
        "amount": "19.99",
        "currency": "USD"
      }
    ],
    "order_adjustments": []
  }
}
```

`restock_type` values: `no_restock`, `cancel`, `return`, `legacy_restock`. When `restock_type` is anything other than `no_restock`, Shopify will adjust inventory and fire `inventory_levels/update` webhooks.

#### `ORDER_TRANSACTIONS_CREATE` / `order_transactions/create`

- **Trigger**: A transaction (authorization, capture, refund, void) is created or its status updated.
- **Root id?**: No — wrapped in `transaction` (or alternative shapes per docs).
- **Payload shape**:

```json
{
  "id": 1068278476,
  "admin_graphql_api_id": "gid://shopify/OrderTransaction/1068278476",
  "order_id": 820982911946154508,
  "kind": "capture",
  "gateway": "bogus",
  "status": "success",
  "message": null,
  "created_at": "2026-05-24T10:11:12-04:00",
  "test": false,
  "authorization": "53433",
  "location_id": null,
  "user_id": null,
  "parent_id": 1068278470,
  "processed_at": "2026-05-24T10:11:12-04:00",
  "device_id": null,
  "error_code": null,
  "source_name": "web",
  "receipt": {},
  "currency_exchange_adjustment": null,
  "amount": "109.98",
  "currency": "USD",
  "payment_id": "c901414060.1",
  "payment_details": {
    "credit_card_bin": null,
    "avs_result_code": null,
    "cvv_result_code": null,
    "credit_card_number": "•••• •••• •••• 4242",
    "credit_card_company": "Visa"
  }
}
```

(Note: this topic's payload was reported in some docs as flat with root `id`; in others as wrapped. The header `X-Shopify-Topic: order_transactions/create` is canonical — verify body shape against the live webhook in your `webhooks.tsx` ingestion logger before assuming.)

### 6.9 Fulfillments

#### `FULFILLMENTS_CREATE` / `fulfillments/create`

- **Trigger**: A fulfillment record is created (typically when a fulfillment service ships items).
- **Root id?**: Technically the documented sample shows `id` at root, but the resource is wrapped in some serializations. Treat as **no reliable root id** for dedup — use `X-Shopify-Webhook-Id`.

```json
{
  "id": 123456,
  "admin_graphql_api_id": "gid://shopify/Fulfillment/123456",
  "order_id": 820982911946154508,
  "status": "pending",
  "created_at": "2021-12-31T19:00:00-05:00",
  "service": null,
  "updated_at": "2021-12-31T19:00:00-05:00",
  "tracking_company": "UPS",
  "shipment_status": null,
  "location_id": null,
  "origin_address": null,
  "email": "jon@example.com",
  "destination": {
    "first_name": "Steve",
    "address1": "123 Shipping Street",
    "phone": "555-555-SHIP",
    "city": "Shippington",
    "zip": "40003",
    "province": "Kentucky",
    "country": "United States",
    "last_name": "Shipper",
    "address2": null,
    "company": "Shipping Company",
    "latitude": null,
    "longitude": null,
    "name": "Steve Shipper",
    "country_code": "US",
    "province_code": "KY"
  },
  "line_items": [
    {
      "id": 487817672276298554,
      "variant_id": null,
      "title": "Aviator sunglasses",
      "quantity": 1,
      "sku": "SKU2006-001",
      "variant_title": null,
      "vendor": null,
      "fulfillment_service": "manual",
      "product_id": 788032119674292922,
      "requires_shipping": true,
      "taxable": true,
      "gift_card": false,
      "name": "Aviator sunglasses",
      "variant_inventory_management": null,
      "properties": [],
      "product_exists": true,
      "fulfillable_quantity": 1,
      "grams": 100,
      "price": "89.99",
      "total_discount": "0.00",
      "fulfillment_status": null,
      "price_set": {
        "shop_money": { "amount": "89.99", "currency_code": "USD" },
        "presentment_money": { "amount": "89.99", "currency_code": "USD" }
      },
      "total_discount_set": {
        "shop_money": { "amount": "0.00", "currency_code": "USD" },
        "presentment_money": { "amount": "0.00", "currency_code": "USD" }
      },
      "discount_allocations": [],
      "duties": [],
      "admin_graphql_api_id": "gid://shopify/LineItem/487817672276298554",
      "tax_lines": []
    }
  ],
  "tracking_number": "1z827wk74630",
  "tracking_numbers": ["1z827wk74630"],
  "tracking_url": "https://www.ups.com/WebTracking?loc=en_US&requester=ST&trackNums=1z827wk74630",
  "tracking_urls": ["https://www.ups.com/WebTracking?loc=en_US&requester=ST&trackNums=1z827wk74630"],
  "receipt": {},
  "name": "#9999.1"
}
```

#### `FULFILLMENTS_UPDATE` / `fulfillments/update`

- **Trigger**: Fulfillment status changes (`pending` → `success`, tracking info updates, `cancelled`, etc.).
- **Root id?**: Same caveat as `fulfillments/create`.
- **Payload**: Same shape as `fulfillments/create`.

`status` values: `pending`, `open`, `success`, `cancelled`, `error`, `failure`.
`shipment_status` values (when non-null): `label_printed`, `label_purchased`, `attempted_delivery`, `ready_for_pickup`, `confirmed`, `in_transit`, `out_for_delivery`, `delivered`, `failure`.

### 6.10 Locations

Location webhooks are low-volume but critical for SyncApp: every location is a sync endpoint, and adding/removing one shifts the allocation graph.

#### `LOCATIONS_CREATE` / `locations/create`

- **Trigger**: A new physical or virtual location is created.
- **Root id?**: No — wrapped in `location`.

```json
{
  "location": {
    "id": 655441491,
    "admin_graphql_api_id": "gid://shopify/Location/655441491",
    "name": "Main Warehouse",
    "address1": "190 MacLaren Street",
    "address2": null,
    "city": "Ottawa",
    "country_code": "CA",
    "country_name": "Canada",
    "province_code": "ON",
    "province": "Ontario",
    "zip": "K2P 0L7",
    "phone": "1-555-1234",
    "country": "Canada",
    "created_at": "2026-05-24T10:11:12-04:00",
    "updated_at": "2026-05-24T10:11:12-04:00",
    "legacy": false,
    "active": true,
    "localized_country_name": "Canada",
    "localized_province_name": "Ontario"
  }
}
```

#### `LOCATIONS_UPDATE` / `locations/update`

- **Trigger**: Location metadata (name, address, phone) changes.
- **Root id?**: No.
- **Payload**: Same shape as `locations/create`.

#### `LOCATIONS_DELETE` / `locations/delete`

- **Trigger**: Location is deleted.
- **Root id?**: No.
- **Payload** (minimal):

```json
{
  "location": {
    "id": 655441491,
    "admin_graphql_api_id": "gid://shopify/Location/655441491",
    "name": "Main Warehouse"
  }
}
```

#### `LOCATIONS_ACTIVATE` / `locations/activate`

- **Trigger**: A previously deactivated location is re-activated.
- **Root id?**: No.
- **Payload**: Same shape as `locations/create`, with `active: true`.

#### `LOCATIONS_DEACTIVATE` / `locations/deactivate`

- **Trigger**: A location is deactivated (soft-removed from operation but kept in history).
- **Root id?**: No.
- **Payload**: Same shape as `locations/create`, with `active: false`.

### 6.11 Bulk operations

#### `BULK_OPERATIONS_FINISH` / `bulk_operations/finish`

- **Trigger**: A bulk-query or bulk-mutation operation initiated via `bulkOperationRunQuery` / `bulkOperationRunMutation` finishes (success, fail, or cancel).
- **Root id?**: No (no wrapper either — flat fields).
- **Payload shape**:

```json
{
  "admin_graphql_api_id": "gid://shopify/BulkOperation/147595010",
  "completed_at": "2024-01-01T07:34:56-05:00",
  "created_at": "2026-03-02T12:16:30-05:00",
  "error_code": null,
  "status": "completed",
  "type": "query"
}
```

`status` values: `completed`, `failed`, `canceled` (note one `l`).
`type` values: `query`, `mutation`.

SyncApp uses bulk operations for initial product/inventory hydration of new shops; the finish webhook signals "go fetch the result URL and ingest it."

---

## 7. Subscription Mutations & Queries

### 7.1 `webhookSubscriptionCreate`

Creates an HTTPS, EventBridge, or Pub/Sub subscription. Replaces the older `eventBridgeWebhookSubscriptionCreate` and `pubSubWebhookSubscriptionCreate` (both still work but are deprecated).

```graphql
mutation webhookSubscriptionCreate(
  $topic: WebhookSubscriptionTopic!
  $webhookSubscription: WebhookSubscriptionInput!
) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription {
      id
      topic
      format
      includeFields
      metafieldNamespaces
      filter
      apiVersion { handle }
      endpoint {
        __typename
        ... on WebhookHttpEndpoint { callbackUrl }
        ... on WebhookEventBridgeEndpoint { arn }
        ... on WebhookPubSubEndpoint { pubSubProject pubSubTopic }
      }
      createdAt
      updatedAt
    }
    userErrors { field message }
  }
}
```

**`WebhookSubscriptionInput` fields:**

| Field | Type | Notes |
|---|---|---|
| `callbackUrl` | URL | HTTPS endpoint. Mutually exclusive with `pubSubProject`/`arn`. |
| `pubSubProject` | String | GCP project id. Paired with `pubSubTopic`. |
| `pubSubTopic` | String | Pub/Sub topic name (without `projects/<id>/topics/` prefix). |
| `format` | `WebhookSubscriptionFormat` | `JSON` (default) or `XML`. |
| `includeFields` | `[String!]` | Restrict payload to these fields. Omit for "all fields". Reduces payload size & protected-data exposure. |
| `metafieldNamespaces` | `[String!]` | Include metafields from these namespaces. |
| `metafields` | `[HasMetafieldsIdentifier!]` | Specific metafields to include (by `namespace` + `key`). |
| `filter` | String | Shopify search-syntax filter (e.g., `"financial_status:paid"` for orders). Limits which events fire the webhook. |

> **Note**: The current API uses a single `WebhookSubscriptionInput`. Older shapes (`uri` field, separate `EventBridgeWebhookSubscriptionInput`, `PubSubWebhookSubscriptionInput`) are deprecated but still accepted.

**Example — create an HTTPS subscription:**

```graphql
mutation {
  webhookSubscriptionCreate(
    topic: INVENTORY_LEVELS_UPDATE,
    webhookSubscription: {
      callbackUrl: "https://app.example.com/webhooks",
      format: JSON,
      includeFields: ["inventory_item_id", "location_id", "available", "updated_at"]
    }
  ) {
    webhookSubscription {
      id
      topic
      endpoint { ... on WebhookHttpEndpoint { callbackUrl } }
    }
    userErrors { field message }
  }
}
```

**Example response:**

```json
{
  "data": {
    "webhookSubscriptionCreate": {
      "webhookSubscription": {
        "id": "gid://shopify/WebhookSubscription/8589934632",
        "topic": "INVENTORY_LEVELS_UPDATE",
        "endpoint": { "callbackUrl": "https://app.example.com/webhooks" }
      },
      "userErrors": []
    }
  }
}
```

**Common `userErrors`:**

| `field` | `message` | Cause |
|---|---|---|
| `["webhookSubscription", "callbackUrl"]` | "Address has already been taken" | A subscription for this topic+URL already exists for this shop. |
| `["topic"]` | "Topic is invalid" | Topic enum value misspelled or not supported in this API version. |
| `["webhookSubscription", "callbackUrl"]` | "Callback url is invalid" | Non-HTTPS, malformed URL, or local IP. |
| `["webhookSubscription", "format"]` | "Format is invalid" | Must be `JSON` or `XML`. |

### 7.2 `webhookSubscriptionUpdate`

Updates an existing subscription's URI, format, filter, includeFields, or metafield namespaces. Topic is **immutable** — to change topic, delete and re-create.

```graphql
mutation webhookSubscriptionUpdate(
  $id: ID!
  $webhookSubscription: WebhookSubscriptionInput!
) {
  webhookSubscriptionUpdate(id: $id, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic endpoint { __typename } updatedAt }
    userErrors { field message }
  }
}
```

Updates are atomic — Shopify never drops events during the change.

### 7.3 `webhookSubscriptionDelete`

```graphql
mutation webhookSubscriptionDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
```

Returns the deleted id on success. After deletion, in-flight retries for past events still complete (or fail without retry, depending on Shopify-side timing).

### 7.4 `webhookSubscriptions` (query)

Lists API-created subscriptions for the current app + shop. **Does not return subscriptions declared in `shopify.app.toml`** — those are "config-managed" and invisible to this query.

```graphql
query {
  webhookSubscriptions(first: 50, topics: [INVENTORY_LEVELS_UPDATE, ORDERS_CREATE]) {
    edges {
      node {
        id
        topic
        format
        endpoint {
          __typename
          ... on WebhookHttpEndpoint { callbackUrl }
        }
        createdAt
        updatedAt
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

**Arguments:**

| Argument | Type | Notes |
|---|---|---|
| `first` / `last` | Int | Pagination. |
| `after` / `before` | String | Cursor. |
| `topics` | `[WebhookSubscriptionTopic!]` | Filter by topic(s). |
| `format` | `WebhookSubscriptionFormat` | Filter by format. |
| `callbackUrl` | URL | Filter by HTTPS endpoint (deprecated; use `uri`). |

### 7.5 `webhookSubscription` (singular query)

```graphql
query {
  webhookSubscription(id: "gid://shopify/WebhookSubscription/8589934632") {
    id
    topic
    endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } }
  }
}
```

### 7.6 Deprecated transport-specific creates

These still work but the recommended path is `webhookSubscriptionCreate` with the right input field:

| Deprecated mutation | Use instead |
|---|---|
| `eventBridgeWebhookSubscriptionCreate(topic, webhookSubscription: { arn, format, includeFields, ... })` | `webhookSubscriptionCreate` with `EventBridgeWebhookSubscriptionInput` shape, OR pass `arn` via `WebhookSubscriptionInput`. |
| `pubSubWebhookSubscriptionCreate(topic, webhookSubscription: { pubSubProject, pubSubTopic, format, ... })` | `webhookSubscriptionCreate` with `pubSubProject` / `pubSubTopic` in `WebhookSubscriptionInput`. |
| `eventBridgeWebhookSubscriptionUpdate` | `webhookSubscriptionUpdate`. |
| `pubSubWebhookSubscriptionUpdate` | `webhookSubscriptionUpdate`. |

### 7.7 `WebhookSubscriptionTopic` enum (key values)

The complete list is large (~200 topics). Topics relevant to SyncApp:

```
APP_UNINSTALLED
APP_SUBSCRIPTIONS_UPDATE
APP_SCOPES_UPDATE
PRODUCTS_CREATE
PRODUCTS_UPDATE
PRODUCTS_DELETE
PRODUCT_FEEDS_UPDATE
PRODUCT_LISTINGS_ADD
PRODUCT_LISTINGS_REMOVE
PRODUCT_LISTINGS_UPDATE
INVENTORY_ITEMS_CREATE
INVENTORY_ITEMS_UPDATE
INVENTORY_ITEMS_DELETE
INVENTORY_LEVELS_CONNECT
INVENTORY_LEVELS_DISCONNECT
INVENTORY_LEVELS_UPDATE
ORDERS_CREATE
ORDERS_UPDATED                # note: UPDATED with D, not UPDATE
ORDERS_CANCELLED
ORDERS_FULFILLED
ORDERS_PARTIALLY_FULFILLED
ORDERS_PAID
ORDERS_DELETE
ORDERS_EDITED
REFUNDS_CREATE
ORDER_TRANSACTIONS_CREATE
FULFILLMENTS_CREATE
FULFILLMENTS_UPDATE
FULFILLMENT_EVENTS_CREATE
FULFILLMENT_EVENTS_DELETE
FULFILLMENT_HOLDS_ADDED
FULFILLMENT_HOLDS_RELEASED
FULFILLMENT_ORDERS_CANCELLATION_REQUEST_ACCEPTED
FULFILLMENT_ORDERS_CANCELLATION_REQUEST_REJECTED
FULFILLMENT_ORDERS_CANCELLATION_REQUEST_SUBMITTED
FULFILLMENT_ORDERS_CANCELLED
FULFILLMENT_ORDERS_FULFILLMENT_REQUEST_ACCEPTED
FULFILLMENT_ORDERS_FULFILLMENT_REQUEST_REJECTED
FULFILLMENT_ORDERS_FULFILLMENT_REQUEST_SUBMITTED
FULFILLMENT_ORDERS_HOLD_RELEASED
FULFILLMENT_ORDERS_LINE_ITEMS_PREPARED_FOR_LOCAL_DELIVERY
FULFILLMENT_ORDERS_LINE_ITEMS_PREPARED_FOR_PICKUP
FULFILLMENT_ORDERS_MOVED
FULFILLMENT_ORDERS_PLACED_ON_HOLD
FULFILLMENT_ORDERS_RESCHEDULED
LOCATIONS_CREATE
LOCATIONS_UPDATE
LOCATIONS_DELETE
LOCATIONS_DEACTIVATE
LOCATIONS_ACTIVATE
VARIANTS_IN_STOCK
VARIANTS_OUT_OF_STOCK
BULK_OPERATIONS_FINISH
SHOP_REDACT                   # compliance (config-only)
CUSTOMERS_REDACT              # compliance (config-only)
CUSTOMERS_DATA_REQUEST        # compliance (config-only)
COLLECTIONS_CREATE
COLLECTIONS_UPDATE
COLLECTIONS_DELETE
```

Use `__type(name: "WebhookSubscriptionTopic") { enumValues { name description } }` to get the complete authoritative list for the current API version.

---

## 8. Compliance Topics (GDPR / Privacy)

The three compliance webhooks (`SHOP_REDACT`, `CUSTOMERS_REDACT`, `CUSTOMERS_DATA_REQUEST`) are **mandatory for every public app** sold on the Shopify App Store. App Review will reject submissions without them.

### Key rules

1. **Declare in `shopify.app.toml` only, not via API.** Compliance topics use the special `compliance_topics` key in a subscription block; the regular `topics` key won't validate them.
2. **Receive only what your scopes touch.** If your app has no customer-data scopes, you'll still get `shop/redact` but not `customers/redact` or `customers/data_request`.
3. **Respond 200 within 5 seconds.** Like any webhook.
4. **Complete the work within 30 days** (industry-standard GDPR SLA).
5. **HMAC verification still required.** Return 401 on verification failure.

### Config example

```toml
[webhooks]
api_version = "2026-04"

[[webhooks.subscriptions]]
compliance_topics = ["customers/data_request", "customers/redact", "shop/redact"]
uri = "https://app.example.com/webhooks/compliance"
```

You can route all three to one endpoint (recommended; cheaper to operate) or split them. Use `X-Shopify-Topic` to dispatch.

### Timing reference

| Topic | When it fires |
|---|---|
| `shop/redact` | 48 hours after the shop uninstalls your app. |
| `customers/redact` | If customer has no orders in 6 months → 10 days after merchant initiates the redact request. Otherwise → withheld until 6 months after the customer's last order. |
| `customers/data_request` | Immediately after a merchant requests the customer's data. |

### What to do for each

**`shop/redact`** — delete everything tied to that shop:
- Shop record, access tokens (already encrypted; still delete).
- Variants, inventory snapshots, sync history.
- Cached order line items, even anonymized.
- Job queue entries pending for that shop (BullMQ: `await queue.removeJobs(\`${shopId}:*\`)`).
- Audit logs may be retained per your retention policy; document this in your privacy policy.

**`customers/redact`** — for the specific `customer.id`:
- Delete or anonymize all stored references (orders cached in your DB, line items mentioning their email/address).
- Don't touch other customers' data.

**`customers/data_request`** — gather and email the merchant:
- Every record keyed by `customer.id`, `customer.email`, `customer.phone`, or any of the `orders_requested` ids.
- Format as JSON or structured document.
- Email to the merchant (use the shop owner's email from their auth session; do **not** email the customer directly).

---

## 9. Protected Customer Data

Shopify gates access to PII fields behind a **two-tier approval system** managed in your Partner Dashboard.

### The two tiers

| Level | Covers | Approval |
|---|---|---|
| **Level 1** | All customer/order data **except** name, address, email, phone. Examples: customer id, accepts-marketing flag, order totals, line items, financial status. | Request access in Partner Dashboard. Public apps: app review required. Custom apps: automatic. |
| **Level 2** | Name, address (line1/line2/city/zip/province/country), email, phone, IP. | Request access **+** complete a data-protection self-assessment. Public apps: data-protection review required. Custom apps: depends on plan. |

### Protected fields (exact list)

The fields Shopify treats as "protected customer data":

- **Name**: `first_name`, `last_name`, `name` (full name).
- **Address**: `address1`, `address2`, `city`, `province`, `province_code`, `country`, `country_code`, `zip`, `latitude`, `longitude`. Applies to `customer.default_address`, `billing_address`, `shipping_address`, and any nested address.
- **Email**: `email`, `customer_email`, `contact_email`.
- **Phone**: `phone`.
- **Network**: `browser_ip`, `client_details.browser_ip`.

If your app reads any of these via REST/GraphQL/webhook payloads without approval, the fields come back as `null` and Shopify emits a warning in the merchant's admin pointing at your app's API access settings.

### How to request access

1. Partner Dashboard → Apps → your app → **API access**.
2. Under "Protected customer data", click **Request access**.
3. List the specific fields you need and the business justification.
4. For Level 2, complete the data-protection details (encryption, retention, staff access, incident response).
5. Submit. Public apps go to Shopify review.

### Data minimization

Even with approval, Shopify requires:

- **Minimization**: process only what you need.
- **Retention**: define and document a retention period; auto-delete after.
- **Encryption**: at rest and in transit.
- **Access control**: log who accesses customer data internally.
- **Incident response**: written process for breaches.

### Webhook implication

Use `includeFields` on your subscriptions to **avoid receiving** protected fields you don't need. For SyncApp, the inventory webhooks (`inventory_levels/update`, `inventory_items/update`) contain no customer data — no Level 2 needed. But `orders/create` includes the full customer object. If SyncApp only needs `line_items` + `id` + `cancel_reason` from orders, declare:

```toml
[[webhooks.subscriptions]]
topics = ["orders/create", "orders/cancelled"]
uri = "https://app.example.com/webhooks"
include_fields = ["id", "admin_graphql_api_id", "cancelled_at", "cancel_reason", "financial_status", "fulfillment_status", "line_items", "updated_at"]
```

This both reduces payload size and means SyncApp can skip Level 2 approval.

---

## 10. App Config (`shopify.app.toml`)

### Where it lives

The `[webhooks]` table at the top level of `shopify.app.toml`. Required CLI version: **3.63.0+**.

### Basic structure

```toml
[webhooks]
api_version = "2026-04"

# Standard subscriptions
[[webhooks.subscriptions]]
topics = ["products/create", "products/update", "products/delete"]
uri = "https://app.example.com/webhooks"

[[webhooks.subscriptions]]
topics = ["inventory_levels/update", "inventory_levels/connect", "inventory_levels/disconnect"]
uri = "https://app.example.com/webhooks"
include_fields = ["inventory_item_id", "location_id", "available", "updated_at", "admin_graphql_api_id"]

# Compliance topics — special key
[[webhooks.subscriptions]]
compliance_topics = ["customers/data_request", "customers/redact", "shop/redact"]
uri = "https://app.example.com/webhooks/compliance"
```

### Per-subscription fields

| Key | Type | Notes |
|---|---|---|
| `topics` | `[string]` | Standard topic names in snake_case slash format. Mutually exclusive with `compliance_topics`. |
| `compliance_topics` | `[string]` | One or more of `customers/data_request`, `customers/redact`, `shop/redact`. Mutually exclusive with `topics`. |
| `uri` | string | HTTPS URL, EventBridge ARN, or `pubsub://project:topic`. Required. |
| `include_fields` | `[string]` | Restrict payload to these fields. Recommended for protected-data minimization. |
| `filter` | string | Shopify search-syntax filter. Limits which events fire. Example: `filter = "financial_status:paid"` on `orders/updated`. |
| `metafield_namespaces` | `[string]` | Include metafields from these namespaces. |
| `name` | string | Human-readable identifier. Alphanumeric, `-`, `_`. Max 50 chars. Useful for debugging in Partner Dashboard. |

### Config-managed vs API-created

When to use which:

| Use **config (`shopify.app.toml`)** for: | Use **API (`webhookSubscriptionCreate`)** for: |
|---|---|
| Compliance topics (required). | Per-shop subscriptions (different URLs per tenant). |
| Topics where every shop subscribes identically. | Plan-gated topics (e.g., only Business+ shops subscribe to `orders/edited`). |
| Topics with stable `include_fields` / `filter`. | Topics whose filter depends on shop-specific state. |
| Anything you want auto-cleaned on uninstall. | Subscriptions a user explicitly enables in your app's settings UI. |

Config-managed subscriptions are **invisible to `webhookSubscriptions` query** — only API-created ones show up. To audit config-managed: deploy → check Partner Dashboard → Webhooks tab.

### Migrating between config and API

If you have an API-created subscription for `orders/create` and you want to move it to `shopify.app.toml`:

1. Add the config block.
2. Delete the API subscription (`webhookSubscriptionDelete`) **before** the next `shopify app deploy` — otherwise you get duplicate deliveries.
3. Deploy. The config subscription provisions fresh.

The reverse (config → API) is rarer; pulling the topic out of config will deprovision on next deploy. Create the API subscription **after** deploy completes.

### `api_version` matters

The `api_version` at `[webhooks]` controls the **serialization version** of all webhook bodies. Your app's GraphQL API version (separate `[access_scopes]` / app-level setting) controls the version used for `admin.graphql(...)` calls. Keep them aligned to avoid field-name drift between what you receive and what you query.

---

## 11. SyncApp-Specific Gotchas

These come straight from the codebase's history and `PRODUCTION-AUDIT.md`. Cross-reference with [`app/routes/webhooks.tsx`](../../../Desktop/SyncApp/app/routes/webhooks.tsx) and [`app/workers/`](../../../Desktop/SyncApp/app/workers/) when touching webhook code.

### a) `INVENTORY_LEVELS_UPDATE` has no root id — dedup MUST use `X-Shopify-Webhook-Id`

The webhook payload is `{ "inventory_item_id": …, "location_id": …, "available": … }`. A naive `payload?.id || Date.now()` falls back to a fresh timestamp on every retry, so dedup silently breaks.

`webhooks.tsx` builds a composite `eventKey` per-topic and uses **that** as the BullMQ `jobId`. The canonical key is the `X-Shopify-Webhook-Id` header. Never trust the body shape.

### b) Self-push echo — 60s Redis TTL keyed by `item+location+qty`

Every `inventorySetQuantities` mutation SyncApp makes triggers Shopify to fire an `inventory_levels/update` webhook back to us. Without suppression we'd allocate-against-our-own-allocation in a loop.

The fix lives in [`app/lib/sync-origin.server.ts`](../../../Desktop/SyncApp/app/lib/sync-origin.server.ts):

- **Before** the GraphQL mutation: `markAsSelfPush(itemGid, locationGid, qty)` — writes a key in Redis with 60s TTL.
- **In** the webhook worker: `isSelfPushAsync(itemGid, locationGid, qty)` — if matched, drop the event silently.

Suppression state lives in Redis because the web process (which writes to Shopify) and the worker process (which receives webhooks) don't share memory.

Important: if SyncApp writes the **same** quantity as a coincidental merchant edit at the same time, suppression will swallow the merchant edit. The 60s TTL is a trade-off — short enough to avoid colliding with normal merchant cadence, long enough to cover Shopify's webhook delivery latency. Don't widen it.

### c) `orders/cancelled` with `restock=true` fires inventory webhooks too

When a merchant cancels an order and chooses "restock items", Shopify returns the items to inventory. That triggers `inventory_levels/update` for each line-item × location.

If SyncApp's cancellation handler **also** tries to restock (e.g., to keep its own ledger in sync), you'll double-count. Currently the worker does **not** restock — it lets Shopify do it and ingests the resulting `inventory_levels/update`. Don't add manual restock logic.

### d) Webhook dedup key is built in one place

`webhooks.tsx` lines 52–59 build the dedup key:

```ts
const eventKey = `${topic}:${webhookId}`;  // canonical
```

This MUST be the same as the BullMQ `jobId` you pass to `queue.add(...)`. If you change one, change the other.

### e) Auto-removal recovery

If a subscription gets auto-removed after 48h of failures (e.g., during a long deploy outage), SyncApp must re-create. The `reconciliation` worker should periodically (e.g., daily) call `webhookSubscriptions` for each connected shop and diff against the expected set; any missing topic gets `webhookSubscriptionCreate`'d.

### f) Mandatory compliance scope vs. SyncApp's actual data

SyncApp does not store `customer.email` / `customer.phone` / addresses. The `customers/redact` and `customers/data_request` handlers are nearly no-ops:

- `customers/redact`: scrub any cached order line items where `customer.id` matches, but typically no records.
- `customers/data_request`: respond 200, email the merchant "no data stored about this customer".

`shop/redact` is real work: cascade-delete the shop and all its dependent records.

All three handlers must:

1. Verify HMAC (return 401 on failure).
2. Return 200 within 5s (queue the actual work).
3. Complete within 30 days.

### g) `webhooks.tsx` vs declarative config

SyncApp currently does both: `[[webhooks.subscriptions]]` blocks in `shopify.app.toml` AND `afterAuth` hook registration. Don't subscribe to the same `topic + uri` in both places — Shopify will reject or you'll get duplicates. Audit on every release.

### h) `X-Shopify-Triggered-At` is your last-write-wins clock

For reconciliation: when comparing a webhook-driven state change vs a poll-driven one, use `X-Shopify-Triggered-At` (or the body's `updated_at`), not your local timestamp. The webhook may arrive late after a retry; the trigger time is the source of truth.

---

## Quick Reference: Has Root `id`?

| Topic | Root `id`? | Dedup strategy |
|---|---|---|
| `app/uninstalled` | ✅ Yes | `X-Shopify-Webhook-Id` (canonical). Payload `id` works as secondary. |
| `app_subscriptions/update` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `shop/redact` | ❌ No | `X-Shopify-Webhook-Id` |
| `customers/redact` | ❌ No | `X-Shopify-Webhook-Id` |
| `customers/data_request` | ❌ No | `X-Shopify-Webhook-Id` |
| `products/create` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `products/update` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `products/delete` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `product_feeds/update` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `inventory_items/create` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `inventory_items/update` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `inventory_items/delete` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `inventory_levels/connect` | ❌ **No** | `X-Shopify-Webhook-Id` |
| `inventory_levels/disconnect` | ❌ **No** | `X-Shopify-Webhook-Id` |
| `inventory_levels/update` | ❌ **No** | `X-Shopify-Webhook-Id` ← **critical** |
| `orders/create` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `orders/updated` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `orders/cancelled` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `orders/fulfilled` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `orders/partially_fulfilled` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `orders/paid` | ✅ Yes | `X-Shopify-Webhook-Id` |
| `refunds/create` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `order_transactions/create` | shape-dependent | `X-Shopify-Webhook-Id` |
| `fulfillments/create` | ✅ Yes (flat) | `X-Shopify-Webhook-Id` |
| `fulfillments/update` | ✅ Yes (flat) | `X-Shopify-Webhook-Id` |
| `locations/create` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `locations/update` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `locations/delete` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `locations/activate` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `locations/deactivate` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `variants/in_stock` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `variants/out_of_stock` | ❌ No (wrapped) | `X-Shopify-Webhook-Id` |
| `bulk_operations/finish` | ❌ No (flat) | `X-Shopify-Webhook-Id` |

**Rule of thumb**: always dedup on `X-Shopify-Webhook-Id`. Treat the payload `id` (when present) as a *resource* identifier, not an *event* identifier.

---

## Source list (fetched 2026-05-24)

- `https://shopify.dev/docs/api/webhooks?reference=graphql`
- `https://shopify.dev/docs/api/admin-graphql/latest/enums/WebhookSubscriptionTopic`
- `https://shopify.dev/docs/api/admin-graphql/latest/objects/WebhookSubscription`
- `https://shopify.dev/docs/api/admin-graphql/latest/queries/webhookSubscriptions`
- `https://shopify.dev/docs/api/admin-graphql/latest/mutations/webhookSubscriptionCreate`
- `https://shopify.dev/docs/api/admin-graphql/latest/mutations/webhookSubscriptionUpdate`
- `https://shopify.dev/docs/api/admin-graphql/latest/mutations/webhookSubscriptionDelete`
- `https://shopify.dev/docs/api/admin-graphql/latest/mutations/eventBridgeWebhookSubscriptionCreate` (deprecated)
- `https://shopify.dev/docs/api/admin-graphql/latest/mutations/pubSubWebhookSubscriptionCreate` (deprecated)
- `https://shopify.dev/docs/apps/build/webhooks/configuration/https`
- `https://shopify.dev/docs/apps/build/webhooks/subscribe`
- `https://shopify.dev/docs/apps/build/privacy-law-compliance` (compliance webhooks)
- `https://shopify.dev/docs/apps/launch/protected-customer-data`
- `https://shopify.dev/docs/api/admin-rest/latest/resources/webhook` (per-topic payload shapes)
- `https://shopify.dev/docs/api/webhooks/latest.md` (raw payload examples)
