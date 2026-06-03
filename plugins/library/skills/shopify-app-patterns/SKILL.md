---
name: shopify-app-patterns
description: Battle-tested architectural patterns for production Shopify apps. Covers multi-tenancy (workspace-level billing across N shops), OAuth + reinstall idempotency, webhook handling (HMAC, dedup via X-Shopify-Webhook-Id, raw-event storage + worker normalization, GDPR cascades), sync engines (self-push suppression for write-back loops, compare-and-set inventory writes, distributed locks per resource, fulfillment-service location exclusion), rate limiting (cost-based throttling, circuit breakers, per-shop quotas), BullMQ + Redis topology (queue isolation, plan-tier priority, dirty-flush debounce), and Shopify Billing API integration including comp/redeem-code overrides. Use when designing or debugging any non-trivial Shopify app — especially multi-shop, sync-engines, write-back-causing webhooks, or anything where correctness under concurrency matters.
---

# Shopify App Architectural Patterns

Patterns that survived production for a multi-store inventory sync app. Each is non-obvious — every one came from a real bug I hit and fixed.

## When to load this skill

- Designing a new Shopify app from scratch (use this as the architecture template)
- Debugging an existing Shopify app that's leaking data, overselling, double-pushing, or losing webhooks
- Adding multi-shop support to a single-shop app
- Adding webhooks that trigger writes (read about self-push suppression FIRST)
- Inventory writes against multiple stores (compare-and-set is non-negotiable)
- Touching billing for any plan structure beyond "single tier per shop"
- Onboarding a teammate to how a real Shopify app should be structured

## File map

| Topic | File | When to consult |
|---|---|---|
| Multi-tenancy: workspaces, primary billing shop, workspace-level scoping, RBAC | [`multi-tenancy.md`](multi-tenancy.md) | Designing any app where multiple Shopify shops belong to one merchant/account, or where one subscription covers several shops |
| OAuth, `afterAuth` idempotency, reinstall handling, token encryption | [`oauth-and-session.md`](oauth-and-session.md) | Anything in `shopify.server.ts` afterAuth hook, install/uninstall lifecycle, token storage |
| Webhook ingestion: HMAC, dedup, raw event storage, normalization workers, GDPR | [`webhooks.md`](webhooks.md) | Any new webhook subscription, debugging missed events or duplicate processing |
| Sync engines: self-push suppression, CAS, locks, FS exclusion, idempotency keys | [`sync-engines.md`](sync-engines.md) | Anything where you write to Shopify and Shopify echoes back a webhook (inventory, metafields, product updates) |
| Shopify Billing API integration: tiered plans, downgrades, comp/redeem codes, workspace billing | [`billing.md`](billing.md) | Designing or modifying billing flow, supporting free tiers without going through Shopify Billing |
| Rate limiting + circuit breaker + cost-based throttling | [`rate-limiting.md`](rate-limiting.md) | Hot-path mutations, large imports, bulk operations, anywhere Shopify might throttle |
| BullMQ + Redis topology, PM2 process layout, dirty-flush debounce | [`workers.md`](workers.md) | Designing the worker layer, isolating queues, scheduling background work, plan-tier prioritization |

## The 10 patterns every Shopify app should use

These are the patterns that catch real bugs. Internalize before designing anything.

### 1. Workspace-level billing for multi-shop apps

Don't bill per-shop if your app value is per-workspace. Designate one shop as `primaryBillingShopId`, others inherit the plan. Reviewers love this; merchants do too. See [`multi-tenancy.md`](multi-tenancy.md).

### 2. Encrypt access tokens at rest

AES-256-GCM with a 64-hex-char key from env (`TOKEN_ENCRYPTION_KEY`). Even with Postgres-level encryption, plaintext tokens in a SQL log dump is a breach. Wrap your session storage:

```ts
import { RefreshingPrismaSessionStorage } from "./lib/session-storage.server";
// Custom storage that encrypts/decrypts on every read/write
```

### 3. afterAuth must be idempotent

The Shopify SDK fires `afterAuth` **twice** in cluster mode due to a session-establishment race. Both invocations create-or-update the same shop. If you `setTimeout(() => importProducts(), 3000)`, both timers fire and you do the import twice — wasting Shopify quota.

Mitigation: enqueue post-install work to BullMQ with `jobId = shopId`. Duplicate enqueues with the same jobId are silently dropped.

### 4. Webhook deduplication via `X-Shopify-Webhook-Id`

Shopify can deliver the same event multiple times (their retry logic + Redis cluster timeouts). The `X-Shopify-Webhook-Id` header is the canonical dedup key. SETNX into Redis with 60-120s TTL; skip if already seen.

For inventory webhooks specifically, the payload has no top-level `id` — `payload.id` is undefined and naive code falls back to `Date.now()` which silently never dedupes. Build an `eventKey` from topic + relevant payload fields.

### 5. Self-push suppression for inventory writes

When your app writes inventory to Shopify, Shopify fires an `inventory_levels/update` webhook to your app with the new value. Without suppression, you process that webhook and treat it as a merchant change → trigger another sync → write again → infinite loop.

Pattern: before every Shopify write, `SET inv:{item}:{loc}:{value}` in Redis with 60s TTL. Webhook handler checks `EXISTS inv:{item}:{loc}:{value}`; if yes, this is our own echo → skip. See [`sync-engines.md`](sync-engines.md).

