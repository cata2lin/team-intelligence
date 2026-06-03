# Webhook Ingestion: HMAC, Deduplication, Raw-Event Storage, Normalization Workers

The webhook endpoint is the hot path. It MUST ack in <5s (Shopify times out otherwise) and survive duplicate deliveries, malformed payloads, and traffic spikes.

## The architecture

```
Shopify
   │
   ▼ POST /webhooks (HMAC + X-Shopify-Webhook-Id headers)
   │
┌──────────────────────────────────────────────┐
│  Web tier (Remix action)                     │
│  1. HMAC verify (via authenticate.webhook)   │
│  2. Build eventKey                           │
│  3. SETNX dedup in Redis (60-120s TTL)       │
│  4. Enqueue raw payload to BullMQ            │
│  5. Return 200                                │
└──────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────┐
│  Normalize worker (BullMQ)                   │
│  1. Parse payload                            │
│  2. Persist RawWebhookEvent (audit trail)    │
│  3. Update domain DB rows                    │
│  4. Check self-push markers                  │
│  5. Enqueue downstream sync work             │
└──────────────────────────────────────────────┘
```

The web tier never does the actual work. It just verifies, dedupes, and enqueues. This keeps the `/webhooks` p99 under 100ms even under burst load.

## HMAC verification

Use `@shopify/shopify-app-remix`:

```ts
export const action = async ({ request }: Route.ActionArgs) => {
  const { topic, shop, payload, webhookId } = await authenticate.webhook(request);
  // authenticate.webhook throws if HMAC fails — returns 401 automatically
  // ...
};
```

If you must roll your own (rare):

```ts
import crypto from "node:crypto";

function verifyHmac(rawBody: string, header: string, secret: string): boolean {
  const computed = crypto.createHmac("sha256", secret).update(rawBody, "utf8").digest("base64");
  return crypto.timingSafeEqual(Buffer.from(computed), Buffer.from(header));
}
```

Use constant-time compare (`timingSafeEqual`). Standard `===` leaks timing info that's exploitable.

## Deduplication: `X-Shopify-Webhook-Id` first

Shopify can deliver the same webhook event multiple times. Their docs say "at least once". Real causes:

- Their internal retry on transient errors
- Redis cluster failover in their delivery infrastructure
- Cross-region replication delays

The **canonical** dedup key is the `X-Shopify-Webhook-Id` header. SETNX into Redis with a generous TTL:

```ts
const webhookId = request.headers.get("x-shopify-webhook-id");
if (webhookId) {
  const redis = getRedisForQueues();
  const ok = await redis.set(`webhook:seen:${webhookId}`, "1", "EX", 120, "NX");
  if (!ok) {
    // Already processed this exact delivery
    return new Response(null, { status: 200 });
  }
}
```

### Topic-specific composite keys

Some topics don't have a useful root `id` field on the payload. Inventory webhooks are the worst offender:

```json
{
  "inventory_item_id": 123,
  "location_id": 456,
  "available": 10,
  "updated_at": "..."
}
```

No top-level `id`. Naive code like `jobId: payload.id || Date.now()` falls back to a unique timestamp on every delivery and **never dedupes**.

For these topics, build an `eventKey` from the meaningful fields:

```ts
function buildEventKey(topic: string, payload: any): string {
  switch (topic) {
    case "INVENTORY_LEVELS_UPDATE":
      return `inv:${payload.inventory_item_id}:${payload.location_id}:${payload.available}:${payload.updated_at}`;
    case "PRODUCTS_UPDATE":
      return `prod:${payload.id}:${payload.updated_at}`;
    case "ORDERS_CREATE":
    case "ORDERS_UPDATED":
      return `ord:${payload.id}:${payload.updated_at}`;
    default:
      return `${topic}:${payload.id ?? Date.now()}`;
  }
}
```

Use `eventKey` for BullMQ jobId (so the queue dedupes even if Redis SETNX missed) AND for the `RawWebhookEvent.eventKey` column (so the DB also enforces dedup).

## Known topics list — pin it

Maintain an explicit list of topics your app subscribes to. Anything outside is logged + ignored (defense against Shopify changing topic names silently):

