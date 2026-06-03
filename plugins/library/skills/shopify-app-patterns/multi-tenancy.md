# Multi-Tenancy: Workspaces, Primary Billing Shop, RBAC

When a single merchant owns multiple Shopify stores and wants them to share state (inventory pool, plan, settings), you need workspace-level multi-tenancy. This is the pattern.

## The Workspace abstraction

```prisma
model Workspace {
  id                        String   @id @default(cuid())
  name                      String
  ownerUserId               String   @map("owner_user_id")
  planId                    String?  @map("plan_id")
  primaryBillingShopId      String?  @map("primary_billing_shop_id")
  status                    String   @default("active")
  // ... workspace-wide settings (sync defaults, alert prefs, etc.)

  owner       User              @relation("WorkspaceOwner", fields: [ownerUserId], references: [id])
  plan        Plan?             @relation(fields: [planId], references: [id])
  shops       Shop[]            // N shops belong to this workspace
  invites     WorkspaceInvite[]
}

model Shop {
  id              String   @id @default(cuid())
  workspaceId     String   @map("workspace_id")
  myshopifyDomain String   @unique @map("myshopify_domain")
  shopName        String   @map("shop_name")
  status          String   @default("active")
  installedAt     DateTime @default(now())
  // ... per-shop fields

  workspace Workspace    @relation(fields: [workspaceId], references: [id], onDelete: Cascade)
  billing   ShopBilling? // 1-to-1 — the shop's relationship to the workspace's plan
  tokens    ShopToken[]  // encrypted access tokens
  // ... all per-shop child relations
}
```

Every other model is scoped to `workspaceId` (preferably) or `shopId` (when shop-specific). The golden rule: **every loader and action query must filter by `workspaceId` of the authenticated shop**. Cross-workspace data leaks are the single biggest correctness risk in multi-tenant apps.

## Workspace creation on first install

When a brand-new shop installs the app and the domain is unknown:

1. Create `User` row (email = `${shop}@yourapp.local` placeholder; merchant can override later)
2. Create `Workspace` row, owned by that User
3. Create `Shop` row, `workspaceId = workspace.id`
4. Set `Workspace.primaryBillingShopId = shop.id` (this shop pays)
5. Create `ShopBilling` row with default `Starter` plan, status `"active"`
6. Queue auto-import (see `oauth-and-session.md`)

```ts
// In afterAuth callback
const created = await db.$transaction(async (tx) => {
  const user = await tx.user.upsert({...});
  const workspace = await tx.workspace.create({ data: { ownerUserId: user.id, ... }});
  const shop = await tx.shop.create({ data: { workspaceId: workspace.id, ... }});
  await tx.workspace.update({
    where: { id: workspace.id },
    data: { primaryBillingShopId: shop.id, planId: starterPlan.id },
  });
  await tx.shopBilling.create({
    data: { shopId: shop.id, workspaceId: workspace.id, planId: starterPlan.id, status: "active" },
  });
  return { workspace, newShop: shop };
});
```

Use `$transaction` to keep the workspace + primary billing shop + billing row creation atomic. A half-failed install leaves orphan state that's hard to debug.

## Connecting a second shop (workspace join)

The first shop generates an invite code. The second shop installs the app fresh (gets its own workspace), then redeems the invite which **migrates the second shop into the first's workspace**:

```ts
// Migration: move shopB from its own workspace into workspaceA
await db.$transaction(async (tx) => {
  // Reparent the shop
  await tx.shop.update({
    where: { id: shopB.id },
    data: { workspaceId: workspaceA.id },
  });
  // Move all shop-scoped data to the new workspace
  await tx.shopifyProduct.updateMany({ where: { shopId: shopB.id }, data: { workspaceId: workspaceA.id }});
  await tx.shopifyVariant.updateMany({ where: { shopId: shopB.id }, data: { workspaceId: workspaceA.id }});
  await tx.inventoryLevel.updateMany({ where: { shopId: shopB.id }, data: { workspaceId: workspaceA.id }});
  // ... every shop-scoped table
  // Update the second shop's billing row to point to the new workspace + inherit the plan
  await tx.shopBilling.update({
    where: { shopId: shopB.id },
    data: { workspaceId: workspaceA.id, planId: workspaceA.planId },
  });
  // Delete the now-empty workspace
  await tx.workspace.delete({ where: { id: workspaceB.id }});
});
```