### 6. Compare-and-set on inventory writes

Use Shopify's `changeFromQuantity` parameter on `inventorySetQuantities`. Read current quantity immediately before write; pass it as `changeFromQuantity`. If the real value differs (race with a concurrent sale or another app), Shopify rejects the write with a userError instead of overwriting silently.

Pass `changeFromQuantity: null` only on merchant-initiated UI flows where the merchant explicitly wants to set the number regardless. Background syncs MUST use real CAS.

### 7. Exclude fulfillment-service locations from inventory writes

Shopify rejects `SET_INVENTORY_QUANTITIES` against locations where `isFulfillmentService = true` (App-managed locations like Shopify Fulfillment Network, third-party 3PLs). Trying to write returns userErrors.

Pattern: every location query includes `isFulfillmentService` field; every redistribution algorithm filters those out. The merchant can SEE FS stock; you just can't write to it.

### 8. Distributed lock per resource for sync operations

Per-resource (per-barcode-group, per-product, per-customer) Redis SETNX lock prevents concurrent syncs from corrupting state. Lua-atomic release. TTL longer than expected operation. Always release in `finally`.

### 9. GraphQL Admin API only — no REST

As of April 2025. Even one REST call gets you rejected. Use `@shopify/shopify-app-remix` `admin.graphql()` throughout. Centralize mutation/query strings in `app/graphql/mutations.ts` and `app/graphql/queries.ts` for DRY.

### 10. API version pinned in three places consistently

`shopify.app.toml` `api_version`, `shopify.server.ts` `ApiVersion.AprilXX`, and GraphQL `endpoint /admin/api/{version}/graphql.json` — all three must match. Mismatch causes silent field-removed errors when Shopify deprecates fields between versions.

Current: `2026-04` (April26). Check `shopify.dev/docs/api/admin-graphql/latest` for the actively-recommended version.

## Quick-start: file structure for a new Shopify app

```
app/
├── graphql/
│   ├── queries.ts          # Centralized GraphQL queries
│   └── mutations.ts        # Centralized mutations (every @idempotent mutation here)
├── lib/
│   ├── db.server.ts        # Prisma singleton
│   ├── redis.server.ts     # 3 Redis pools: default, queues, locks
│   ├── encryption.server.ts# AES-256-GCM for token storage
│   ├── lock.server.ts      # Per-resource distributed lock (Lua-atomic release)
│   ├── logger.server.ts    # Pino structured logger, no console.log
│   ├── role.server.ts      # RBAC: requireRole(shop, "manager")
│   └── session-storage.server.ts  # Encrypted Prisma session storage
├── routes/
│   ├── webhooks.tsx        # Single HMAC-verifying endpoint, dispatches by topic
│   ├── app.tsx             # Embedded shell, authenticate.admin gate
│   └── app.*.tsx           # Pages
├── services/
│   ├── rate-limiter.server.ts  # Cost-based throttling per shop
│   └── ... domain services
├── workers/
│   ├── queues.server.ts    # BullMQ queue factory + types
│   ├── webhook-normalize.worker.ts  # Parses raw events, enqueues sync
│   ├── sync.worker.ts      # The main sync engine
│   └── ... per WORKER_TYPE
├── shopify.server.ts       # @shopify/shopify-app-remix entry + afterAuth
└── root.tsx                # App Bridge script as FIRST <script> in <head>

prisma/
└── schema.prisma           # Workspace + Shop + per-resource models

shopify.app.toml            # API version, scopes, embedded=true, webhooks
ecosystem.config.cjs        # PM2: web cluster + N worker types
```

## Production deployment topology

For 10k+ active stores at the worst-case webhook rate, a single 32-vCPU VDS handles it if:

- **PgBouncer** in transaction-pool mode in front of Postgres (`default_pool_size=50-80`, `max_client_conn=2000`)
- **Redis** with `maxmemory` + `noeviction` for queue keys
- **PM2 cluster mode** for web (`instances: 8` on 32 vCPUs)
- **Separate PM2 apps per `WORKER_TYPE`**: webhook (×8), sync (×4), reconciliation (×2), misc (×1)
- **BullMQ priority** based on plan tier (Enterprise=1, Starter=20)
- **Backpressure**: pause webhook-normalize queue when Postgres slow-query rate spikes

See [`workers.md`](workers.md) for full topology.

## Anti-patterns I've seen get apps rejected or break in prod

- **Polling instead of webhooks** — Shopify rate-limits aggressively. Webhooks are mandatory.
- **REST API for one "convenient" endpoint** — see #9. Even one call fails review.
- **Plaintext tokens in DB** — breach magnet.
- **`afterAuth` does sync work inline** — blocks OAuth response, double-fires on cluster races.
- **Per-shop session-stored caches** — break in cluster mode. Use Redis.
- **No webhook HMAC verification** — anyone can hit `/webhooks` and inject events.
- **No webhook dedup** — Shopify retries; you process N times.
- **No self-push suppression** — infinite loop the first time you write to Shopify.
- **Blocking on Shopify API in webhook handlers** — webhooks have a 5s ACK timeout; do parsing inline, push real work to a queue.
- **Storing customer PII you don't need** — escalates you from Level 0 to Level 2 protected customer data.
