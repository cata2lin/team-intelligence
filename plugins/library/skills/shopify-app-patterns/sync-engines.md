# Sync Engines: Self-Push Suppression, CAS, Locks, FS Exclusion, Idempotency

If your app writes to Shopify and Shopify writes back via webhooks, you need a sync engine that doesn't oversell, doesn't loop forever, and doesn't corrupt state under concurrency.

## The webhook loop problem

```
Your app writes to Shopify
  → Shopify fires inventory_levels/update webhook
    → Your app receives webhook, sees "stock changed!", triggers sync
      → Your app writes to Shopify again
        → Shopify fires another webhook
          → ... ∞
```

Without explicit suppression, the first write causes an infinite loop. The first time an app developer hits this in prod, it's a 10k requests/minute outage.

## Self-push suppression pattern

Before every write, mark the (inventoryItem, location, value) triple as "self-originated" in Redis with a 60s TTL. The webhook handler checks this mark and skips the echo.

```ts
// app/lib/sync-origin.server.ts
import { getRedis } from "./redis.server";

const TTL_SECONDS = 60;

export async function markAsSelfPush(
  inventoryItemGid: string,
  locationGid: string,
  newValue: number,
  shopId?: string
): Promise<void> {
  const redis = getRedis();
  // Extract numeric IDs from GIDs for tight keys
  const itemId = inventoryItemGid.replace("gid://shopify/InventoryItem/", "");
  const locId = locationGid.replace("gid://shopify/Location/", "");
  const key = `selfpush:${itemId}:${locId}:${newValue}`;
  await redis.set(key, "1", "EX", TTL_SECONDS);

  // Sprint 6: track echo budget per shop for runaway detection
  if (shopId) {
    const counterKey = `echo:${shopId}:${Math.floor(Date.now() / 60000)}`; // per-minute bucket
    await redis.incr(counterKey);
    await redis.expire(counterKey, 120);
  }
}

export async function isSelfPushAsync(
  inventoryItemId: number | string,
  locationId: number | string,
  available: number,
  shopId?: string
): Promise<boolean> {
  const redis = getRedis();
  const key = `selfpush:${inventoryItemId}:${locationId}:${available}`;
  const exists = await redis.exists(key);
  if (exists) return true;

  // Sprint 6 echo budget: if a single shop has >50 echoes in a minute,
  // something is wrong (likely a self-push key mismatch). Eagerly suppress
  // all of that shop's inventory webhooks for the next 5 min to break the loop.
  if (shopId) {
    const counterKey = `echo:${shopId}:${Math.floor(Date.now() / 60000)}`;
    const count = parseInt((await redis.get(counterKey)) || "0", 10);
    if (count > 50) {
      log.warn({ shopId, count }, "Echo budget exceeded — runaway suppression engaged");
      return true;
    }
  }
  return false;
}
```

**Usage in the write path**:

```ts
async function pushInventoryToShopify(item, location, newValue, shopId, shopDomain) {
  // 1. Mark BEFORE writing
  await markAsSelfPush(item.gid, location.gid, newValue, shopId);

  // 2. Write to Shopify
  await rateLimitedShopifyFetch(shopId, shopDomain, SET_INVENTORY_QUANTITIES, {
    input: {
      reason: "correction",
      name: "available",
      quantities: [{ inventoryItemId: item.gid, locationId: location.gid, quantity: newValue, changeFromQuantity: currentValue }],
    },
    idempotencyKey: newIdempotencyKey("sync"),
  });
}
```

**Usage in the webhook handler**:

```ts
async function handleInventoryUpdate(shopId, workspaceId, payload) {
  if (await isSelfPushAsync(payload.inventory_item_id, payload.location_id, payload.available, shopId)) {
    return; // our own echo, don't react
  }
  // ... process as a real merchant change
}
```

## Compare-and-set (CAS) on inventory writes

Use Shopify's `changeFromQuantity` field. Read the current value immediately before writing; pass it. If a concurrent edit happened between your read and write, Shopify rejects with a userError instead of silently overwriting.

