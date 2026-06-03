# OAuth, afterAuth Idempotency, Reinstall Handling, Token Encryption

The OAuth flow looks simple in the SDK but has subtle production traps. Internalize these before touching `shopify.server.ts`.

## The `afterAuth` double-fire problem

In cluster mode (PM2, Kubernetes, anywhere with multiple Node processes), `@shopify/shopify-app-remix`'s `afterAuth` callback can fire **twice per install**:

1. Initial OAuth callback hits worker A → session created → `afterAuth(A)` runs
2. The embedded app loads in the Admin iframe → re-establishes session → hits worker B → `afterAuth(B)` runs again with the SAME session

If `afterAuth` does any non-idempotent work (createShop, INSERT-only queries, `setTimeout(import, 3000)`, charge requests), both invocations fire it. Real consequences I've seen:

- Duplicate Workspace rows
- Auto-import running twice → Shopify API quota exhaustion in the first 5 min
- Billing request fired twice → merchant sees the approval modal flicker
- Email "welcome" sent twice
- Two BullMQ jobs for the same shop

## Mitigation: design `afterAuth` to be idempotent end-to-end

```ts
async function afterAuth({ session }: { session: Session }) {
  // 1. Lookup-or-create using a UNIQUE-constrained natural key (myshopifyDomain)
  const existingShop = await db.shop.findUnique({
    where: { myshopifyDomain: session.shop },
  });

  if (!existingShop) {
    // Atomic transaction for first-install path
    const created = await db.$transaction(async (tx) => {
      const user = await tx.user.upsert({
        where: { email: `${session.shop}@yourapp.local` },
        update: {},
        create: { email: `${session.shop}@yourapp.local`, name: session.shop },
      });
      const workspace = await tx.workspace.create({...});
      const newShop = await tx.shop.create({
        data: {
          workspaceId: workspace.id,
          myshopifyDomain: session.shop, // <-- UNIQUE constraint catches the race
          // ...
        },
      });
      return { workspace, newShop };
    }).catch((err) => {
      // If the unique constraint fires, the OTHER afterAuth invocation
      // already won the race. Re-lookup and treat as existing.
      if (err.code === "P2002") {
        return null;
      }
      throw err;
    });

    if (created) {
      // Only enqueue post-install work in the WINNING invocation's path
      await queueAutoImport(created.newShop.id, created.workspace.id, session.shop);
    }
    // The losing invocation fell through to the null path — it'll re-enter on next request as existing.
  } else {
    // Reinstall path — see below
    if (existingShop.status !== "active") {
      await db.shop.update({
        where: { id: existingShop.id },
        data: { status: "active" },
      });
    }
    // Patch any half-failed state from a prior crashed install (workspace plan, billing row, etc.)
    await patchHalfFailedInstall(existingShop);
    // Re-enqueue auto-import — BullMQ jobId-dedup handles duplicates
    await queueAutoImport(existingShop.id, existingShop.workspaceId, session.shop);
  }
}
```