After join, shopB inherits Workspace A's plan automatically. No new billing charge — Workspace A's primaryBillingShop continues to pay.

## Primary billing shop semantics

- Only `workspace.primaryBillingShopId` has a Shopify subscription
- All other shops in the workspace have a `ShopBilling` row with status `"active"` but no Shopify subscription (their plan inherits)
- UI badge: only show "Primary billing · Paid/Comp/Pending" on the primary shop. Secondary shops show no billing badge — they inherit.
- If the primary shop uninstalls, the workspace needs a successor — either auto-promote another shop, or require explicit support contact

## Workspace-scoped query enforcement

Every query, including loaders, MUST filter by workspace:

```ts
// WRONG — leaks cross-workspace data
const products = await db.shopifyProduct.findMany({ where: { id: productId }});

// RIGHT
const shop = await db.shop.findUnique({ where: { myshopifyDomain: session.shop }});
const products = await db.shopifyProduct.findMany({
  where: { id: productId, workspaceId: shop.workspaceId },
});
```

For action handlers that take a target ID from form data:

```ts
// Re-fetch with workspace check, 403 on miss
const targetGroup = await db.barcodeGroup.findFirst({
  where: { id: groupId, workspaceId: shop.workspaceId },
});
if (!targetGroup) {
  return Response.json({ error: "Not in your workspace" }, { status: 403 });
}
```

## RBAC: Role hierarchy

Four-role minimum:

```
owner > admin > manager > viewer
```

- **Owner**: workspace creator, can change billing, transfer ownership
- **Admin**: can change settings, invite users, manage all sync config
- **Manager**: can run syncs, adjust inventory, create barcode groups
- **Viewer**: read-only

```ts
// app/lib/role.server.ts
export async function requireRole(
  shopDomain: string,
  minRole: "owner" | "admin" | "manager" | "viewer"
): Promise<void> {
  const userRole = await getUserRole(shopDomain);
  const ranks = { viewer: 0, manager: 1, admin: 2, owner: 3 };
  if (ranks[userRole] < ranks[minRole]) {
    throw new Response("Insufficient role", { status: 403 });
  }
}

// Usage in actions
export const action = async ({ request }) => {
  const { session } = await authenticate.admin(request);
  await requireRole(session.shop, "manager"); // gate before any mutation
  // ...
};
```

Gate every mutator. Loaders typically don't need role gates (just workspace scoping), but mutating actions do.

## Workspace deletion

On `shop/redact` for the primary billing shop, the entire workspace gets nuked. Use Prisma cascading FKs on every shop-child relation:

```prisma
model ShopifyProduct {
  // ...
  shop      Shop      @relation(fields: [shopId], references: [id], onDelete: Cascade)
}
```

Then `db.shop.delete({ where: { id }})` cascades through everything. Belt-and-suspenders: also explicitly delete in the webhook handler in case a future FK gets added without `onDelete: Cascade`.

For a secondary shop redact (not the primary billing shop), delete just that shop's child data; leave the workspace and other shops intact.

## Common pitfalls

- **Forgetting `workspaceId` filter on action handlers** — most common cross-workspace data leak. Run a "Sprint 1 sweep" — grep every `app/routes/app.*.tsx` `action` handler and confirm it re-fetches the target with `workspaceId` clause.
- **Not migrating ALL child tables when joining workspaces** — leaves dangling rows that count against the new workspace. Audit every `@map("shop_id")` foreign-key column.
- **Per-shop billing checks instead of workspace** — checking `shop.billing.status` instead of `workspace.plan.includes_X`. The primary shop pays; secondary shops have a "pseudo-active" billing row but the entitlement lives on the workspace plan.
- **Stale workspace plan after Shopify cancels subscription** — listen to `APP_SUBSCRIPTIONS_UPDATE` webhook; if the primary shop's subscription is cancelled, downgrade the workspace plan accordingly (with grace period if your policy allows).
