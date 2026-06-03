# BullMQ + Redis + PM2 Worker Topology

For any Shopify app that handles webhooks, runs syncs, or schedules background jobs, the worker layer is critical. Here's the production topology that scales to thousands of shops on a single VDS.

## The three Redis pools

Isolate Redis connections by purpose so a noisy queue doesn't starve the rest:

```ts
// app/lib/redis.server.ts
import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

// Pool 0 — default reads/writes (rate limiter, session cache, dedup keys)
let _redis: Redis;
export function getRedis() {
  if (!_redis) _redis = new Redis(REDIS_URL, { maxRetriesPerRequest: null });
  return _redis;
}

// Pool 1 — BullMQ queues. Different DB index so a FLUSHDB on queues doesn't nuke locks.
let _queueRedis: Redis;
export function getRedisForQueues() {
  if (!_queueRedis) _queueRedis = new Redis(REDIS_URL, {
    maxRetriesPerRequest: null,
    db: 1,
  });
  return _queueRedis;
}

// Pool 2 — distributed locks. BullMQ requires maxRetriesPerRequest: null; locks don't.
let _lockRedis: Redis;
export function getRedisForLocks() {
  if (!_lockRedis) _lockRedis = new Redis(REDIS_URL, {
    maxRetriesPerRequest: 3,
    db: 2,
  });
  return _lockRedis;
}
```

`maxRetriesPerRequest: null` is mandatory for BullMQ — Redis disconnects must retry forever. For locks you want bounded retries so a hung Redis doesn't deadlock callers.

## Queue inventory

Typical queues for an inventory-sync app:

| Queue | Purpose | Worker concurrency | Job TTL |
|---|---|---|---|
| `webhook-normalize` | Parse raw webhook payloads, update DB rows, enqueue downstream sync | High (25-50 per worker) | Remove on complete after 500 |
| `sync-trigger` | Run the actual sync for a barcode group / product | Low (5 per worker) — each holds a lock | Keep failed for 100 |
| `auto-import` | Initial product/inventory import on first install | 1 per worker — heavy | Keep for 100 |
| `reconciliation-full` | 6-hourly full reconciliation against Shopify | 1 — long running | Keep failed for 20 |
| `reconciliation-fast` | 10-minute drift detector | 1 — long running | Keep failed for 20 |
| `snapshot` | Daily metrics snapshots | 1 — scheduled | Keep failed for 20 |
| `dirty-flush` | Periodic flush of marked-dirty resources | 1 — scheduled every 2 min | Keep failed for 20 |
| `dlq-monitor` | Cleanup + alert on dead-letter jobs | 1 — scheduled hourly | Keep failed for 20 |
| `alert-scan` | Low-stock / forecast / weekly-digest alerts | 1 — scheduled daily | Keep failed for 20 |

## Queue factory pattern

Define every queue + worker type in one file:

```ts
// app/workers/queues.server.ts
import { Queue, QueueEvents } from "bullmq";
import { getRedisForQueues } from "../lib/redis.server";

const connection = getRedisForQueues();

// Job data type for each queue
export interface WebhookNormalizeJob {
  topic: string;
  shop: string;
  eventKey: string;
  webhookId: string | null;
  payload: any;
}

export interface SyncTriggerJob {
  barcodeGroupId: string;
  workspaceId: string;
  triggeredBy: "manual" | "webhook" | "reconciliation";
  reason: string;
}

// Factory functions — lazy init so the worker process only loads what it needs
let _webhookNormalizeQueue: Queue<WebhookNormalizeJob>;
export function getWebhookNormalizeQueue() {
  if (!_webhookNormalizeQueue) {
    _webhookNormalizeQueue = new Queue<WebhookNormalizeJob>("webhook-normalize", { connection });
  }
  return _webhookNormalizeQueue;
}

let _syncTriggerQueue: Queue<SyncTriggerJob>;
export function getSyncTriggerQueue() {
  if (!_syncTriggerQueue) {
    _syncTriggerQueue = new Queue<SyncTriggerJob>("sync-trigger", { connection });
  }
  return _syncTriggerQueue;
}

// ... per queue
```