The pattern:
- UNIQUE constraint on `myshopifyDomain` lets Postgres referee the race
- Catch `P2002` (Prisma's "unique constraint violated") in transaction, treat as already-existing
- Enqueue post-install work to BullMQ with `jobId = shopId` — duplicate enqueues silently dropped

## Why not `setTimeout`?

`setTimeout(() => doWork(), 3000)` inside `afterAuth` doesn't survive:

- Cluster mode fork — timer is local to the process that ran `afterAuth`. If the merchant's next request lands on a different process, the timer keeps running but the merchant is acting on stale state.
- Server restart — timer evaporates. Auto-import never runs. Shop has zero products.
- Process crash during timer wait — same, lost work.

Always enqueue durable jobs to BullMQ instead.

## Reinstall handling

Merchants uninstall and reinstall apps often (testing, payment issues, app upgrades). The flow:

1. On `app/uninstalled` webhook: mark shop `status = "uninstalled"`. Do NOT delete data (the merchant may reinstall within minutes and want their config back).
2. Eventually (after `shop/redact` webhook, ~48h later): delete all shop data.
3. On reinstall: `afterAuth` runs again. Existing shop row exists with `status = "uninstalled"`. Flip back to `"active"`. Re-queue auto-import (BullMQ jobId-dedupes if it ran recently).
4. If a prior install crashed before completing (e.g., shop row exists but billing row doesn't), patch the missing pieces in `afterAuth`:

```ts
async function patchHalfFailedInstall(existingShop: Shop) {
  const ws = await db.workspace.findUnique({ where: { id: existingShop.workspaceId }});
  if (!ws) return; // genuinely broken — recover requires manual intervention

  const updates: Partial<Workspace> = {};
  if (!ws.planId) updates.planId = (await db.plan.findFirst({ where: { name: "Starter" }}))?.id;
  if (!ws.primaryBillingShopId) updates.primaryBillingShopId = existingShop.id;
  if (Object.keys(updates).length > 0) {
    await db.workspace.update({ where: { id: ws.id }, data: updates });
  }

  // Ensure billing row exists (idempotent upsert)
  await db.shopBilling.upsert({
    where: { shopId: existingShop.id },
    update: {}, // don't overwrite existing status
    create: { shopId: existingShop.id, workspaceId: ws.id, planId: ws.planId!, status: "active" },
  });
}
```

This is your "self-healing" path — covers all the edge cases of partial installs in a single place.

## Access token storage: AES-256-GCM

Shopify access tokens are bearer credentials. Anyone with a token can call the Admin API as that merchant until the token is revoked. Plaintext storage = breach.

Pattern: encrypt at rest with a key from env, decrypt only when needed.

```ts
// app/lib/encryption.server.ts
import crypto from "node:crypto";

const KEY = Buffer.from(process.env.TOKEN_ENCRYPTION_KEY!, "hex"); // 32 bytes = 64 hex chars
const ALGO = "aes-256-gcm";

export function encryptToken(plaintext: string): string {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGO, KEY, iv);
  const enc = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  // Store as base64(iv | tag | ciphertext)
  return Buffer.concat([iv, tag, enc]).toString("base64");
}

export function decryptToken(stored: string): string {
  const buf = Buffer.from(stored, "base64");
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const enc = buf.subarray(28);
  const decipher = crypto.createDecipheriv(ALGO, KEY, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(enc), decipher.final()]).toString("utf8");
}
```

Wrap the session storage so encryption is transparent:

```ts
// app/lib/session-storage.server.ts — Prisma session storage with auto-encryption
class RefreshingPrismaSessionStorage extends PrismaSessionStorage {
  async storeSession(session: Session): Promise<boolean> {
    const encrypted = { ...session, accessToken: encryptToken(session.accessToken) };
    return super.storeSession(encrypted);
  }
  async loadSession(id: string): Promise<Session | undefined> {
    const session = await super.loadSession(id);
    if (session?.accessToken) {
      session.accessToken = decryptToken(session.accessToken);
    }
    return session;
  }
}
```

Use it in `shopifyApp({...})`:

```ts
const shopify = shopifyApp({
  apiKey: process.env.SHOPIFY_API_KEY,
  apiSecretKey: process.env.SHOPIFY_API_SECRET,
  sessionStorage: new RefreshingPrismaSessionStorage(db),
  // ...
});
```

Key rotation: keep TWO keys in env (`TOKEN_ENCRYPTION_KEY` current, `TOKEN_ENCRYPTION_KEY_OLD` previous). On decrypt, try current first, fall back to old. Re-encrypt on next write to migrate. Drop the old key after migration is complete.

## Fail-fast on missing secrets

```ts
if (!process.env.SHOPIFY_API_KEY) {
  throw new Error("SHOPIFY_API_KEY is not set — refusing to start.");
}
if (!process.env.SHOPIFY_API_SECRET) {
  throw new Error("SHOPIFY_API_SECRET is not set — refusing to start.");
}
if (!process.env.TOKEN_ENCRYPTION_KEY || process.env.TOKEN_ENCRYPTION_KEY.length !== 64) {
  throw new Error("TOKEN_ENCRYPTION_KEY must be a 64-char hex string.");
}
```

The previous `process.env.SHOPIFY_API_SECRET || ""` pattern silently produces an empty-secret SDK config, which yields baffling OAuth failures downstream. Crash at boot instead.

## Session token verification (App Bridge)

For embedded app loaders, the Shopify SDK's `authenticate.admin(request)` handles JWT verification automatically. Don't roll your own. Just gate every `/app/*` route with it:

```ts
export const loader = async ({ request }) => {
  const { session } = await authenticate.admin(request);
  // session.shop is the verified myshopify.com domain
  // Use this for the workspace-scoping queries.
};
```
