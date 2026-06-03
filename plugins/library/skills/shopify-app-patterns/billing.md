# Shopify Billing Integration: Tiered Plans, Downgrades, Comp/Redeem Codes

The Billing API is well-documented but has edge cases around multi-shop, downgrades, and "I want this customer on a free plan without paying anything".

## Plan structure: define once, reuse everywhere

The dollar amounts in TOML, code, DB, and listing must match. Define a `Plan` table seeded once and reference it from `ShopBilling`:

```prisma
model Plan {
  id                    String   @id @default(cuid())
  name                  String   @unique  // "Starter", "Growth", "Business", "Enterprise"
  monthlyPrice          Decimal  @db.Decimal(8, 2)  @map("monthly_price")
  // Entitlements — what features this plan unlocks
  maxStores             Int      @map("max_stores")
  includesStockRules    Boolean  @default(false)   @map("includes_stock_rules")
  includesAdvancedReports Boolean @default(false)  @map("includes_advanced_reports")
  // ...
}

model ShopBilling {
  id              String   @id @default(cuid())
  shopId          String   @unique @map("shop_id")
  workspaceId     String   @map("workspace_id")
  planId          String   @map("plan_id")
  status          String   @default("pending")  // pending | active | cancelled | frozen
  shopifySubscriptionId String? @map("shopify_subscription_id")
  trialEndsAt     DateTime? @map("trial_ends_at")
  // Sprint 13 — comp plan flag
  isCompPlan      Boolean  @default(false) @map("is_comp_plan")
  compRedeemedAt  DateTime? @map("comp_redeemed_at")

  shop      Shop @relation(fields: [shopId], references: [id], onDelete: Cascade)
  plan      Plan @relation(fields: [planId], references: [id])
}
```

Then in `shopify.server.ts` declare the same plan structure:

```ts
const shopify = shopifyApp({
  // ...
  billing: {
    Starter: {
      lineItems: [{ amount: 9.99, currencyCode: "USD", interval: BillingInterval.Every30Days }],
      trialDays: 7,
    },
    Growth: {
      lineItems: [{ amount: 24.99, currencyCode: "USD", interval: BillingInterval.Every30Days }],
      trialDays: 7,
    },
    Business: {
      lineItems: [{ amount: 49.99, currencyCode: "USD", interval: BillingInterval.Every30Days }],
      trialDays: 7,
    },
    Enterprise: {
      lineItems: [{ amount: 99.99, currencyCode: "USD", interval: BillingInterval.Every30Days }],
      trialDays: 7,
    },
  },
});
```

The keys (`Starter`, `Growth`, etc.) must match the `Plan.name` values in DB exactly — they're load-bearing strings.

## Requesting a billing charge

In your `/app/billing` action:

```ts
export const action = async ({ request }) => {
  const { admin, session, billing } = await authenticate.admin(request);
  await requireRole(session.shop, "owner");

  const formData = await request.formData();
  const planName = formData.get("plan") as string;

  // billing.require returns existing active charge OR triggers the approval flow
  await billing.require({
    plans: [planName],  // Pass the plan name (matches @shopify/shopify-app-remix billing keys)
    isTest: process.env.NODE_ENV !== "production",
    onFailure: async () => billing.request({ plan: planName, isTest: !!process.env.DEV }),
  });

  return Response.json({ ok: true });
};
```

On first call for a new plan, Shopify shows the approval modal. After approval, subsequent calls return immediately (charge exists).

## Plan downgrade — Shopify auto-cancels the old subscription

When a merchant changes from Enterprise → Growth:

```ts
await billing.require({ plans: ["Growth"], isTest: !!isDev });
```

Shopify automatically cancels the old Enterprise subscription when the merchant approves the Growth charge. You don't need to call cancel explicitly.

**Critical**: ALWAYS preserve the merchant's PREMIUM configuration even after downgrade. If a Business-tier merchant configured "Weighted" allocation strategy and then downgrades to Starter, do NOT erase their strategy choice. Instead, fall back at engine-time:

```ts
function resolveStrategyForPlan(configured: string, hasFeature: boolean): string {
  if (configured === "weighted" && !hasFeature) return "safe";
  if (configured === "manual" && !hasFeature) return "safe";
  return configured;
}
```

When they re-upgrade, their original strategy auto-restores without re-configuration. Surface this in the UI with a "your saved Weighted config is preserved — upgrade to reactivate" banner.

## `APP_SUBSCRIPTIONS_UPDATE` webhook

Listen for this to keep your local billing state in sync with Shopify:

```ts
case "APP_SUBSCRIPTIONS_UPDATE": {
  const status = payload.app_subscription?.status; // ACTIVE | CANCELLED | FROZEN | EXPIRED
  const subscriptionId = payload.app_subscription?.admin_graphql_api_id;
  const planName = payload.app_subscription?.name; // matches your plan keys

  // Find the shop's billing row
  const shopRow = await db.shop.findUnique({ where: { myshopifyDomain: shop }});
  if (!shopRow) return new Response(null, { status: 200 });

  // SKIP THIS WEBHOOK if the workspace is on a comp plan (see below)
  const billing = await db.shopBilling.findUnique({ where: { shopId: shopRow.id }});
  if (billing?.isCompPlan) {
    log.info({ shop }, "Comp plan — ignoring APP_SUBSCRIPTIONS_UPDATE");
    return new Response(null, { status: 200 });
  }

  const plan = await db.plan.findFirst({ where: { name: planName }});
  if (plan) {
    await db.shopBilling.update({
      where: { shopId: shopRow.id },
      data: {
        planId: plan.id,
        status: status.toLowerCase(),
        shopifySubscriptionId: subscriptionId,
      },
    });
    // For primary billing shop, also update the workspace plan
    const ws = await db.workspace.findUnique({ where: { id: shopRow.workspaceId }});
    if (ws?.primaryBillingShopId === shopRow.id) {
      await db.workspace.update({
        where: { id: ws.id },
        data: { planId: plan.id },
      });
    }
  }
  return new Response(null, { status: 200 });
}
```