```ts
// 1. Read current value from Shopify (or use a cached value if you have one fresh)
const currentBody = await rateLimitedShopifyFetch(shopId, shopDomain, READ_CURRENT_QUANTITY, {
  inventoryItemId, locationId,
});
const currentAvailable = currentBody.data?.inventoryItem?.inventoryLevel?.quantities
  .find((q: any) => q.name === "available")?.quantity;

// 2. Push with CAS
const body = await rateLimitedShopifyFetch(shopId, shopDomain, SET_INVENTORY_QUANTITIES, {
  input: {
    reason: "correction",
    name: "available",
    quantities: [{
      inventoryItemId, locationId,
      quantity: newValue,
      changeFromQuantity: currentAvailable, // CAS guard
    }],
  },
  idempotencyKey: newIdempotencyKey("sync"),
});

const userErrors = body.data?.inventorySetQuantities?.userErrors;
if (userErrors?.length) {
  // CAS failed — another write got in between. Mark allocation as failed and retry on next sync.
  throw new Error(userErrors[0].message);
}
```

### When to skip CAS

For **merchant-initiated UI flows** where the merchant explicitly says "set to N", pass `changeFromQuantity: null` to bypass CAS. The merchant clicked "Set Pool to 50" and they want it to happen even if the local cache is a few seconds stale.

For **background sync** (webhooks, reconciliation, scheduled syncs), ALWAYS use real CAS. If state changed mid-sync, the safe thing is to fail and retry, not blindly overwrite.

```ts
// Shopify 2026-04: the field is MANDATORY but the value may be null to skip CAS
// "you must explicitly pass in a value, even if that value is null"
quantities: [{
  inventoryItemId, locationId, quantity: value,
  changeFromQuantity: null, // present, but null = skip CAS
}]
```

## Distributed lock per resource

Sync operations must be serialized per resource (per barcode-group / per product / per customer). Without a lock, concurrent syncs race and corrupt allocations.

Redis SETNX with Lua-atomic release:

```ts
// app/lib/lock.server.ts
import { getRedisForLocks } from "./redis.server";
import crypto from "node:crypto";

const RELEASE_SCRIPT = `
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
`;

export async function acquireLock(
  workspaceId: string,
  resourceId: string,
  ttlSec: number = 120,
  timeoutMs: number = 30_000
): Promise<{ release: () => Promise<void> } | null> {
  const redis = getRedisForLocks();
  const key = `lock:${workspaceId}:${resourceId}`;
  const token = crypto.randomBytes(16).toString("hex");
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const ok = await redis.set(key, token, "EX", ttlSec, "NX");
    if (ok) {
      return {
        release: async () => {
          // Lua-atomic: only release if WE hold it
          await redis.eval(RELEASE_SCRIPT, 1, key, token);
        },
      };
    }
    await new Promise((r) => setTimeout(r, 100 + Math.random() * 200));
  }
  return null;
}

export async function extendLock(...): Promise<void> {
  // Similar Lua check + EXPIRE for long-running sync ops
}
```

**Use in sync orchestrator**:

```ts
const lock = await acquireLock(workspaceId, barcodeGroupId, 120, 30_000);
if (!lock) {
  // Another worker is syncing this group. Skip; rely on dirty-flush to retry.
  return { status: "lock_skipped" };
}
try {
  // ... sync work
} finally {
  try { await lock.release(); } catch (err) { log.warn({err}, "lock release failed (will TTL)"); }
}
```

### Critical: always release in `finally`

If `release()` throws (Redis hiccup), the lock TTLs out — but other workers are blocked until it does. Optional-chain the release and log warnings:

```ts
finally {
  try {
    await lock?.release?.();
  } catch (err) {
    log.warn({ resource: barcodeGroupId, err: String(err) }, "Lock release failed — will expire via TTL");
  }
}
```

## Fulfillment-service location exclusion

Shopify rejects `SET_INVENTORY_QUANTITIES` against locations where `isFulfillmentService = true` ("App-managed" — Shopify Fulfillment Network, third-party 3PLs, etc.). Trying to write returns userErrors.

