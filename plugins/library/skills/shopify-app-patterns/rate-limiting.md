# Rate Limiting: Cost-Based Throttling, Circuit Breaker, Per-Shop Quotas

Shopify's GraphQL Admin API uses cost-based rate limiting (leaky bucket). Every response carries `extensions.cost` showing what the request "cost" and how much budget is left. Naive request-rate limits don't work — you need to track cost.

## How Shopify's leaky bucket works

- Each shop has a per-shop bucket with `currentlyAvailable` cost units
- Standard plan: bucket capacity = 1,000, restore rate = 50/sec
- Shopify Plus: 10x — capacity = 10,000, restore rate = 500/sec
- Every query costs roughly: small read = 1, complex read = 5-10, mutation = 10, bulk operation = much more
- Reaching 0 returns `THROTTLED` GraphQL error
- The bucket refills `restore_rate` units per second

The response includes the cost info:

```json
{
  "data": { ... },
  "extensions": {
    "cost": {
      "requestedQueryCost": 11,
      "actualQueryCost": 11,
      "throttleStatus": {
        "maximumAvailable": 1000,
        "currentlyAvailable": 989,
        "restoreRate": 50
      }
    }
  }
}
```

## The rate limiter wrapper

Centralize every Shopify API call through one wrapper that:

1. Tracks the bucket level per shop (Redis)
2. Pre-flight checks if requested cost would deplete the bucket
3. Sleeps adaptively when the bucket is low
4. Handles `THROTTLED` with exponential backoff
5. Tracks the actual cost from the response to update local estimate

```ts
// app/services/rate-limiter.server.ts
import { getRedis } from "../lib/redis.server";
import { createLogger } from "../lib/logger.server";

const log = createLogger("rate-limiter");

interface ThrottleStatus {
  maximumAvailable: number;
  currentlyAvailable: number;
  restoreRate: number;
}

export class CircuitBreakerOpenError extends Error {}

const CIRCUIT_BREAKER_THRESHOLD = 3;     // consecutive failures
const CIRCUIT_BREAKER_COOLDOWN = 300_000; // 5 min

async function getBucketState(shopId: string): Promise<ThrottleStatus | null> {
  const redis = getRedis();
  const raw = await redis.get(`bucket:${shopId}`);
  if (!raw) return null;
  return JSON.parse(raw);
}

async function setBucketState(shopId: string, state: ThrottleStatus) {
  const redis = getRedis();
  await redis.set(`bucket:${shopId}`, JSON.stringify(state), "EX", 60);
}

async function checkCircuitBreaker(shopId: string): Promise<void> {
  const redis = getRedis();
  const openUntil = await redis.get(`circuit:${shopId}:open_until`);
  if (openUntil && parseInt(openUntil, 10) > Date.now()) {
    throw new CircuitBreakerOpenError(`Circuit open for shop ${shopId}`);
  }
}

async function recordFailure(shopId: string) {
  const redis = getRedis();
  const count = await redis.incr(`circuit:${shopId}:failures`);
  await redis.expire(`circuit:${shopId}:failures`, 600);
  if (count >= CIRCUIT_BREAKER_THRESHOLD) {
    await redis.set(`circuit:${shopId}:open_until`, String(Date.now() + CIRCUIT_BREAKER_COOLDOWN), "EX", 600);
    log.warn({ shopId, count }, "Circuit breaker opened");
  }
}

async function recordSuccess(shopId: string) {
  const redis = getRedis();
  await redis.del(`circuit:${shopId}:failures`);
}

export async function rateLimitedShopifyFetch<T>(
  shopId: string,
  shopDomain: string,
  query: string,
  variables: any,
  estimatedCost: number = 10
): Promise<T> {
  await checkCircuitBreaker(shopId);

  // Pre-flight: if bucket is low, sleep
  const bucket = await getBucketState(shopId);
  if (bucket && bucket.currentlyAvailable < estimatedCost * 2) {
    const deficit = estimatedCost * 2 - bucket.currentlyAvailable;
    const sleepMs = (deficit / bucket.restoreRate) * 1000;
    if (sleepMs > 0 && sleepMs < 30_000) {
      log.info({ shopId, available: bucket.currentlyAvailable, sleepMs }, "Pre-flight bucket low — sleeping");
      await new Promise((r) => setTimeout(r, sleepMs));
    }
  }

  // Get session token, build request
  const session = await getSessionForShop(shopId);
  const accessToken = session.accessToken;

  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(`https://${shopDomain}/admin/api/2026-04/graphql.json`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": accessToken,
      },
      body: JSON.stringify({ query, variables }),
    });
    const body = await res.json();

    // Update bucket state from response
    const throttle = body?.extensions?.cost?.throttleStatus;
    if (throttle) await setBucketState(shopId, throttle);

    // Check for THROTTLED
    const throttled = body?.errors?.find((e: any) => e.extensions?.code === "THROTTLED");
    if (throttled) {
      const wait = Math.min(2 ** attempt * 1000, 10_000);
      log.warn({ shopId, attempt, wait }, "Shopify THROTTLED — backing off");
      await new Promise((r) => setTimeout(r, wait));
      continue;
    }

    if (!res.ok) {
      await recordFailure(shopId);
      throw new Error(`Shopify API ${res.status}: ${JSON.stringify(body)}`);
    }

    await recordSuccess(shopId);
    return body as T;
  }

  await recordFailure(shopId);
  throw new Error(`Shopify API throttled after 3 retries (shop ${shopId})`);
}
```

## Adaptive backoff in the response

After every successful request, peek at `currentlyAvailable`. If it's low, slow down BEFORE the next request:

```ts
const throttleInfo = body?.extensions?.cost?.throttleStatus;
if (throttleInfo) {
  const remaining = throttleInfo.currentlyAvailable || 0;
  if (remaining < 100) {
    log.warn({ shopId, remaining }, "Low API budget — backing off 2s");
    await new Promise((r) => setTimeout(r, 2000));
  } else if (remaining < 200) {
    await new Promise((r) => setTimeout(r, 500));
  }
}
```

This adaptive pacing keeps your overall throughput high without ever hitting THROTTLED in the steady state.

## Per-workspace quota layer

A workspace with 50 shops can DOS your own limiter — 50 shops × 50 cost/sec = 2,500 cost/sec across the workspace, which overwhelms downstream resources (Postgres connection pool, Redis bandwidth). Layer a per-workspace quota ON TOP of the per-shop bucket:

```ts
async function checkWorkspaceQuota(workspaceId: string, cost: number) {
  const redis = getRedis();
  const key = `wsquota:${workspaceId}:${Math.floor(Date.now() / 1000)}`;
  const current = await redis.incrby(key, cost);
  await redis.expire(key, 5);
  if (current > 500) {  // 500 cost/sec/workspace max
    throw new Error("Workspace API budget exceeded");
  }
}
```

## Circuit breaker auto-recovery

When a shop's circuit opens (3 consecutive failures), it stays open for 5 min. After cooldown, the next request retries; if it succeeds, the failure counter resets and the breaker closes.

For visibility, emit a metric every time the breaker opens or closes:

```ts
import { circuitBreakerOpensTotal } from "../lib/metrics.server";
circuitBreakerOpensTotal.inc({ shop_id: shopId });
```

This gets you a chart of "shops in trouble" — usually the same handful that have OAuth issues, deleted webhook endpoints, or invalid tokens.

## Bulk Operations for large catalog imports

For >1,000 product or variant operations, use Shopify's Bulk Operations API instead of paginated queries:

```graphql
mutation {
  bulkOperationRunQuery(query: "{ products { edges { node { id title variants { edges { node { id sku barcode }}}}}}}") {
    bulkOperation { id status }
    userErrors { field message }
  }
}
```

Then poll `currentBulkOperation` until status = COMPLETED, fetch the JSONL output URL, and stream-parse it. One bulk op replaces hundreds of paginated requests and counts as a much smaller cost slice.

Naming convention: rename queries that LOOK like mutations:

```graphql
# Bad — looks like a mutation
const BULK_PRODUCTS_MUTATION = "mutation { bulkOperationRunQuery(...) ...}";