```ts
const KNOWN_DATA_TOPICS = new Set([
  "PRODUCTS_CREATE", "PRODUCTS_UPDATE", "PRODUCTS_DELETE",
  "INVENTORY_LEVELS_UPDATE",
  "ORDERS_CREATE", "ORDERS_UPDATED", "ORDERS_CANCELLED",
  "REFUNDS_CREATE",
  "LOCATIONS_CREATE", "LOCATIONS_UPDATE", "LOCATIONS_ACTIVATE", "LOCATIONS_DEACTIVATE",
]);

const KNOWN_LIFECYCLE_TOPICS = new Set([
  "APP_UNINSTALLED",
  "APP_SUBSCRIPTIONS_UPDATE",
]);

const KNOWN_GDPR_TOPICS = new Set([
  "CUSTOMERS_DATA_REQUEST",
  "CUSTOMERS_REDACT",
  "SHOP_REDACT",
]);

// In the switch:
default: {
  log.warn({ topic, shop }, "Received unknown webhook topic — ignoring");
  return new Response(null, { status: 200 });
}
```

Always end the switch with a `default` that logs and returns 200 — never throw on unknown topics, Shopify will mark your endpoint unhealthy.

## Raw-event storage

Persist the raw payload to a `RawWebhookEvent` table for audit + replay:

```prisma
model RawWebhookEvent {
  id          String   @id @default(cuid())
  shopDomain  String   @map("shop_domain")
  topic       String
  eventKey    String   @map("event_key")
  webhookId   String?  @map("webhook_id")  // X-Shopify-Webhook-Id header
  payload     Json
  status      String   @default("pending")  // pending | processed | failed
  errorMessage String?  @map("error_message")
  receivedAt  DateTime @default(now()) @map("received_at")
  processedAt DateTime? @map("processed_at")

  @@unique([shopDomain, eventKey])
  @@index([shopDomain, receivedAt])
  @@index([status])
  @@map("raw_webhook_events")
}
```

Retention: 7 days by default (cleanup job in misc worker). Enough for debugging a recent issue, not so much that the table grows unbounded.

## The web tier's job

The Remix `action` is intentionally minimal:

```ts
export const action = async ({ request }: Route.ActionArgs) => {
  let topic: string, shop: string, payload: any;
  try {
    ({ topic, shop, payload } = await authenticate.webhook(request));
  } catch (err) {
    log.warn({ err: String(err) }, "Webhook HMAC failed");
    return new Response(null, { status: 401 });
  }

  // Dedup by X-Shopify-Webhook-Id first (fastest)
  const webhookId = request.headers.get("x-shopify-webhook-id") ?? null;
  const eventKey = buildEventKey(topic, payload);

  // Inventory-specific tight dedup window (handles burst writes from one merchant)
  if (topic === "INVENTORY_LEVELS_UPDATE") {
    const tight = `inv:${payload.inventory_item_id}:${payload.location_id}:${payload.available}`;
    const ok = await redis.set(tight, "1", "EX", 30, "NX");
    if (!ok) return new Response(null, { status: 200 });
  }

  // GDPR webhooks: handle inline (small payload, fast)
  if (KNOWN_GDPR_TOPICS.has(topic)) {
    return handleGdprWebhook(topic, shop, payload);
  }

  // Lifecycle: handle inline (rare)
  if (KNOWN_LIFECYCLE_TOPICS.has(topic)) {
    return handleLifecycleWebhook(topic, shop, payload);
  }

  // Data webhooks: enqueue and ACK
  if (KNOWN_DATA_TOPICS.has(topic)) {
    await queue.add(
      "normalize",
      { topic, shop, eventKey, webhookId, payload },
      { jobId: eventKey, removeOnComplete: { count: 500 }}
    );
    return new Response(null, { status: 200 });
  }

  log.warn({ topic, shop }, "Unknown webhook topic");
  return new Response(null, { status: 200 });
};
```

Note `jobId: eventKey` on BullMQ — duplicate enqueues are silently dropped.

## The normalize worker

In `app/workers/webhook-normalize.worker.ts`:

```ts
const worker = new Worker("webhook-normalize", async (job) => {
  const { topic, shop, eventKey, webhookId, payload } = job.data;

  // Persist raw event (audit)
  await db.rawWebhookEvent.upsert({
    where: { shopDomain_eventKey: { shopDomain: shop, eventKey }},
    create: { shopDomain: shop, topic, eventKey, webhookId, payload, status: "pending" },
    update: {}, // Idempotent — duplicates land here too
  });

  // Resolve shopId + workspaceId
  const shopRow = await db.shop.findUnique({ where: { myshopifyDomain: shop }});
  if (!shopRow) return; // uninstalled, ignore

  // Dispatch to topic-specific handler
  try {
    switch (topic) {
      case "INVENTORY_LEVELS_UPDATE":
        await handleInventoryUpdate(shopRow.id, shopRow.workspaceId, payload);
        break;
      case "PRODUCTS_UPDATE":
        await handleProductUpsert(shopRow.id, shopRow.workspaceId, payload);
        break;
      // ... per topic
    }
    await db.rawWebhookEvent.update({
      where: { shopDomain_eventKey: { shopDomain: shop, eventKey }},
      data: { status: "processed", processedAt: new Date() },
    });
  } catch (err) {
    await db.rawWebhookEvent.update({
      where: { shopDomain_eventKey: { shopDomain: shop, eventKey }},
      data: { status: "failed", errorMessage: String(err) },
    });
    throw err; // Let BullMQ retry per its retry policy
  }
}, { connection: redis, concurrency: 25 });
```

## Self-push suppression in the handler

If your handler may trigger a Shopify write that fires another webhook to you:

```ts
async function handleInventoryUpdate(shopId, workspaceId, payload) {
  // Check if this update came from our own push
  if (await isSelfPush(payload.inventory_item_id, payload.location_id, payload.available, shopId)) {
    log.info({ shop: shopId, item: payload.inventory_item_id }, "Skipping self-push echo");
    return;
  }
  // Real merchant edit — proceed to sync
  await db.inventoryLevel.upsert({...});
  await markGroupDirty(workspaceId, barcodeGroupId, "inventory_update");
}
```

See [`sync-engines.md`](sync-engines.md) for the full self-push pattern.

## GDPR webhook handlers (don't enqueue, handle inline)

```ts
function handleGdprWebhook(topic, shop, payload) {
  switch (topic) {
    case "CUSTOMERS_DATA_REQUEST":
      // 30-day SLA — log and notify merchant async
      log.info({ shop }, "GDPR data request received");
      // ... (send email to merchant within 30 days with packaged data)
      return new Response(null, { status: 200 });

    case "CUSTOMERS_REDACT":
      // Delete any rows tied to this customer_id
      const customerId = payload?.customer?.id;
      // ... cascade delete
      return new Response(null, { status: 200 });

    case "SHOP_REDACT":
      // 48-hour SLA — cascade delete everything for the shop
      const shopRow = await db.shop.findUnique({ where: { myshopifyDomain: shop }});
      if (shopRow) {
        await db.shop.delete({ where: { id: shopRow.id }});
        // FK cascades handle child tables
      }
      return new Response(null, { status: 200 });
  }
}
```

GDPR handlers MUST be synchronous. Don't enqueue — Shopify's reviewer specifically tests these and a queue lag would fail the audit.

## Webhook subscription registration

Two ways to register webhooks:

1. **`shopify.app.toml` declarative** (preferred):

```toml
[webhooks]
api_version = "2026-04"

  [[webhooks.subscriptions]]
  topics = ["products/create", "products/update", "products/delete"]
  uri = "/webhooks"

[webhooks.privacy_compliance]
customer_data_request_url = "/webhooks"
customer_deletion_url = "/webhooks"
shop_deletion_url = "/webhooks"
```

Shopify CLI registers these on `shopify app deploy`. Idempotent.

2. **`shopify.server.ts` `afterAuth` programmatic** (for dynamic subscriptions):

```ts
const shopify = shopifyApp({
  // ...
  webhooks: {
    [DeliveryMethod.Http]: {
      INVENTORY_LEVELS_UPDATE: { callbackUrl: "/webhooks" },
      // ...
    },
  },
  hooks: { afterAuth: async ({ session }) => {
    shopify.registerWebhooks({ session });
    // ... rest of afterAuth
  }},
});
```

Use TOML for everything you can; programmatic is rarely needed unless subscriptions depend on plan tier.

## Pitfalls

- **Doing work in the request handler** — webhooks have a 5s ACK timeout. Anything beyond HMAC + enqueue must be in a worker.
- **No HMAC verification** — anyone can POST `/webhooks` and inject events.
- **Naive `jobId: payload.id`** — payload.id is undefined for inventory webhooks; falls back to randomness and never dedupes.
- **Forgetting `default` in topic switch** — throws on unknown topics; Shopify marks endpoint unhealthy.
- **Storing raw payload without TTL/retention** — table grows unbounded; eventually breaks Postgres.
- **No backpressure when normalize queue depth grows** — workers fall behind, ACK timeouts start failing. Add a backpressure monitor that pauses ingestion when DB writes slow down.
