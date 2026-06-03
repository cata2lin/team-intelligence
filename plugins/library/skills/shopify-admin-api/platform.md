# Shopify Admin GraphQL API — Platform Plumbing Reference

**Scope:** Bulk Operations, Rate Limits, Authentication, Errors, `@idempotent` directive, Pagination, Metafields, Files, API versioning, headers, release notes.
**API version pinned:** `2026-04` (April 2026 release — current "latest").
**Sources:** Live Shopify docs fetched 2026-05-24, cross-checked with SyncApp's in-tree [docs/SHOPIFY-API-REFERENCE.md](file:///C:/Users/Admin/Desktop/SyncApp/docs/SHOPIFY-API-REFERENCE.md) (last live-fetch 2026-05-19).

This document covers the platform-level mechanics that every Shopify Admin GraphQL caller must understand. For domain mutations (inventory, orders, products, webhooks), see SyncApp's repo-local reference.

---

## Table of contents

1. [API version model](#1-api-version-model)
2. [Authentication](#2-authentication)
3. [Headers](#3-headers)
4. [Rate limits](#4-rate-limits)
5. [Pagination](#5-pagination)
6. [Bulk operations](#6-bulk-operations)
7. [The `@idempotent` directive](#7-the-idempotent-directive)
8. [Error handling](#8-error-handling)
9. [Metafields](#9-metafields)
10. [Files](#10-files)
11. [Release notes / breaking changes](#11-release-notes--breaking-changes)
12. [SyncApp angles — applied usage](#12-syncapp-angles--applied-usage)

---

## 1. API version model

### Current state

- **Latest stable:** `2026-04` (April 2026 release).
- **Release candidate:** `2026-07` (becomes stable July 1, 2026).
- **Unstable:** rolling — experimental features, no stability guarantee.
- **Supported stable versions as of 2026-05:** `2026-04`, `2026-01`, `2025-10`, `2025-07`.

### Release cadence

- **Quarterly:** new stable version on the first of January / April / July / October at **5pm UTC**.
- Version names are date-coded `YYYY-MM` matching the release month.
- Release candidates are published alongside their stable predecessor and may contain backwards-incompatible changes before promotion.

### Support window

- Each stable version is supported for **a minimum of 12 months**.
- At least **9 months of overlap** between consecutive stable versions — meaning you have ~9 months to migrate to N+1 before N drops out of support.
- Deprecated fields are announced in the developer changelog and removed in a future version.
- Continuing to use unsupported versions after the deadline risks app delisting from the App Store and installation blocks.

### Specifying the version

The version is in the URL, not a header:

```
POST https://{shop}.myshopify.com/admin/api/2026-04/graphql.json
```

If you target a version that's no longer supported, Shopify "falls forward" to the oldest accessible stable version — silently. Don't rely on URL parsing; check the response header.

### `X-Shopify-API-Version` response header

Every response includes:

```
X-Shopify-API-Version: 2026-04
```

This is **the only authoritative signal** of which version actually executed the request. Log it. If it differs from the URL you sent, you're on a fallback version.

### Migration workflow

1. Read the release notes for the target version (see [§11](#11-release-notes--breaking-changes)).
2. Switch one canary deployment to the new version URL.
3. Run integration tests and compare responses against the prior version.
4. Watch the `X-Shopify-API-Version` header to confirm the new version is actually serving traffic.
5. Update the pinned version in code (`shopify.app.toml`, server config, all mutation strings).
6. Bump the API library / SDK to the matching release.

### Deprecation lifecycle

- A field is marked deprecated in version N — still works.
- It continues to work in versions N+1, N+2, N+3 (the 12-month support window).
- It's removed in version N+4 (the version released ~12 months after deprecation announcement).
- During the entire deprecation window, queries selecting the field still return data but you'll see `deprecationReason` in the schema and (often) a warning in the response `extensions`.

---

## 2. Authentication

Shopify Admin GraphQL uses **OAuth 2.0** with two grant types:

| Grant type | When to use |
|---|---|
| **Token exchange** (`urn:ietf:params:oauth:grant-type:token-exchange`) | Recommended for apps rendered inside Shopify admin (embedded apps). Converts a session token from App Bridge into an access token without redirect. |
| **Authorization code grant** | Standalone apps or apps not embedded in Shopify admin. Classic redirect flow. Considered "degraded UX" for embedded apps. |
| **Client credentials grant** | Internal-only / system-to-system flows where no user interaction is needed. Rare for storefront apps. |

### Token exchange flow (preferred)

**Endpoint:**

```
POST https://{shop}.myshopify.com/admin/oauth/access_token
```

**Request body (form-encoded):**

| Param | Required | Description |
|---|---|---|
| `client_id` | yes | App API key |
| `client_secret` | yes | App secret |
| `grant_type` | yes | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `subject_token` | yes | The session token from App Bridge (a JWT representing the merchant's session) |
| `subject_token_type` | yes | `urn:ietf:params:oauth:token-type:id_token` |
| `requested_token_type` | no | `urn:shopify:params:oauth:token-type:online-access-token` OR `urn:shopify:params:oauth:token-type:offline-access-token` (default = offline) |
| `expiring` | no (offline only) | `1` for expiring offline tokens, `0` for non-expiring (default = `0`, non-expiring; this is being phased out — see deprecation note below) |

### Online vs offline access tokens

|  | Online | Offline (non-expiring) | Offline (expiring — Dec 2025+) |
|---|---|---|---|
| **Use case** | User-initiated requests in the admin UI; respect per-user permissions | Service-to-service: webhook handlers, scheduled jobs, background sync | Same use case as non-expiring offline, but with refresh tokens |
| **Lifetime** | **24 hours** OR until the user logs out (whichever first) | Forever, until app uninstall or secret rotation | Access token: **1 hour**. Refresh token: **90 days**. |
| **User context** | Has `associated_user` field | None — represents the app, not a user | None |
| **Header** | `X-Shopify-Access-Token: <token>` | Same | Same |
| **Token prefix** | Various; `shpat_...` typical | `shpat_...` | `shpat_...` (refresh token: `shprt_...`) |
| **Migration deadline** | n/a | **Public apps must migrate to expiring offline tokens by January 1, 2027** | Recommended for new apps |

### Sample token-exchange responses

**Online:**

```json
{
  "access_token": "shpat_...",
  "scope": "write_orders,read_customers",
  "expires_in": 86399,
  "associated_user_scope": "write_orders,read_customers",
  "associated_user": {
    "id": 902541635,
    "email": "john@example.com",
    "email_verified": true,
    "first_name": "John",
    "last_name": "Smith",
    "account_owner": true,
    "locale": "en"
  }
}
```

**Offline (expiring):**

```json
{
  "access_token": "shpat_...",
  "scope": "write_orders,read_customers",
  "expires_in": 3600,
  "refresh_token": "shprt_...",
  "refresh_token_expires_in": 7776000
}
```

**Offline (non-expiring, legacy):**

```json
{
  "access_token": "shpat_...",
  "scope": "write_orders,read_customers"
}
```

### Refreshing an expiring offline token

When `access_token` expires, POST to the same `/admin/oauth/access_token` endpoint with:

- `grant_type=refresh_token`
- `client_id`, `client_secret`
- `refresh_token` (from the prior exchange)

The original non-expiring token is **revoked on first successful exchange** to expiring tokens. Don't keep both.

### Token format prefixes

Observed in the wild and in Shopify docs:

| Prefix | Meaning |
|---|---|
| `shpat_` | Shopify Partner Access Token (the standard access token returned by OAuth) |
| `shprt_` | Shopify Refresh Token (returned with expiring offline tokens) |
| `shpss_` | Shopify Storefront Secret (Storefront API private access token) |
| `shpca_` | Shopify Customer Access (Customer Account API token) |
| `shppa_` | Shopify Private App (legacy custom apps) |

Treat the format as opaque — Shopify reserves the right to change prefixes. Don't pattern-match.

### Access scopes — categories

Three buckets:

#### 1. Authenticated access scopes (Admin GraphQL API)

For acting on a store's behalf. Format: `read_X` / `write_X`. `write_` implies `read_`.

Common ones SyncApp uses:

- `read_products`, `write_products`
- `read_inventory`, `write_inventory`
- `read_locations` (no `write_` — locations are admin-managed)
- `read_orders`, `write_orders`
- `read_customers`, `write_customers`
- `read_fulfillments`, `write_fulfillments`
- `read_files`, `write_files`
- `read_themes`, `write_themes`
- `read_images`, `write_images`

Some scopes require an explicit approval from Shopify (Partner Dashboard):

- `read_all_orders` — orders older than 60 days
- `read_customer_payment_methods`
- Subscription-related scopes
- **Protected Customer Data** declaration for webhook topics containing customer PII (`ORDERS_UPDATED`, `ORDERS_CANCELLED`, `REFUNDS_CREATE`)

#### 2. Unauthenticated access scopes (Storefront API)

Read-only, customer-facing. Examples:
- `unauthenticated_read_product_listings`
- `unauthenticated_read_checkouts`
- `unauthenticated_write_customers`
- `unauthenticated_read_product_inventory`

#### 3. Customer access scopes (Customer Account API)

Customer-facing, scoped to the logged-in customer.
- `customer_read_orders`, `customer_write_orders`
- `customer_read_customers`, `customer_write_customers`
- `customer_read_own_subscription_contracts`

### Custom apps vs public apps

|  | Custom apps | Public apps |
|---|---|---|
| Distribution | Single named shop (or a Plus org's stores) | Shopify App Store / unlisted distribution link |
| Install method | Direct install link from Partner Dashboard | OAuth flow |
| Access token type | Offline (non-expiring), per-shop | Offline (expiring required by Jan 2027) or online |
| Review required | No | Yes for App Store listing |
| Use case | Internal merchant tools, single-org SaaS | Multi-tenant SaaS |

SyncApp is structured as a **public app** (multi-tenant) with the `shopify.app.toml` declaring it as such.

---

## 3. Headers

### Request headers (you send)

| Header | Purpose | Notes |
|---|---|---|
| `X-Shopify-Access-Token: <token>` | Authenticates the request as an installed app. **Required on every Admin API request.** | Use offline token for server-to-server; online for per-user. |
| `X-Shopify-Storefront-Access-Token: <token>` | Storefront API only — public token for client-side. | Public; safe to embed. |
| `Shopify-Storefront-Private-Token: <token>` | Storefront API private token for server-to-server. | Use with `Shopify-Storefront-Buyer-IP` to preserve bot protection. |
| `Shopify-Storefront-Buyer-IP: <ip>` | When using private storefront tokens for buyer-facing traffic. | Required for correct rate-limiting / bot protection. |
| `Content-Type: application/json` | All GraphQL request bodies. | Always required. |
| `X-Shopify-API-Version: <version>` | **DEPRECATED.** Version goes in URL now. | Sending it has no effect. |
| `Idempotency-Key: <uuid>` | REST API idempotency (legacy). | GraphQL uses the `@idempotent` directive instead — see [§7](#7-the-idempotent-directive). |
| `X-Shopify-Bulk-Operation-Id: <gid>` | Returned by Shopify on bulk JSONL downloads; not a request header you set. | Don't set this; the `url` in the BulkOperation response is signed. |

### Webhook headers (Shopify sends to you)

For completeness — outside this doc's scope but worth noting:

| Header | Purpose |
|---|---|
| `X-Shopify-Hmac-Sha256` | HMAC signature for webhook verification — MUST validate against `SHOPIFY_API_SECRET`. |
| `X-Shopify-Topic` | The webhook topic, e.g. `inventory_levels/update`. |
| `X-Shopify-Shop-Domain` | The shop that fired the webhook. |
| `X-Shopify-Webhook-Id` | Unique ID per webhook delivery — use for dedup. |
| `X-Shopify-Triggered-At` | ISO 8601 timestamp when the event fired. |
| `X-Shopify-API-Version` | The version that produced the webhook payload. |

### Response headers (Shopify returns)

| Header | Meaning |
|---|---|
| `X-Shopify-API-Version` | The version that actually served the request. **Log this — fallbacks are silent.** |
| `X-Request-ID` | Trace ID — include in support tickets. |
| `Retry-After` | Seconds to wait before retrying. Set on `429 Too Many Requests`. |

---

## 4. Rate limits

GraphQL Admin API uses a **calculated-cost / leaky-bucket** model — not request-count throttling.

### The leaky bucket

Each app × shop has a bucket of points. Every query consumes points equal to its computed cost. Points are restored continuously at a fixed rate. When the bucket is empty, requests are throttled with HTTP `429` and `extensions.code: THROTTLED`.

### Bucket size and restore rate by plan

| Plan | Bucket size (max points) | Restore rate |
|---|---|---|
| **Standard Shopify** | 1,000 | 50 pts/sec |
| **Advanced Shopify** | 2,000 | 100 pts/sec |
| **Shopify Plus** | 10,000 | 200 pts/sec |
| **Enterprise (Commerce Components)** | 20,000 | 1,000 pts/sec |

(Note: Shopify docs and the SyncApp in-tree reference are slightly inconsistent on Plus/Enterprise — the live docs say "1000/100 for non-Plus and 2000/200 for Plus." The numbers in the table above are from the in-tree SyncApp reference last fetched 2026-05-19, which captured the per-plan tiering more precisely. **Always trust `throttleStatus.maximumAvailable` from the actual response over any hard-coded table.**)

### Cost calculation defaults

Field cost is summed based on return type:

| Return type | Cost |
|---|---|
| **Scalar** (Int, String, Boolean, ID, etc.) | 0 |
| **Enum** | 0 |
| **Object** | 1 |
| **Interface / Union** | Max of all possible selected types |
| **Connection** (anything ending in `Connection`) | `first` or `last` value (multiplier) |
| **Mutation** (root field) | 10 (default; some mutations override) |

### Connection multipliers

A connection's cost equals `first` (or `last`) **multiplied by the cost of one node**.

Example:

```graphql
query {
  products(first: 50) {       # cost = 50 × (product node cost)
    edges {
      node {
        id                   # scalar = 0
        title                # scalar = 0
        variants(first: 10) {  # nested: 10 × variant node cost, per product = 500 total
          edges { node { id sku } }
        }
      }
    }
  }
}
```

Approximate total: 50 × (1 + 10 × 1) = 550 points.

### Per-request hard cap

**A single query may not exceed 1,000 points.** Even if your bucket has 10,000, one query can't ask for more than 1,000. If it does, you get `MAX_COST_EXCEEDED`.

Solution: smaller pages, or bulk operations.

### `extensions.cost` response structure

Every GraphQL response includes:

```json
{
  "data": { ... },
  "extensions": {
    "cost": {
      "requestedQueryCost": 8,
      "actualQueryCost": 4,
      "throttleStatus": {
        "maximumAvailable": 1000,
        "currentlyAvailable": 996,
        "restoreRate": 50
      }
    }
  }
}
```

| Field | Meaning |
|---|---|
| `requestedQueryCost` | Cost estimated **before** execution (based on `first` args, etc.) |
| `actualQueryCost` | Cost charged **after** execution. The difference between requested and actual is **refunded** to the bucket. |
| `throttleStatus.maximumAvailable` | Your bucket size — authoritative per plan. |
| `throttleStatus.currentlyAvailable` | Points left in your bucket right now. |
| `throttleStatus.restoreRate` | Points added per second. |

### Mutation cost

- **Default mutation cost: 10 points.**
- Some mutations override this — `bulkOperationRunQuery` is 10, `productCreate` ~10, but heavier mutations may charge more.
- The only authoritative value is `actualQueryCost` from the response. Don't hard-code estimates without measuring.

### Backoff strategy

When you get `THROTTLED` (HTTP 429):

1. Read `Retry-After` header if present — that's Shopify's suggested wait in seconds.
2. Otherwise, wait at least **1 second** (Shopify's recommendation).
3. Exponential backoff on repeated throttles: 1s → 2s → 4s → 8s → max 30s.
4. After the wait, retry the same query.

**Better strategy** (used by SyncApp's `rateLimitedShopifyFetch`): track `throttleStatus` from every response and **proactively** delay the next request if `currentlyAvailable < estimatedCost`. Compute wait as:

```
waitMs = ((estimatedCost - currentlyAvailable) / restoreRate) * 1000 + jitter
```

This avoids ever hitting 429 in the first place.

### Per-shop, not per-app

The bucket is **scoped to (app, shop)**. Two installs of the same app on different shops get separate buckets. Two different apps on the same shop get separate buckets.

### Bulk operations bypass the bucket

Bulk operations have their own per-shop concurrency limit (one bulk query + one bulk mutation in flight at a time, raised to 5 bulk queries in 2026-01+). The trigger mutation (`bulkOperationRunQuery`) costs 10 points. The actual data scan does **not** consume the standard bucket. See [§6](#6-bulk-operations).

### Throttled error format

```json
{
  "errors": [
    {
      "message": "Throttled",
      "extensions": { "code": "THROTTLED" }
    }
  ],
  "extensions": {
    "cost": {
      "throttleStatus": { "currentlyAvailable": 0, "restoreRate": 50, "maximumAvailable": 1000 }
    }
  }
}
```

The `data` field may be partial or null. Don't process it.

### Max cost exceeded

```json
{
  "errors": [
    {
      "message": "Query cost is 1500, which exceeds the single query max cost limit (1000).",
      "extensions": { "code": "MAX_COST_EXCEEDED" }
    }
  ]
}
```

This is a permanent failure for the query as written — no retry will fix it. Reduce `first:` values, drop nested connections, or switch to bulk operations.

---

## 5. Pagination

Shopify GraphQL pagination is **cursor-based, exclusively**. No offset/limit, no page numbers.

### Connection pattern

Every list field that can be paginated returns a `Connection` type with this shape:

```graphql
type ProductConnection {
  edges: [ProductEdge!]!
  nodes: [Product!]!     # newer / simpler API — same data as edges.node
  pageInfo: PageInfo!
}

type ProductEdge {
  cursor: String!        # opaque cursor for this edge
  node: Product!
}

type PageInfo {
  hasNextPage: Boolean!
  hasPreviousPage: Boolean!
  startCursor: String
  endCursor: String
}
```

### Arguments — forward pagination

| Arg | Type | Notes |
|---|---|---|
| `first` | `Int!` | Number of items to return. **Max 250.** |
| `after` | `String` | Cursor — typically the `endCursor` from the previous page. |

### Arguments — backward pagination

| Arg | Type | Notes |
|---|---|---|
| `last` | `Int!` | Number of items to return. **Max 250.** |
| `before` | `String` | Cursor — typically the `startCursor` from the previous page. |

You can't mix forward and backward in the same query.

### Maximum page size

**250.** Trying `first: 251` returns a validation error before execution. For >250 records, paginate or use bulk operations.

### Cursors are stable

A cursor encodes the position in a sort key. As long as the sort key doesn't change, the cursor is stable across requests. If you re-query a few minutes later, the same `endCursor` still works.

Cursors are **not portable across queries** — a cursor from `products(sortKey: TITLE)` doesn't work in `products(sortKey: CREATED_AT)`. Same for different `query:` filters.

### Two output styles: `nodes` vs `edges`

**`nodes` (recommended for most cases):**

```graphql
query Products($cursor: String) {
  products(first: 50, after: $cursor) {
    nodes {
      id
      title
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

**`edges` (use when you need per-edge metadata, e.g. cursor of a specific edge):**

```graphql
query Products($cursor: String) {
  products(first: 50, after: $cursor) {
    edges {
      cursor
      node { id title }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

`nodes` is cheaper to type and equally cost-efficient.

### Pagination loop pseudocode

```typescript
async function paginateAll<T>(query: string, vars: object): Promise<T[]> {
  const all: T[] = [];
  let cursor: string | null = null;
  let hasNext = true;
  while (hasNext) {
    const resp = await admin.graphql(query, { variables: { ...vars, cursor } });
    const data = resp.data.products; // or whatever connection
    all.push(...data.nodes);
    cursor = data.pageInfo.endCursor;
    hasNext = data.pageInfo.hasNextPage;
  }
  return all;
}
```

**Caveats:**

1. For >1,000 records, this loop will throttle. Use bulk operations.
2. Stop paginating if you hit a soft deadline (e.g. webhook ACK in 5 seconds). Resume in a background worker.
3. The connection field's cost is `first × node-cost`. Larger pages = more points per call, but fewer round trips. Tune to fit your bucket.

### Sort keys

Most connections support a `sortKey` argument. Match it to your search field if you're using a `query:` filter — Shopify's docs warn this prevents timeout on large result sets.

Example: `products(query: "vendor:Acme", sortKey: TITLE, first: 50)` is more efficient than mixing `query: "vendor:Acme"` with `sortKey: CREATED_AT`.

`reverse: Boolean` (default `false`) flips the sort direction.

---

## 6. Bulk operations

Shopify's escape hatch for "I need the entire catalog" or "I want to update 50,000 variants." Both ends — read and write — are asynchronous and bypass the normal rate limit bucket.

### Two variants

| Mutation | Purpose |
|---|---|
| `bulkOperationRunQuery` | **Bulk read.** Submit a single root query; Shopify executes it asynchronously and produces a JSONL file URL. |
| `bulkOperationRunMutation` | **Bulk write.** Submit a mutation string + a JSONL file of input variables; Shopify executes the mutation once per JSONL line and produces a result JSONL. |

### Bulk query: lifecycle

1. **Trigger:**

   ```graphql
   mutation {
     bulkOperationRunQuery(
       query: """
       {
         products {
           edges {
             node {
               id
               title
               variants {
                 edges { node { id sku barcode inventoryQuantity } }
               }
             }
           }
         }
       }
       """
     ) {
       bulkOperation { id status }
       userErrors { field message }
     }
   }
   ```

   Cost: 10 points (the trigger mutation itself).

2. **Initial status:** `CREATED`. Shopify queues the operation.

3. **Poll for completion** — two strategies:

   **Webhook (recommended for production):** subscribe to `BULK_OPERATIONS_FINISH`. Payload contains the bulk operation GID; query the node by ID to get `status`, `url`, `errorCode`.

   **Polling:** query `currentBulkOperation` (deprecated in 2026-01+) or the newer `bulkOperations` connection:

   ```graphql
   query {
     bulkOperations(first: 1, query: "status:running", sortKey: CREATED_AT, reverse: true) {
       nodes { id status objectCount fileSize url errorCode }
     }
   }
   ```

   Typical cadence: every 10 seconds. Don't poll faster — the bucket cost adds up.

4. **Status transitions:**
   ```
   CREATED → RUNNING → COMPLETED | FAILED | CANCELED
                                      ↓
                                   EXPIRED (after 7 days — URL becomes invalid)
                CANCELING → CANCELED
   ```

5. **Download:** when `status: COMPLETED`, the `url` field contains a signed S3/GCS URL with the JSONL output. **Valid for 7 days.** No auth required to download — the signature is in the URL.

6. **Parse the JSONL:** one JSON object per line (see format below).

7. **Cleanup is automatic** — Shopify expires the URL after 7 days. You don't have to "release" anything.

### Bulk mutation: lifecycle

Three-step workflow:

1. **`stagedUploadsCreate`** — get a signed upload URL for your JSONL input file:

   ```graphql
   mutation {
     stagedUploadsCreate(input: [{
       filename: "bulk_input.jsonl"
       mimeType: "text/jsonl"
       httpMethod: POST
       resource: BULK_MUTATION_VARIABLES
     }]) {
       stagedTargets {
         url
         resourceUrl
         parameters { name value }
       }
     }
   }
   ```

2. **PUT or POST the JSONL** to the returned `url`, including the `parameters` (these are AWS / GCS form fields).

3. **`bulkOperationRunMutation`** — trigger the bulk execution:

   ```graphql
   mutation {
     bulkOperationRunMutation(
       mutation: """
       mutation call($input: ProductInput!) {
         productCreate(input: $input) {
           product { id }
           userErrors { field message }
         }
       }
       """,
       stagedUploadPath: "<resourceUrl from step 1>"
     ) {
       bulkOperation { id status }
       userErrors { field message }
     }
   }
   ```

4. **Poll / webhook** same as bulk query.

5. **Download the result JSONL** — one line per mutation invocation, with the response or error.

### JSONL output format (bulk query)

Each line is a JSON object representing one node from the query.

**Without `groupObjects: true` (default):** every node is a separate line. Children include a `__parentId` field pointing to their parent's `id`.

```jsonl
{"id":"gid://shopify/Product/1921569226808","title":"T-shirt"}
{"id":"gid://shopify/ProductVariant/19435458986123","sku":"TS-RED-S","__parentId":"gid://shopify/Product/1921569226808"}
{"id":"gid://shopify/ProductVariant/19435458986124","sku":"TS-RED-M","__parentId":"gid://shopify/Product/1921569226808"}
{"id":"gid://shopify/Product/1921569226809","title":"Hoodie"}
{"id":"gid://shopify/ProductVariant/19435458986125","sku":"HD-BLU-L","__parentId":"gid://shopify/Product/1921569226809"}
```

The order is "depth-first, but flattened" — you'll see a parent, then its children, then the next parent. You need to track `__parentId` to reconstruct hierarchy.

**With `groupObjects: true`:** children nest inside parents in the JSON. Slower, more memory-heavy, but easier to parse.

### JSONL input format (bulk mutation)

Each line is a `variables` object for one execution of your mutation:

```jsonl
{"input": {"title": "Product A", "vendor": "Acme"}}
{"input": {"title": "Product B", "vendor": "Acme"}}
{"input": {"title": "Product C", "vendor": "Acme"}}
```

Maximum file size: large (Shopify doesn't publish a hard limit, but files >100MB risk timeout). Split into multiple bulk ops if needed.

### Query restrictions (bulk queries only)

You **cannot** use:

| Restriction | Why |
|---|---|
| `first:` or `last:` arguments on connections | Bulk query scans all results; `first:` is ignored anyway. Including it is allowed but harmless. |
| Three or more levels of nested connections | Hard limit — query is rejected. |
| More than 5 total connections in the query | Hard limit. |
| Top-level `node` or `nodes` fields | Only connection roots are allowed. |
| Inline fragments (`... on Type`) | Not supported in bulk queries. |
| Pagination cursors (`pageInfo`, `edges.cursor`) | Ignored if present. |

You **must** include at least one connection field at the root.

Sorting and filtering (`sortKey`, `reverse`, `query:`) ARE supported.

### Concurrency limits

- **Before 2026-01:** one bulk query AND one bulk mutation in flight per shop at a time.
- **In 2026-01 and later:** up to **5 bulk query operations** simultaneously per shop. Bulk mutation still limited to 1 at a time.
- Operations must finish within **10 days** or auto-fail. (In practice, anything longer than a few hours is suspicious.)

### Cost model

- Trigger mutations cost 10 points each.
- The actual data scan does **not** consume the per-shop point bucket. Bulk is metered by concurrency (above), not points.
- This is why bulk is the right answer for >1,000-row reads — a paginated query would burn the bucket; bulk is "free."

### Webhook: `BULK_OPERATIONS_FINISH`

Subscribe in `shopify.app.toml`:

```toml
[[webhooks.subscriptions]]
topics = ["bulk_operations/finish"]
uri = "/webhooks"
```

Payload (representative):

```json
{
  "admin_graphql_api_id": "gid://shopify/BulkOperation/720918",
  "completed_at": "2026-05-24T10:30:00Z",
  "created_at": "2026-05-24T10:20:00Z",
  "error_code": null,
  "status": "completed",
  "type": "query"
}
```

Then query the node to get the download URL:

```graphql
query {
  node(id: "gid://shopify/BulkOperation/720918") {
    ... on BulkOperation { url partialDataUrl status errorCode }
  }
}
```

### BulkOperation object — all fields

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | GID. |
| `status` | `BulkOperationStatus!` | See enum below. |
| `type` | `BulkOperationType!` | `QUERY` or `MUTATION`. |
| `createdAt` | `DateTime!` | When the operation was queued. |
| `completedAt` | `DateTime` | When it finished. Null while running. |
| `objectCount` | `UnsignedInt64!` | Total nodes processed (including children). |
| `rootObjectCount` | `UnsignedInt64!` | Top-level nodes only. |
| `fileSize` | `UnsignedInt64` | Output JSONL size in bytes. |
| `url` | `URL` | Signed download URL for the result. **Expires after 7 days.** |
| `partialDataUrl` | `URL` | Signed URL with whatever data was retrieved before failure. Same 7-day expiry. |
| `query` | `String!` | The query/mutation string that was submitted. |
| `errorCode` | `BulkOperationErrorCode` | Set if `status == FAILED`. |

### `BulkOperationStatus` enum

| Value | Meaning |
|---|---|
| `CREATED` | Queued, not yet started. |
| `RUNNING` | Actively executing. |
| `COMPLETED` | Finished successfully — `url` is ready. |
| `CANCELING` | Cancel requested, not yet stopped. |
| `CANCELED` | User-canceled. |
| `FAILED` | Errored out — check `errorCode` and `partialDataUrl`. |
| `EXPIRED` | The result URL has expired (7 days post-completion). |

### `BulkOperationErrorCode` enum

| Code | Meaning | Recovery |
|---|---|---|
| `ACCESS_DENIED` | Missing scopes for fields in the query. | Add the required scopes; reinstall if needed. |
| `INTERNAL_SERVER_ERROR` | Shopify-side error during execution; may have partial data. | Retry the same query. |
| `TIMEOUT` | Query took too long; may have partial data. | Simplify the query (fewer fields, fewer nested connections). |

### `bulkOperationCancel` mutation

```graphql
mutation { bulkOperationCancel(id: "gid://shopify/BulkOperation/720918") { bulkOperation { status } userErrors { field message } } }
```

Transitions the op to `CANCELING` → `CANCELED`. There may be a brief delay before it actually halts.

### `currentBulkOperation` query (deprecated in 2026-01+)

```graphql
query { currentBulkOperation(type: QUERY) { id status url } }
```

**Deprecated** in 2026-01+. Use the `bulkOperations` connection with a status filter instead:

```graphql
query {
  bulkOperations(first: 1, query: "status:running", sortKey: CREATED_AT, reverse: true) {
    nodes { id status url }
  }
}
```

Still works in 2026-04 but the deprecation warning is in the schema.

### Use cases

- **Full catalog export** — pull every product + variant + inventory level once a day for analytics.
- **Initial import on app install** — for a merchant with 10,000+ variants, paginated import takes minutes and burns the bucket; bulk runs in the background.
- **Backfill a new field** — e.g. you add a barcode-grouping algorithm; recompute for all existing variants.
- **Full order history export** — pull all orders ever.
- **Mass tag update on 50,000 products** — bulk mutation with a JSONL of `productUpdate` invocations.

### Don't use bulk for

- Real-time / low-latency reads (webhook handlers, UI loaders) — bulk has minutes of latency before `COMPLETED`.
- Small reads (<500 nodes) — overhead of two GraphQL calls + JSONL parsing outweighs the savings.
- Single-shop interactive editing — use regular mutations.

---

## 7. The `@idempotent` directive

### What it is

A GraphQL directive applied to mutation fields (not to the `mutation` operation keyword). The first invocation with a given `key` executes normally; subsequent invocations with the same key replay the original response without re-executing the mutation.

### Syntax

```graphql
mutation SetInventoryQuantities($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup { reason changes { name quantityAfterChange } }
    userErrors { field message code }
  }
}
```

**Placement:** on the **field call** (`inventorySetQuantities(input: $input)`), NOT on the `mutation` keyword.

### Key specs

| Property | Value |
|---|---|
| Type | `String!` |
| Max length | **255 characters** |
| Recommended format | UUID v4 (e.g. `b8f0b172-1ffc-41ff-90c5-14c254e3c202`) — guarantees uniqueness without coordination |
| Deduplication window | **24 hours** server-side |

### Behavior

| Scenario | Result |
|---|---|
| Same key + same variables, within 24h | Shopify replays the cached response. Mutation does **not** execute again. |
| Same key + different variables, within 24h | Returns error `IDEMPOTENCY_KEY_PARAMETER_MISMATCH`. |
| Same key, two concurrent requests in flight | One executes; the other returns `IDEMPOTENCY_CONCURRENT_REQUEST`. |
| Same key after 24h | New execution — the cache has expired. |
| Different keys, same variables | Both execute independently. |

### Mutations that support / require `@idempotent`

As of API version **2026-04**, the directive is **mandatory** on a set of 18 mutations covering inventory and refunds. The directive was optional in 2026-01 (warning emitted), required in 2026-04.

Confirmed required (from live docs and SyncApp's in-tree reference):

- `inventorySetQuantities`
- `inventoryAdjustQuantities`
- `inventoryMoveQuantities`
- `inventoryActivate`
- `inventoryDeactivate`
- `refundCreate`
- ...plus ~12 other inventory + refund mutations (full list not officially enumerated; check each mutation's docs page for "@idempotent" in the description).

Mutations that do NOT require it (representative list):

- `productCreate`, `productUpdate`, `productDelete`
- `productVariantsBulkCreate`, `productVariantsBulkUpdate`
- `customerCreate`, `customerUpdate`
- `metafieldsSet`, `metafieldsDelete`
- File and staged-upload mutations

### Why it matters for SyncApp

Every push to Shopify (inventory writes) MUST include `@idempotent`. Without the key, the request fails at validation time. Practically:

1. **Sync orchestrator** — generates a deterministic key per `(groupId, poolVersion, shopId, locationId, targetQty)` so that BullMQ retries hit the cache instead of double-applying.
2. **Manual UI adjust/set buttons** — fresh UUID per submit; user-initiated retries are separate intents.
3. **Reconciliation worker** — deterministic key per `(reconciliation_run_id, shopId, locationId, itemId)` within a single recon run.

See [SyncApp's idempotency strategy table](file:///C:/Users/Admin/Desktop/SyncApp/docs/SHOPIFY-API-REFERENCE.md) for the concrete pattern.

### Error codes

| Code | When | Recovery |
|---|---|---|
| `IDEMPOTENCY_KEY_PARAMETER_MISMATCH` | Same key reused with different variables. | This is a bug in your deterministic-key formula. Surface it; don't auto-retry. |
| `IDEMPOTENCY_CONCURRENT_REQUEST` | Two callers racing with the same key. | Backoff briefly (50-200ms jitter) and retry; benign at scale. |

### Idempotency in bulk mutations

When you use `bulkOperationRunMutation` with `@idempotent`, **idempotency is applied per JSONL row, not per the entire bulk operation.** Provide the idempotency key as a variable in each JSONL line:

```jsonl
{"input": {"name": "available", "quantities": [...]}, "idempotencyKey": "uuid-1"}
{"input": {"name": "available", "quantities": [...]}, "idempotencyKey": "uuid-2"}
```

Retrying the entire bulk op replays each row's cached response.

### REST API note

REST endpoints use the `Idempotency-Key` HTTP header instead of the GraphQL directive. The semantics are similar but separate — don't confuse them. GraphQL Admin API uses **only** the directive.

---

## 8. Error handling

### Two error layers

GraphQL responses have **two** error reporting mechanisms — they mean different things:

1. **Top-level `errors` array** — protocol / transport / authorization errors. Set when the query couldn't be processed correctly. `data` may be null or partial.
2. **`data.<mutation>.userErrors` array** — business-logic errors from the mutation. The query executed fine, but the operation was rejected (e.g. validation failure).

**Always check both.** A mutation can return `errors: null` and `userErrors: [{...}]` — that's a normal validation failure.

### HTTP status codes

| Code | Meaning | Retry semantic |
|---|---|---|
| `200 OK` | Request processed. Check `errors` and `userErrors` in body. | n/a |
| `401 Unauthorized` | Missing or invalid `X-Shopify-Access-Token`. | Re-auth; do not retry with same credentials. |
| `402 Payment Required` | Shop is frozen due to billing issue. | Surface to merchant; don't retry until resolved. |
| `403 Forbidden` | Token lacks required scope, OR valid online token lacks user permission. | Request the scope (via OAuth re-install) or surface to user. Don't retry. |
| `404 Not Found` | Resource ID doesn't exist (or wrong endpoint URL). | Confirm ID; immediate retry won't help. |
| `422 Unprocessable Entity` | Request well-formed but semantically invalid (rare in GraphQL — usually surfaces as `userErrors`). | Fix request; don't blind-retry. |
| `423 Locked` | Shop locked due to repeated rate-limit violations or fraud detection. | Contact Shopify support; don't retry. |
| `429 Too Many Requests` | Rate limit hit. `extensions.code: THROTTLED`. | Wait `Retry-After` or 1s+backoff, then retry. |
| `430 Shopify Security Rejection` | Shopify's bot/security system rejected the request. | Investigate request patterns; persistent rejections need support. |
| `500 Internal Server Error` | Shopify-side error. | Exponential backoff retry; if persistent, record `X-Request-ID` and contact Partner Support. |
| `520` / `502` / `503` / `504` | Edge / proxy errors. | Exponential backoff retry. |

### GraphQL error codes (in `errors[].extensions.code`)

| Code | Meaning |
|---|---|
| `THROTTLED` | Bucket empty. Wait + retry. |
| `MAX_COST_EXCEEDED` | Single query exceeds 1,000 points. Permanent — reduce query size. |
| `ACCESS_DENIED` | Token lacks scope. Permanent — fix scopes. |
| `INVALID_API_VERSION` | Version in URL is invalid or unsupported. |
| `INVALID_REQUEST` | Malformed GraphQL (syntax error, type mismatch). Permanent — fix code. |
| `FIELD_NOT_FOUND` | Selected field doesn't exist on type. Permanent — fix code (often a version-mismatch symptom). |
| `INTERNAL_SERVER_ERROR` | Shopify side. Retry with backoff. |
| `IDEMPOTENCY_KEY_PARAMETER_MISMATCH` | Same idempotency key, different variables. Permanent — fix key formula. |
| `IDEMPOTENCY_CONCURRENT_REQUEST` | Same key, concurrent requests. Brief backoff + retry. |

### `userErrors` shape

Almost every mutation returns a `userErrors` field. The type implements the `UserError` interface (sometimes extended — `InventoryUserError`, `MetafieldsSetUserError`, etc.):

```graphql
type InventoryUserError implements DisplayableError {
  code: InventoryErrorCode   # enum specific to inventory mutations
  field: [String!]           # path into the input where the error occurred
  message: String!           # human-readable
}
```

**Standard fields on the interface:**

- `field: [String!]` — JSON pointer into the input (e.g. `["input", "quantities", "0", "quantity"]`).
- `message: String!` — error text. Don't surface raw to end users — translate where needed.
- `code: String` (often enum) — machine-readable. Branch on this, not on `message`.

### Common `userError.code` values to handle

Examples for inventory mutations (from SyncApp's in-tree reference):

| Code | When | Recovery |
|---|---|---|
| `CHANGE_FROM_QUANTITY_STALE` | CAS mismatch — Shopify state changed since you read. | Re-read, recompute, retry. Mark group dirty if it persists. |
| `INVALID_INVENTORY_ITEM` | Inventory item GID doesn't exist or isn't on this shop. | Log; skip; the link is stale. |
| `INVENTORY_ITEM_NOT_AT_LOCATION` | Item not activated at the target location. | Call `inventoryActivate` first, retry. |
| `INVENTORY_ITEM_NOT_TRACKED` | Variant has `tracked: false` in Shopify. | Skip; merchant config issue. |

### Cost-exceeded scenarios

If your query has `requestedQueryCost > maximumAvailable`:

- Pre-execution: returns `MAX_COST_EXCEEDED`. The query never runs.
- Mid-execution: extremely rare — Shopify usually estimates correctly. If it happens (e.g. variable expansion blows up cost), you get `THROTTLED` + partial data.

Mitigation: smaller `first:` values, fewer nested connections, or switch to bulk operations.

### `INTERNAL_SERVER_ERROR` retry semantics

- Transient: retry with exponential backoff (1s → 2s → 4s → 8s, max 30s, 5 attempts).
- Persistent: capture `X-Request-ID`, timestamp, full request body, and open a Partner Support ticket.
- Bulk op `errorCode: INTERNAL_SERVER_ERROR` is similarly retriable — re-run the same query.

### Error handling decision tree

```
Response received
├── HTTP 200
│   ├── data.<mutation>.userErrors.length > 0
│   │   └── Inspect code → branch by domain logic (see table above)
│   ├── errors[].length > 0
│   │   ├── code: THROTTLED → wait + retry
│   │   ├── code: MAX_COST_EXCEEDED → reduce query, fail fast
│   │   ├── code: ACCESS_DENIED → fix scopes, fail
│   │   └── code: INTERNAL_SERVER_ERROR → backoff + retry
│   └── otherwise → success
├── HTTP 401/403 → re-auth or fix scopes
├── HTTP 402 → billing issue, surface to user
├── HTTP 429 → wait + retry
└── HTTP 5xx → backoff + retry
```

---

## 9. Metafields

Metafields attach custom key-value data to almost any Shopify resource. Three uses:

1. **Merchant custom data** — extra fields on products, orders, customers (e.g. "warranty period" on a product).
2. **App-private storage** — apps store per-shop configuration in `AppInstallation` metafields without needing their own DB.
3. **Inter-app data sharing** — apps expose metafields to merchants and other apps via the storefront.

### Anatomy of a metafield

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | GID. |
| `namespace` | `String!` | Logical container. Conventions below. |
| `key` | `String!` | Unique within (owner, namespace). |
| `type` | `String!` | Data type — see [types](#metafield-types) below. |
| `value` | `String!` | Always serialized as a string; interpretation depends on `type`. |
| `jsonValue` | `JSON` | Same data, but already parsed where applicable. |
| `ownerType` | `MetafieldOwnerType!` | The kind of resource this attaches to. |
| `owner` | `HasMetafields!` | The actual resource node. |
| `definition` | `MetafieldDefinition` | If a definition exists for `(ownerType, namespace, key)`. |
| `createdAt`, `updatedAt` | `DateTime!` | Standard. |
| `compareDigest` | `String` | Hash for compare-and-set operations (since 2024-07). |

### `MetafieldOwnerType` enum

Complete list (24 values as of 2026-04):

`API_PERMISSION`, `ARTICLE`, `BLOG`, `CARTTRANSFORM`, `COLLECTION`, `COMPANY`, `COMPANY_LOCATION`, `CUSTOMER`, `DELIVERY_CUSTOMIZATION`, `DISCOUNT`, `DRAFTORDER`, `FULFILLMENT_CONSTRAINT_RULE`, `GIFT_CARD_TRANSACTION`, `LOCATION`, `MARKET`, `ORDER`, `ORDER_ROUTING_LOCATION_RULE`, `PAGE`, `PAYMENT_CUSTOMIZATION`, `PRODUCT`, `PRODUCTVARIANT`, `SELLING_PLAN`, `SHOP`, `VALIDATION`.

(Note: `MEDIA_IMAGE` was deprecated.) `API_PERMISSION` is the owner type for `AppInstallation` metafields.

### Namespace conventions

| Namespace | Owner | Visibility |
|---|---|---|
| `custom` | Merchant | Public (storefront API, other apps) |
| `specs`, `details`, etc. | Merchant | Public |
| `$app:<your-handle>` or `app:<your-handle>` | Your app, owned exclusively | **Private to the owning app** (no other app, no merchant can read/write) |
| Reserved Shopify namespaces | Shopify | Read-only for apps |

The `$app:` prefix is GraphQL-context syntax; in `shopify.app.toml` it's `app:`. Same thing.

### Metafield types — full list

**Text:**
- `single_line_text_field` — string, no line breaks.
- `multi_line_text_field` — string with line breaks.
- `rich_text_field` — Shopify's structured rich text JSON (paragraph/bold/italic tree).

**Numeric:**
- `number_integer` — JSON integer, range +/- 9,007,199,254,740,991.
- `number_decimal` — string of decimal digits, e.g. `"10.4"`.

**Boolean / scalar:**
- `boolean` — `true` or `false`.
- `color` — hex string `"#fff123"`.
- `id` — string with optional regex/min/max validations.

**Date / time:**
- `date` — ISO 8601 date `"2026-05-24"`.
- `date_time` — ISO 8601 datetime `"2026-05-24T12:30:00"`.

**Money / link:**
- `money` — JSON `{"amount": "10.00", "currency_code": "USD"}`.
- `link` — JSON `{"text": "Click here", "url": "https://..."}`.
- `url` — string URL with allowed schemes `https`, `http`, `mailto`, `sms`, `tel`.

**Measurement (all use `{"value": number, "unit": "name"}`):**
- `weight` — kg, g, lb, oz.
- `volume` — l, ml, m3, etc.
- `dimension` — mm, cm, m, in, ft.
- `area`, `distance`, `temperature`, `pressure`, `speed`, `duration`, `energy`, `frequency`, `power`, `voltage`, `data_storage_capacity`, `data_transfer_rate`.

**Rating:**
- `rating` — JSON `{"value": "3.5", "scale_min": "1.0", "scale_max": "5.0"}`.

**References (all are GID strings):**
- `product_reference` → `"gid://shopify/Product/1"`
- `variant_reference` → `"gid://shopify/ProductVariant/1"`
- `collection_reference` → `"gid://shopify/Collection/1"`
- `customer_reference` → `"gid://shopify/Customer/1"`
- `page_reference` → `"gid://shopify/Page/1"`
- `article_reference` → `"gid://shopify/Article/1"`
- `file_reference` → `"gid://shopify/MediaImage/123"` or other File subtype
- `metaobject_reference` → `"gid://shopify/Metaobject/123"`
- `mixed_reference` → metaobject of any allowed definition
- `company_reference` → `"gid://shopify/Company/1"`
- `product_taxonomy_value_reference` → `"gid://shopify/TaxonomyValue/1"`

**List types — prefix any basic or reference type with `list.`:**
- `list.single_line_text_field` → `["foo", "bar"]`
- `list.product_reference` → `["gid://shopify/Product/1", ...]`
- `list.color`, `list.number_integer`, etc.
- Measurement lists: `[{"value": 100, "unit": "meters"}, ...]`

**`json`** — escape hatch. Free-form JSON. Use only if no typed alternative fits.

### `metafieldsSet` mutation

Atomic upsert of up to **25 metafields per call** (max 10MB request).

```graphql
mutation SetMetafields($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key value }
    userErrors { field message code }
  }
}
```

`MetafieldsSetInput`:

| Field | Required | Notes |
|---|---|---|
| `ownerId` | yes | GID of the resource. |
| `namespace` | yes | E.g. `custom` or `$app:syncapp`. |
| `key` | yes | Unique within `(ownerId, namespace)`. |
| `type` | yes | Must match the definition's type if one exists. |
| `value` | yes | Serialized as a string regardless of type. |
| `compareDigest` | no (since 2024-07) | CAS — pass the current digest to ensure no concurrent overwrite. Pass `null` to assert "does not exist." |

**Atomicity:** if any one metafield fails validation, none are persisted.

**Permissions:** the access scope needed to mutate the owning resource. E.g. setting a metafield on a Product requires `write_products`. Setting an `AppInstallation` metafield requires no extra scope (the app already owns the install).

### `metafieldsDelete` mutation

```graphql
mutation DeleteMetafields($metafields: [MetafieldIdentifierInput!]!) {
  metafieldsDelete(metafields: $metafields) {
    deletedMetafields { ownerId namespace key }
    userErrors { field message }
  }
}
```

Input: `[{ownerId, namespace, key}]`. Each must be a separate identifier.

Behavior: graceful — if a metafield doesn't exist, the mutation still succeeds and returns `null` for that identifier.

### Metafield definitions

A `MetafieldDefinition` formalizes a `(ownerType, namespace, key)` triple with a fixed type and optional validations. Defining one is **optional** but recommended for:

- Type-safety enforcement.
- Pinning in the admin UI for merchants.
- Validation rules (min/max, regex, allowed values).
- Use as collection conditions (auto-collections).
- Showing up in the standard Shopify customer/merchant UI.

#### `metafieldDefinitionCreate`

```graphql
mutation {
  metafieldDefinitionCreate(definition: {
    name: "Sync mode",
    namespace: "$app:syncapp",
    key: "default_sync_mode",
    description: "Default allocation strategy for new barcode groups.",
    type: "single_line_text_field",
    ownerType: SHOP,
    validations: [{name: "choices", value: "[\"safe\",\"aggressive\",\"main_store\",\"weighted\",\"manual\"]"}]
  }) {
    createdDefinition { id name }
    userErrors { field message code }
  }
}
```

When a definition is created, any existing unstructured metafields matching `(ownerType, namespace, key)` are **validated against it**. Compliant ones are bound; non-compliant ones remain unstructured until updated.

#### `metafieldDefinitionUpdate`

Updates name, description, validations, access settings, and capabilities. **Cannot change** `type`, `namespace`, `key`, or `ownerType` — those identify the definition.

```graphql
mutation { metafieldDefinitionUpdate(definition: { namespace: "$app:syncapp", key: "default_sync_mode", ownerType: SHOP, name: "Default sync mode (canonical)" }) { updatedDefinition { id name } userErrors { field message } } }
```

Returns a `validationJob` (an async `Job` object) if existing metafields need re-validation.

#### `metafieldDefinitionDelete`

```graphql
mutation {
  metafieldDefinitionDelete(
    identifier: { ownerType: SHOP, namespace: "$app:syncapp", key: "default_sync_mode" }
    deleteAllAssociatedMetafields: true
  ) {
    deletedDefinitionId
    userErrors { field message }
  }
}
```

`deleteAllAssociatedMetafields` **must be `true`** when deleting `$app:` namespace definitions — otherwise the metafields are orphaned.

### Querying metafields

**Single metafield by namespace/key (on any resource implementing HasMetafields):**

```graphql
query {
  product(id: "gid://shopify/Product/1") {
    metafield(namespace: "custom", key: "warranty") { value type }
  }
}
```

**All metafields with pagination:**

```graphql
query {
  product(id: "gid://shopify/Product/1") {
    metafields(first: 50, namespace: "custom") {
      nodes { namespace key value type }
      pageInfo { hasNextPage endCursor }
    }
  }
}
```

**Definitions:**

```graphql
query {
  metafieldDefinitions(ownerType: PRODUCT, first: 50) {
    nodes { id namespace key type name }
  }
}
```

### App-private storage via `AppInstallation` metafields

The single most useful trick for apps that don't want to host their own DB for per-shop config:

```graphql
query { currentAppInstallation { id } }
```

Returns the app's installation node for the current shop. Use that ID to set/get app-private metafields:

```graphql
mutation {
  metafieldsSet(metafields: [{
    ownerId: "gid://shopify/AppInstallation/12345",
    namespace: "$app:syncapp",
    key: "default_strategy",
    type: "single_line_text_field",
    value: "weighted"
  }]) {
    metafields { id }
    userErrors { field message }
  }
}
```

**Properties:**

- Only the owning app can read/write these metafields.
- Merchants cannot view or edit them in the admin UI.
- Other apps cannot access them.
- Persists across uninstall/reinstall **only if** you preserve the install record on uninstall.
- Storage is roughly bounded — not designed for >MB-scale data, but fine for config blobs.

**SyncApp use case:** workspace-level default settings (default allocation strategy, default safety buffer) could be stored as `AppInstallation` metafields if you wanted to avoid Postgres for that one record. Currently SyncApp uses Postgres because of cross-shop workspace scoping, but it's a legitimate alternative for single-shop scope.

---

## 10. Files

Shopify's "Files" section in the admin holds:

- Product images (`MediaImage`)
- Videos (`Video`, `ExternalVideo`)
- 3D models (`Model3d`)
- Generic uploads — CSV, PDF, JSON, ZIP, etc. (`GenericFile`)

All implement the `File` interface and `Node`.

### `File` interface — common fields

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | GID. |
| `alt` | `String` | Accessibility description (max 512 chars). |
| `createdAt` | `DateTime!` | Upload time. |
| `updatedAt` | `DateTime!` | Last modification. |
| `fileStatus` | `FileStatus!` | Processing state — see enum below. |
| `fileErrors` | `[FileError!]!` | Set if `fileStatus: FAILED`. |
| `preview` | `MediaPreviewImage` | Thumbnail (for videos/3D); identity for images. |

### `FileStatus` enum

| Value | Meaning |
|---|---|
| `UPLOADED` | Bytes received but not yet processed. |
| `PROCESSING` | Shopify is generating renditions / running validation. |
| `READY` | Fully processed; usable in products / themes. **Most file-update operations require `READY` state.** |
| `FAILED` | Processing failed. Check `fileErrors`. |

### `GenericFile` (non-media uploads)

For CSV / PDF / JSON / ZIP and other documents.

| Field | Type | Notes |
|---|---|---|
| `id`, `alt`, `createdAt`, `updatedAt`, `fileStatus`, `fileErrors` | (from File interface) | |
| `originalFileSize` | `Int` | In bytes. |
| `mimeType` | `String` | E.g. `"text/csv"`, `"application/pdf"`, `"application/json"`. |
| `url` | `URL` | Public download URL. |

### Required scopes for Files API

One of: `write_files`, `write_themes`, `write_images` (for create/update/delete). Reads require `read_files`, `read_themes`, `read_images`, `read_orders`, `read_products`, `read_quick_sale`, or `read_draft_orders` (the broadest one your app already has).

### Two-step upload flow (production-grade)

For files > a few MB, or to avoid timeouts and bandwidth on your own server:

**Step 1: stagedUploadsCreate** — get a signed upload URL.

```graphql
mutation StagedUpload($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters { name value }
    }
    userErrors { field message }
  }
}
```

**`StagedUploadInput`:**

| Field | Required | Notes |
|---|---|---|
| `filename` | yes | Name of the file. |
| `mimeType` | yes | E.g. `"image/jpeg"`, `"text/csv"`. |
| `httpMethod` | yes | `POST` (default for AWS S3) or `PUT` (GCS). Shopify tells you which to use. |
| `resource` | yes | One of: `PRODUCT_IMAGE`, `COLLECTION_IMAGE`, `SHOP_IMAGE`, `VIDEO`, `MODEL_3D`, `FILE`, `URL_REDIRECT_IMPORT`, `BULK_MUTATION_VARIABLES`, `IMAGE`. |
| `fileSize` | only for `VIDEO` and `MODEL_3D` | Required so Shopify can pre-allocate. |

**Response:**

```json
{
  "stagedTargets": [{
    "url": "https://shopify-staged-uploads.storage.googleapis.com/...",
    "resourceUrl": "https://shopify-staged-uploads.storage.googleapis.com/.../bulk_input.jsonl",
    "parameters": [
      {"name": "Content-Type", "value": "text/jsonl"},
      {"name": "success_action_status", "value": "201"},
      {"name": "key", "value": "..."},
      {"name": "x-goog-signature", "value": "..."},
      ...
    ]
  }]
}
```

**Step 2: HTTP upload** — `POST` (or `PUT`) the file bytes to `url` with the `parameters` as form fields (multipart for POST, headers for PUT). Use the exact `httpMethod` Shopify specified.

After successful upload, `resourceUrl` is now usable.

**Step 3: fileCreate** — register the staged upload as a Shopify file:

```graphql
mutation CreateFile($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { id fileStatus alt }
    userErrors { field message code }
  }
}
```

**`FileCreateInput`:**

| Field | Required | Notes |
|---|---|---|
| `originalSource` | yes | The `resourceUrl` from step 1, OR an external HTTPS URL (Shopify will fetch). |
| `contentType` | yes | `IMAGE`, `VIDEO`, `EXTERNAL_VIDEO`, `MODEL_3D`, `FILE` (for GenericFile). |
| `alt` | no | Accessibility text. |
| `filename` | no | Override the name. |
| `duplicateResolutionMode` | no | `APPEND_UUID` (default — appends to avoid conflict), `RAISE_ERROR`, `REPLACE`. |

Max **250 files per `fileCreate` call**.

**Step 4: poll fileStatus** — wait for `READY`. Processing is async (especially for video / 3D).

```graphql
query CheckFile($id: ID!) {
  node(id: $id) {
    ... on File { fileStatus fileErrors { code details message } }
  }
}
```

### Direct upload (no staging) — small files only

You can pass an HTTPS URL directly to `fileCreate.originalSource` and Shopify will fetch it:

```graphql
mutation { fileCreate(files: [{ originalSource: "https://example.com/image.jpg", contentType: IMAGE, alt: "Product photo" }]) { files { id } userErrors { field message } } }
```

Use cases:

- The file is already hosted somewhere durable.
- The file is small (Shopify fetches with a timeout).
- You don't want to manage staged upload state.

Don't use for:

- Files only accessible to you (Shopify can't fetch).
- Anything > a few MB (timeout risk).
- Files that need to be uploaded synchronously.

### `fileUpdate`

```graphql
mutation { fileUpdate(files: [{ id: "gid://shopify/MediaImage/1", alt: "Updated description" }]) { files { id alt } userErrors { field message } } }
```

`FileUpdateInput`:

| Field | Required | Notes |
|---|---|---|
| `id` | yes | The file GID. |
| `alt` | no | New accessibility text. |
| `originalSource` | no | Replace file content (preserves the same `id` / URL). |
| `previewImageSource` | no | Updated thumbnail for video files. |
| `filename` | no | Rename. |

**Constraints:**

- Cannot update both `originalSource` and `previewImageSource` in a single update.
- File must be in `READY` state before update.
- Videos and 3D models can only have `alt` updated (plus product references) — not content.

### `fileDelete`

```graphql
mutation { fileDelete(fileIds: ["gid://shopify/MediaImage/1", "gid://shopify/MediaImage/2"]) { deletedFileIds userErrors { field message } } }
```

Behavior:

- Permanent. No undo.
- If the file is referenced by products, references are automatically removed and remaining media reordered.
- Files actively being processed are rejected (returns `userErrors`).

### `files` query

```graphql
query Files($query: String, $first: Int!, $after: String) {
  files(first: $first, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
    nodes {
      id
      alt
      fileStatus
      createdAt
      ... on GenericFile { url mimeType originalFileSize }
      ... on MediaImage { image { url width height } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

**Filterable via `query:`** (Shopify search syntax):

- `created_at:>2026-01-01`
- `filename:report*` (wildcard)
- `id:>=12345` (range)
- `media_type:GENERIC_FILE` (or `IMAGE`, `VIDEO`, `MODEL3D`)
- `original_upload_size:>1MB`
- `product_id:gid://shopify/Product/1`
- `status:READY` (or `UPLOADED`, `PROCESSING`, `FAILED`)
- `updated_at:>2026-04-01`
- `used_in:product` (or `none`)

**Sort keys:** `CREATED_AT` (default), `ID`, `UPDATED_AT`, `FILENAME`, `ORIGINAL_UPLOAD_SIZE`.

### File use cases

- **Product media** — image and video for product detail pages. Use `MediaImage` and link via `productCreateMedia` / `productUpdateMedia`.
- **Theme assets** — though theme files have their own API (`themeFiles*`).
- **Generic downloads** — PDF spec sheets, JSON config files, CSV reports for merchants.
- **Bulk operation input files** — JSONL files for `bulkOperationRunMutation` are uploaded via `stagedUploadsCreate` with `resource: BULK_MUTATION_VARIABLES`.

---

## 11. Release notes / breaking changes

Shopify publishes a single rolling developer changelog at https://shopify.dev/changelog. Per-version release notes pages exist but the URLs are not consistently linked from the API reference (we hit 404s on the canonical `/release_notes` paths). Use the main `/changelog` and filter by version.

### 2026-04 (current latest, released April 1, 2026)

**Inventory mutations:**
- **`@idempotent` directive mandatory on 18 inventory + refund mutations** — was optional in 2026-01. Without it, calls fail at validation.
- **`changeFromQuantity` field mandatory** on `InventoryQuantityInput` and `InventoryChangeInput`. Value may be `null` to skip CAS.
- **`inventorySetOnHandQuantities` deprecated** — use `inventorySetQuantities` with `name: "on_hand"`.

**Checkout:**
- Checkout metafields deprecated. Migrate to cart metafields (UI extensions) or order metafields (post-purchase).

**Discounts:**
- Multiple product discounts per cart line supported.
- Tags can be added to discounts for organization.

**Metaobjects:**
- App-owned metaobjects no longer require access scopes — simplifies app authoring.

**Draft orders:**
- `DraftOrderLineItem.components` field added — tracks line item hierarchy for bundles.

### 2026-01

**Bulk operations:**
- Apps can now run **up to 5 bulk query operations simultaneously** per shop (was 1).
- `currentBulkOperation` query deprecated — use `bulkOperations` connection with status filter.

**Inventory:**
- `@idempotent` directive introduced as optional. Calls without a key emit a warning.

**Offline access tokens:**
- New "expiring" offline tokens: 1h access + 90d refresh. Optional in 2026-01, mandatory for new public apps by **January 1, 2027**.

### 2025-10

- Metafield `compareDigest` field generally available (was in 2024-07 unstable).
- New `MetafieldOwnerType` values added: `MARKET`, `COMPANY_LOCATION`.
- Webhook topic `BULK_OPERATIONS_FINISH` formalized.

### 2025-07

- `productVariantsBulkCreate` / `productVariantsBulkUpdate` raised the per-product variant cap to 2048 (was 100).
- `productCreateMedia` consolidated under `productCreate` + `media` arg.

### 2026-07 (release candidate as of 2026-05)

**Products:**
- `ProductVariant` becomes `Publishable` — fine-grained per-channel visibility.

**Orders:**
- `Order.cartToken` field added.
- `LineItem.weight` queryable with unit flexibility.

**Metaobjects:**
- Deprecated enums `PRIVATE` and `PUBLIC_READ` to be removed (announced in 2026-04).

### Sunset dates worth knowing

| Item | Sunset |
|---|---|
| Shopify Scripts | June 30, 2026 — migrate to Functions. |
| Non-expiring offline tokens (public apps) | January 1, 2027 — must use expiring + refresh tokens. |
| `inventorySetOnHandQuantities` | After 2026-04 + 12 months ≈ April 2027. |
| `currentBulkOperation` | After 2026-01 + 12 months ≈ January 2027. |

### How to read the changelog

`/changelog` supports filters by version, API category, and change type (Added / Changed / Deprecated / Removed). For SyncApp, set up an alert for:
- Inventory mutations (any change can break sync)
- Bulk operations (we depend on this for large imports)
- Webhooks (topic schema changes are silent breaking changes)
- Authentication (token migration deadlines)

---

## 12. SyncApp angles — applied usage

How this platform reference maps to SyncApp's code.

### Bulk import for >1,000 product catalogs

Current state: `runFullImport` uses paginated `products(first: 50, after: $cursor)`. Cost ~80 points/page; a 10,000-variant store ≈ 100 pages × 80 = 8,000 points ≈ 80s at Standard restore rate.

Bulk path: `bulkOperationRunQuery` with the full product+variant+inventoryLevel tree. Submit, poll/webhook, download JSONL, parse with `__parentId` reconstruction, batch-insert into Prisma. No bucket impact during the scan — only the initial 10-point trigger.

Naming gotcha: SyncApp's `BULK_PRODUCTS_MUTATION` constant in [app/graphql/queries.ts](file:///C:/Users/Admin/Desktop/SyncApp/app/graphql/queries.ts) is actually a `bulkOperationRunQuery` invocation — the name is misleading. Rename to `BULK_PRODUCTS_QUERY` in Sprint 2 cleanup.

### Rate limiter must track throttleStatus

SyncApp's [`rateLimitedShopifyFetch`](file:///C:/Users/Admin/Desktop/SyncApp/app/services/rate-limiter.server.ts) implements the proactive pattern:

1. Each response's `extensions.cost` is captured.
2. State tracks `currentlyAvailable`, `restoreRate`, `maximumAvailable` per (app, shop).
3. Before next request, compute `estimatedCost` and wait `((estimatedCost - currentlyAvailable) / restoreRate) * 1000 + jitter` ms.
4. Circuit breaker trips on 5xx or network errors for 30s, then half-opens.

Current `cost:` estimate per mutation: hard-coded at 50 (over-budget for safety). Sprint 6 task: tune against observed `actualQueryCost`.

### `@idempotent` for inventory writes

SyncApp already does this (mandatory since 2026-04). The pattern:

| Code path | Key strategy |
|---|---|
| `sync-orchestrator` push | Deterministic: `sync-v1-{groupId}-{poolVersion}-{shopId}-{locationId}-{targetQty}`. BullMQ retry → Shopify cache hit → no double-apply. |
| `api.stock-adjust.tsx` manual | Fresh UUID per submit (`newIdempotencyKey("adjust")`). User retries are separate intents. |
| Reconciliation worker | Deterministic: `(reconciliation_run_id, shopId, locationId, itemId)`. Idempotent within a single recon run. |

255-char limit is generous; SyncApp uses prefixes like `"sync-"` for grep-ability in audit trails.

### Metafields for app config storage

Currently SyncApp uses Postgres for workspace-level config (`Workspace` table). For per-shop settings that could live without cross-shop joins (e.g. "default allocation strategy for new groups on this shop"), `AppInstallation` metafields under `$app:syncapp` namespace would work:

- No new table.
- No migration.
- Read-modify-write via `metafieldsSet` with `compareDigest`.
- Persists across uninstall if the install record is preserved.

Trade-off: Postgres queries are atomic with the rest of SyncApp's data; metafields aren't transactional with our DB. Don't use metafields for anything that needs to be consistent with our schema in the same transaction.

### Files API for bulk JSONL upload

When SyncApp moves to `bulkOperationRunMutation` (Sprint 6) for mass operations (e.g. "rebalance all inventory across 50,000 variants in one workspace"), the JSONL input has to be uploaded via `stagedUploadsCreate` with `resource: BULK_MUTATION_VARIABLES`. Standard two-step flow.

---

## Quick reference cheat sheet

| Need | Use |
|---|---|
| Read >1000 records | `bulkOperationRunQuery` + JSONL parse |
| Read <1000 records | Paginated query with `first: 250` |
| Write to inventory | `inventorySetQuantities` / `inventoryAdjustQuantities` with `@idempotent` |
| Write to >100 records | `bulkOperationRunMutation` |
| Store per-shop app config | `AppInstallation` metafields, `$app:` namespace |
| Upload a file > a few MB | `stagedUploadsCreate` → HTTP PUT → `fileCreate` |
| Upload a small file from a URL | `fileCreate` with external `originalSource` |
| Check rate limit headroom | `extensions.cost.throttleStatus` on every response |
| Handle 429 | Wait `Retry-After` or 1s, exponential backoff |
| Handle `userErrors[].code: CHANGE_FROM_QUANTITY_STALE` | Re-read, recompute, retry once |
| Dedup webhook delivery | `X-Shopify-Webhook-Id` header (NOT payload `id`) |
| Pin API version | URL path: `/admin/api/2026-04/graphql.json` |
| Detect version fallback | `X-Shopify-API-Version` response header |
| Get a token without redirect | OAuth token exchange (`urn:ietf:params:oauth:grant-type:token-exchange`) |
| Refresh an expiring offline token | `grant_type=refresh_token` to `/admin/oauth/access_token` |

## External links

- API reference index: https://shopify.dev/docs/api/admin-graphql/latest
- Changelog: https://shopify.dev/changelog
- Bulk operations guide (queries): https://shopify.dev/docs/api/usage/bulk-operations/queries
- Rate limits: https://shopify.dev/docs/api/usage/rate-limits
- Cost calculation: https://shopify.dev/docs/api/usage/calculating-rate-limits
- Pagination: https://shopify.dev/docs/api/usage/pagination-graphql
- API versioning: https://shopify.dev/docs/api/usage/versioning
- Response codes: https://shopify.dev/docs/api/usage/response-codes
- Authentication: https://shopify.dev/docs/api/usage/authentication
- Access scopes: https://shopify.dev/docs/api/usage/access-scopes
- Token exchange: https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens
- Metafields: https://shopify.dev/docs/apps/build/custom-data/metafields
- Files: https://shopify.dev/docs/api/admin-graphql/latest/objects/File
- Staged uploads: https://shopify.dev/docs/api/admin-graphql/latest/mutations/stagedUploadsCreate