## Comp/redeem code pattern (free tier without Shopify Billing)

For internal owner stores, beta users, or special-case freebies, implement an in-app redeem code that flips `isCompPlan = true`. The webhook handler then ignores subscription updates for comp-flagged shops.

### Schema additions

```prisma
model ShopBilling {
  // ... existing
  isCompPlan      Boolean   @default(false) @map("is_comp_plan")
  compRedeemedAt  DateTime? @map("comp_redeemed_at")
}

model PlanRedemption {
  id          String   @id @default(cuid())
  workspaceId String   @map("workspace_id")
  shopId      String   @map("shop_id")
  shopDomain  String   @map("shop_domain")
  planId      String   @map("plan_id")
  ipAddress   String?  @map("ip_address")
  userAgent   String?  @map("user_agent")
  createdAt   DateTime @default(now()) @map("created_at")
}
```

### Redeem action

```ts
import { timingSafeEqual } from "node:crypto";
import { getRedis } from "../lib/redis.server";

// In app/routes/app.billing.tsx action handler
if (intent === "redeem_code") {
  await requireRole(session.shop, "owner");

  const expectedCode = process.env.OWNER_REDEEM_CODE || "";
  const submitted = String(formData.get("code") || "");

  // Constant-time compare to prevent timing attacks
  const a = Buffer.from(submitted);
  const b = Buffer.from(expectedCode);
  const matches = a.length === b.length && timingSafeEqual(a, b);

  // Per-shop rate limit: 5 attempts per hour
  const redis = getRedis();
  const rateKey = `redeem:${shop.id}:${Math.floor(Date.now() / 3600000)}`;
  const count = await redis.incr(rateKey);
  await redis.expire(rateKey, 3600);
  if (count > 5) {
    return Response.json({ error: "Too many attempts — try again later" }, { status: 429 });
  }

  if (!matches) {
    return Response.json({ error: "Invalid code" }, { status: 400 });
  }

  const enterprisePlan = await db.plan.findFirst({ where: { name: "Enterprise" }});
  await db.$transaction([
    db.workspace.update({
      where: { id: shop.workspaceId },
      data: { planId: enterprisePlan.id },
    }),
    db.shopBilling.update({
      where: { shopId: shop.id },
      data: {
        planId: enterprisePlan.id,
        status: "active",
        isCompPlan: true,
        compRedeemedAt: new Date(),
      },
    }),
    db.planRedemption.create({
      data: {
        workspaceId: shop.workspaceId,
        shopId: shop.id,
        shopDomain: shop.myshopifyDomain,
        planId: enterprisePlan.id,
        ipAddress: request.headers.get("x-forwarded-for") || null,
        userAgent: request.headers.get("user-agent") || null,
      },
    }),
  ]);

  return Response.json({ ok: true });
}
```

### Critical: protect from `APP_SUBSCRIPTIONS_UPDATE` overwriting

If a comp-plan shop ever clicks a paid plan button by accident and confirms in Shopify Billing, the resulting `APP_SUBSCRIPTIONS_UPDATE` webhook would override the comp plan. The webhook handler must check `isCompPlan` and skip the update.

For multi-shop workspaces, also consider: if ANY shop in the workspace is comp, treat the whole workspace as comp-locked (don't let a non-primary shop's subscription change override the workspace plan).

### `.env` gotcha

If `OWNER_REDEEM_CODE=AronaAdmin123#`, dotenv treats `#` as start of comment → reads only `AronaAdmin123`. ALWAYS quote values containing `#`, `$`, spaces, or quotes:

```
OWNER_REDEEM_CODE="AronaAdmin123#"
```

Verify post-deploy:
```bash
node -e 'require("dotenv").config(); console.log(JSON.stringify(process.env.OWNER_REDEEM_CODE))'
```

## Workspace-level billing for multi-shop apps

See [`multi-tenancy.md`](multi-tenancy.md). The summary: only `workspace.primaryBillingShopId` has a Shopify subscription. Other shops in the workspace inherit. UI shows one "Primary billing" chip on the payer; secondary shops show no billing badge.

## Pitfalls

- **Mismatched amounts across TOML / code / DB / listing** — Shopify reviewers cross-check; rejection.
- **Trial days inconsistent** — must be the same number everywhere.
- **`isTest: true` left on in production** — merchants get test charges that don't actually bill. Use `process.env.NODE_ENV !== "production"`.
- **Comp plan unprotected from webhook overwrite** — single misclick gives the comp shop a real Shopify subscription and they get billed.
- **Plan downgrade erases premium config** — merchants HATE re-configuring on re-upgrade. Preserve config; fall back at engine-time instead.
- **`.env` `#` parse trap** — quote any value containing `#`.
- **No rate limit on redeem endpoint** — attackers can brute-force codes. Limit per shop AND per IP.
- **Logging the plaintext redeem code** — don't log `submitted` directly. Log "redeem attempt: matched=true/false".