# Good — explicit
const BULK_PRODUCTS_QUERY = "mutation { bulkOperationRunQuery(...) ... }";
```

(The wrapper IS a mutation but the operation it runs is a query — name it for what it fetches.)

## Self-push echo budget per shop

If your own writes echo back as webhooks and you fail to suppress one, you get a runaway loop. Track per-shop echo rate and break the loop after a threshold:

```ts
async function recordEcho(shopId: string) {
  const redis = getRedis();
  const key = `echo:${shopId}:${Math.floor(Date.now() / 60_000)}`; // per-minute bucket
  const count = await redis.incr(key);
  await redis.expire(key, 120);
  if (count > 50) {
    // Emergency: suppress ALL inventory webhooks for this shop for 5 min to break the loop
    await redis.set(`runaway:${shopId}`, "1", "EX", 300);
    log.warn({ shopId, count }, "Echo budget exceeded — runaway suppression engaged");
  }
}
```

In the webhook handler, check `runaway:${shopId}` before processing.

## Pitfalls

- **No rate limiter at all** — hits THROTTLED constantly; user-facing requests fail randomly.
- **Tracking request count instead of cost** — Shopify's bucket is cost-based; request count is meaningless.
- **No circuit breaker** — a misbehaving shop's failures hammer your worker pool.
- **Holding sync locks across rate-limited calls** — sleep happens inside the lock; other workers blocked unnecessarily. Acquire lock → quick check → release → call Shopify with retry → re-acquire if needed.
- **Per-shop limit only (no workspace quota)** — multi-shop workspaces DoS themselves.
- **No echo budget** — first self-push suppression bug causes infinite loop.
- **Bulk operations from a webhook handler** — they're async and take minutes; the webhook handler must return 200 in <5s.