The merchant can still SEE FS stock; you just can't write to it. Every redistribution algorithm must filter FS levels out before computing targets:

```ts
const group = await db.barcodeGroup.findUnique({
  where: { id: barcodeGroupId },
  include: {
    inventoryLevels: {
      include: { location: { select: { isFulfillmentService: true }}},
    },
  },
});

// FS-aware pool: only redistributable levels count
const redistributableLevels = group.inventoryLevels.filter(
  (il) => !il.location?.isFulfillmentService
);
const pool = redistributableLevels.reduce((sum, il) => sum + il.availableQuantity, 0);

// FS-managed quantity for display only
const fsManaged = group.inventoryLevels
  .filter((il) => il.location?.isFulfillmentService)
  .reduce((sum, il) => sum + il.availableQuantity, 0);
```

Belt-and-suspenders in the push step too — skip FS locations even if an old allocation row points at one:

```ts
const fsLocationIds = new Set(locations.filter(l => l.isFulfillmentService).map(l => l.id));
if (fsLocationIds.has(alloc.locationId)) {
  log.info({ shopId, locationId: alloc.locationId }, "Skipping FS-managed location");
  continue;
}
```

## Idempotency keys on mutations

Shopify 2026-04 requires the `@idempotent` directive on 18 specific mutations. The directive takes an idempotency key. Repeat calls with the same key return the same response without re-executing.

```ts
// Generate per-operation key
import crypto from "node:crypto";
export function newIdempotencyKey(prefix: string): string {
  return `${prefix}_${crypto.randomBytes(12).toString("hex")}`;
}

// Usage
await rateLimitedShopifyFetch(shopId, shopDomain, SET_INVENTORY_QUANTITIES, {
  input: { ... },
  idempotencyKey: newIdempotencyKey("sync"), // <-- @idempotent variable
});
```

If you retry the same sync, regenerate the key — you DO want the retry to take effect. The key isn't for our retry semantics; it's so Shopify can dedupe accidental dupes on their side.

## Dirty-flush debounce

Webhook-triggered work shouldn't sync inline. A merchant bulk-edit can fire 200 inventory webhooks in 5 seconds; syncing each one would hit rate limits.

Instead, mark dirty + debounce:

```ts
export async function markDirty(workspaceId: string, resourceId: string, reason: string) {
  await db.dirtyResource.upsert({
    where: { workspaceId_resourceId: { workspaceId, resourceId }},
    create: { workspaceId, resourceId, reason, createdAt: new Date() },
    update: { reason, createdAt: new Date() },
  });

  // Also enqueue a debounced sync job for near-realtime
  await getSyncTriggerQueue().add(
    `sync-${resourceId}`,
    { resourceId, workspaceId, reason },
    {
      jobId: `debounced-${resourceId}`,
      delay: 3000,  // 3s window for burst events to settle
    }
  );
}
```

The `jobId` ensures BullMQ collapses bursts of marks into one sync job. The 3s delay lets the merchant's bulk edit complete before sync runs.

Also have a periodic "dirty-flush" job (every 2 minutes) that picks up any `DirtyResource` rows older than 30s and syncs them — safety net for the case where the debounced sync failed for some reason.

## Common pitfalls

- **No self-push suppression** — infinite loop on first write.
- **Suppression key mismatch** — e.g., comparing `inventory_item_id` (number) against `gid://shopify/InventoryItem/123` (string). Normalize one side.
- **Marking self-push AFTER the write** — webhook can arrive before the mark, missing it. Mark BEFORE.
- **Skipping CAS in background sync** — concurrent merchant edits get silently overwritten.
- **Holding lock across long Shopify calls** — TTL expires; another worker starts a duplicate sync. Use `extendLock` for long ops.
- **Releasing lock outside `finally`** — exception leaks the lock for full TTL.
- **Not excluding FS locations** — every push gets userError; logs fill up; allocations stuck "failed".
- **Inline sync from webhook handler** — burst of webhooks → burst of syncs → rate-limited → circuit breaker opens → outage.