## PM2 topology

Use the `WORKER_TYPE` env var to launch different worker bundles:

```js
// ecosystem.config.cjs
module.exports = {
  apps: [
    // Web tier — cluster mode, scales with vCPUs
    {
      name: "syncapp-web",
      script: "node_modules/.bin/react-router-serve",
      args: "./build/server/index.js",
      instances: 8,
      exec_mode: "cluster",
      max_memory_restart: "1G",
      max_restarts: 10,
      min_uptime: 30000,
      env: { NODE_ENV: "production", PORT: 4000 },
      merge_logs: true,
    },

    // Webhook normalize workers — high concurrency
    {
      name: "syncapp-worker-webhook",
      script: "./app/workers/index.js",
      instances: 2,
      exec_mode: "fork",
      instance_var: "INSTANCE_ID", // BullMQ workers use this to identify themselves
      max_memory_restart: "1G",
      env: { NODE_ENV: "production", WORKER_TYPE: "webhook" },
    },

    // Sync workers — low concurrency, each holds a lock
    {
      name: "syncapp-worker-sync",
      script: "./app/workers/index.js",
      instances: 2,
      exec_mode: "fork",
      instance_var: "INSTANCE_ID",
      env: { NODE_ENV: "production", WORKER_TYPE: "sync" },
    },

    // Reconciliation — single instance
    {
      name: "syncapp-worker-reconciliation",
      script: "./app/workers/index.js",
      instances: 1,
      exec_mode: "fork",
      env: { NODE_ENV: "production", WORKER_TYPE: "reconciliation" },
    },

    // Misc — snapshots, alerts, dirty-flush, dlq-monitor
    {
      name: "syncapp-worker-misc",
      script: "./app/workers/index.js",
      instances: 1,
      exec_mode: "fork",
      env: { NODE_ENV: "production", WORKER_TYPE: "misc" },
    },
  ],
};
```

## Worker entry point

A single `app/workers/index.ts` that dispatches based on `WORKER_TYPE`:

```ts
const workerType = process.env.WORKER_TYPE;

async function main() {
  switch (workerType) {
    case "webhook":
      await import("./webhook-normalize.worker");
      break;
    case "sync":
      await import("./sync-trigger.worker");
      break;
    case "reconciliation":
      await import("./reconciliation.worker");
      await import("./fast-reconciliation.worker");
      break;
    case "misc":
      await import("./snapshot.worker");
      await import("./dirty-flush.worker");
      await import("./alert-scan.worker");
      await import("./dlq-monitor.worker");
      break;
    default:
      // Run everything in one process (dev mode)
      await import("./webhook-normalize.worker");
      await import("./sync-trigger.worker");
      // ...
  }
}
main();
```

## Plan-tier priority queueing

Enterprise customers shouldn't wait behind Starter customers when the queue is backed up. Use BullMQ's `priority` field:

```ts
// app/lib/plan-priority.server.ts
const PRIORITY_BY_PLAN: Record<string, number> = {
  "Enterprise": 1,
  "Business": 5,
  "Growth": 10,
  "Starter": 20,
};

export async function getPriorityForWorkspace(workspaceId: string): Promise<number> {
  const ws = await db.workspace.findUnique({
    where: { id: workspaceId },
    select: { plan: { select: { name: true }}},
  });
  return PRIORITY_BY_PLAN[ws?.plan?.name ?? "Starter"] ?? 20;
}

// Use when enqueueing
const priority = await getPriorityForWorkspace(workspaceId);
await getSyncTriggerQueue().add(`sync-${groupId}`, jobData, { jobId: `dedup-${groupId}`, priority });
```

Lower number = higher priority. Enterprise jobs jump the line.

## Backpressure

When the normalize queue depth grows faster than workers can drain it (e.g., Postgres slowdown), pause ingestion before things crash:

```ts
// app/workers/backpressure.ts — runs in misc worker
setInterval(async () => {
  const depth = await getWebhookNormalizeQueue().getWaitingCount();
  const dbActive = await checkPostgresActiveQueries(); // pg_stat_activity active count
  if (depth > 10_000 || dbActive > 50) {
    await getWebhookNormalizeQueue().pause();
    log.warn({ depth, dbActive }, "Backpressure engaged — pausing webhook ingestion");
    // Re-check in 30s and resume if back to normal
    setTimeout(async () => {
      const depth2 = await getWebhookNormalizeQueue().getWaitingCount();
      const dbActive2 = await checkPostgresActiveQueries();
      if (depth2 < 1000 && dbActive2 < 20) {
        await getWebhookNormalizeQueue().resume();
        log.info("Backpressure cleared — resuming ingestion");
      }
    }, 30_000);
  }
}, 5_000);
```

## Dirty-flush pattern

For burst webhook traffic, mark-dirty + periodic flush:

```ts
// On webhook arrival
await markDirty(workspaceId, resourceId, reason);
// (no inline sync — debounced 3s sync gets enqueued; dirty-flush is the safety net)

// In the dirty-flush worker (every 2 min)
const stale = await db.dirtyResource.findMany({
  where: { createdAt: { lt: new Date(Date.now() - 30_000) }},
  take: 100,
});
for (const row of stale) {
  await getSyncTriggerQueue().add(...);
}
```

## Dead-letter queue + monitor

Failed jobs go to a DLQ. A monitor scans the DLQ hourly and alerts if anything's stuck:

```ts
const failed = await getSyncTriggerQueue().getJobs(["failed"], 0, 100);
if (failed.length > 50) {
  log.error({ count: failed.length }, "DLQ alert — >50 failed sync jobs");
  await sendOpsAlert(...);
}
```

## Process management gotchas

- **Cluster mode + Prisma**: each worker fork creates its own Prisma client. Singleton pattern in `db.server.ts` handles this — DON'T create new clients per request.
- **BullMQ + cluster mode**: workers MUST use `instance_var: "INSTANCE_ID"` and the worker code must include INSTANCE_ID in any per-process state to avoid collisions.
- **Graceful shutdown**: register SIGTERM handler that closes the worker, waits for in-flight jobs, then exits. PM2 sends SIGTERM on `pm2 reload`.
- **Memory leaks under load**: `max_memory_restart: "1G"` is a safety net but find the leak. Common cause: not closing PrismaClient after operations in scripts; in long-running workers the singleton is fine but check for accidental `new PrismaClient()` in handlers.
- **`process.exit` from within a worker**: kills the whole process. Throw instead so BullMQ records the failure and retries.

## Job retention policy

```ts
await queue.add("sync", jobData, {
  jobId: `dedup-${resourceId}`,
  removeOnComplete: { count: 500 },  // Keep last 500 successes for debugging
  removeOnFail: { count: 100 },      // Keep last 100 failures for DLQ analysis
  attempts: 3,                        // Retry on transient failures
  backoff: { type: "exponential", delay: 5000 },
});
```

At 1k jobs/sec the queue would otherwise grow unbounded — Redis memory bloat. The retention values balance debuggability vs. memory.

## Common pitfalls

- **Single Redis instance for everything**: queue load spikes starve session lookups and lock acquisitions. Use separate `db` indices or separate Redis instances.
- **Worker without `maxRetriesPerRequest: null`**: BullMQ throws on first disconnect. Connection bounce → all workers die.
- **No backpressure**: Postgres slowdown → queue grows → memory exhausted → app crashes. Pause ingestion before it gets there.
- **No priority queueing**: Enterprise customers stuck waiting behind Starter bulk imports.
- **Failed jobs retained forever**: memory bloat. Use `removeOnFail: { count: N }`.
- **Sync work in webhook handler**: see `webhooks.md`. Webhooks have a 5s ACK timeout.
