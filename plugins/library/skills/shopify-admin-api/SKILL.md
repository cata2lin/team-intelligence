---
name: shopify-admin-api
description: Comprehensive reference for the Shopify Admin GraphQL API (version 2026-04 / "latest"). Covers every endpoint, mutation, object, enum, and webhook topic relevant to inventory-sync / multi-store / catalog-management apps. Includes the @idempotent directive rules, rate-limit math, bulk-operations lifecycle, webhook HMAC + retry policy, and 30+ webhook topics with real payloads. Use when implementing or debugging any code that calls Shopify, receives a Shopify webhook, or designs around Shopify's data model.
---

# Shopify Admin GraphQL API Reference

A topic-scoped reference for the Shopify Admin GraphQL API (version **2026-04**, also addressable as `/latest`). Sourced from `shopify.dev/docs/api/admin-graphql/latest/...` on 2026-05-24.

This skill is split into 7 files so individual lookups don't load 11k lines at once. Open the file that matches your question.

## When to load this skill

Load when working on any of:

- A query or mutation against `https://{shop}.myshopify.com/admin/api/2026-04/graphql.json`
- A webhook handler (HMAC verification, payload parsing, dedup, retry behavior)
- Inventory math (sellable / on_hand / committed / safety_stock semantics)
- The `@idempotent` directive (mandatory on 18 mutations in 2026-04)
- Rate limits (`extensions.cost`, throttleStatus, leaky-bucket math)
- Bulk Operations (catalog imports, large updates, JSONL output)
- OAuth / token exchange / scope changes
- Plan-tier / App billing changes (`AppSubscription`)
- GDPR compliance webhooks (`shop/redact`, `customers/redact`, `customers/data_request`)
- Fulfillment-service ("legacy") location quirks
- Any time you'd otherwise be guessing field names, enum values, or error codes

## File map

| Topic | File | When to consult |
|---|---|---|
| Inventory items/levels/quantities + all 7 inventory mutations | [`inventory.md`](inventory.md) | Anything touching stock counts, reservations, FS-locations, the `on_hand = available + committed + ...` identity |
| Products, variants, options, media + bulk variant updates | [`products-variants.md`](products-variants.md) | Catalog imports, barcode/SKU changes, `PRODUCTS_UPDATE` webhook handling, variant search syntax |
| Orders, line items, fulfillments, refunds, returns, draft orders | [`orders-fulfillment.md`](orders-fulfillment.md) | Order imports, refund-restock side effects, fulfillment workflow, return lifecycle |
| Every webhook topic (30+) with payload + HMAC + retry rules | [`webhooks.md`](webhooks.md) | Implementing or debugging a webhook handler. Has-root-id quick-reference table at the end. |
| Locations (incl. FS/legacy), Shop, AppInstallation, AppSubscription, Markets, Channels | [`locations-shop-billing.md`](locations-shop-billing.md) | Location imports (`includeLegacy: true` rule), billing flow, plan changes, shop metadata |
| Bulk Operations, rate limits, auth, errors, `@idempotent`, metafields, files, release notes | [`platform.md`](platform.md) | "Platform plumbing." Always the right answer for THROTTLED, idempotency keys, version compatibility |
| Scalars (GID, DateTime, Money), enums (Currency/Country/Weight), search syntax, access scopes, Customer object | [`adjacent-types.md`](adjacent-types.md) | Cross-cutting types used everywhere. The universal search query syntax lives here. |

## High-value gotchas (always re-read before relevant work)

These are the things that have cost me/SyncApp real bugs. Internalize before they bite again.

### Inventory

1. **`inventorySetQuantities(name: "available")` reduces `on_hand` as a side effect.**
   Setting `available` is not a pure write — Shopify recomputes `on_hand` to `available + committed + reserved + ...`. If you set `available = 100` on a row with `committed = 4`, `on_hand` becomes `104`, not whatever it was. This caused the SyncApp safety-buffer compounding bug. See [`inventory.md`](inventory.md) §1 + §7.

2. **Fulfillment-service ("legacy") locations reject inventory writes.**
   `SET_INVENTORY_QUANTITIES` against a location where `isFulfillmentService = true` returns a userError. SyncApp must filter these out of the redistribution pool. The merchant can still SEE stock there; we just can't push. See [`inventory.md`](inventory.md) §7 + [`locations-shop-billing.md`](locations-shop-billing.md) §2.

3. **`locations(first: 50)` excludes FS locations by default.**
   You must pass `includeLegacy: true` to get them. Without it, a multi-location merchant's stock totals will silently miss inventory. See [`locations-shop-billing.md`](locations-shop-billing.md) §2.

4. **Self-push echo.** Every inventory write you make causes Shopify to fire an `inventory_levels/update` webhook back at you with the new value. You must suppress these or you'll re-process your own writes. SyncApp uses a 60s Redis TTL keyed by `(item, location, qty)`. See [`webhooks.md`](webhooks.md) §11.

5. **`inventoryAdjustQuantities` is the safe atomic delta. `inventorySetQuantities` is destructive absolute set.** Default to adjust unless you genuinely have an authoritative absolute number from outside Shopify (a cycle count, a 3PL export). See [`inventory.md`](inventory.md) §8.

### Webhooks

6. **`INVENTORY_LEVELS_UPDATE` has NO root `id`.** You cannot dedupe with `payload.id` — there is no such field. Build a composite event key: `inv-{inventory_item_id}-{location_id}-{available}`. See [`webhooks.md`](webhooks.md) §5 + §6.

7. **`X-Shopify-Webhook-Id` is the canonical dedup key.** Shopify guarantees this is unique per logical event. Use it as the BullMQ `jobId` (after sanitizing colons). See [`webhooks.md`](webhooks.md) §5.

8. **HMAC is base64, not hex.** Verify with `crypto.createHmac('sha256', secret).update(rawBody).digest('base64')` and `timingSafeEqual`. See [`webhooks.md`](webhooks.md) §2.

9. **Webhook subscriptions auto-disable after 49 hours of consecutive failures.** Your endpoint must respond 200 within 5 seconds. If subscription is removed, you must re-create it. See [`webhooks.md`](webhooks.md) §4.

10. **GDPR compliance webhook SLAs are legally binding.** `shop/redact` fires 48 hours after uninstall — you MUST delete all shop data. `customers/data_request` requires response within 30 days. `customers/redact` within 10 days. See [`webhooks.md`](webhooks.md) §8.

### Rate limits & idempotency

11. **As of 2026-04, 18 mutations REQUIRE the `@idempotent` directive.** Including: `inventoryAdjustQuantities`, `inventorySetQuantities`, `inventoryMoveQuantities`, `inventoryActivate`, `inventoryDeactivate`, all four `locationAdd/Edit/Activate/Deactivate`, `refundCreate`, plus others. Missing `idempotencyKey` argument now returns `MISSING_IDEMPOTENCY_KEY`. See [`platform.md`](platform.md) §7 + [`inventory.md`](inventory.md) §6.

12. **Idempotency window is 24 hours; key max 255 chars.** Replaying the same key returns the cached result. Mismatched arguments with the same key returns `IDEMPOTENCY_KEY_PARAMETER_MISMATCH`. UUID v4 is the recommended format. See [`platform.md`](platform.md) §7.

13. **Rate limit is a leaky bucket per `(app, shop)`, NOT per app.** Standard: 1000-point bucket, 100 pts/sec restore. Plus: 2000 / 200. Single query hard cap: 1000 points. Monitor `extensions.cost.throttleStatus.currentlyAvailable`. See [`platform.md`](platform.md) §4.

14. **Mutation cost defaults to 10 points (vs query default 1).** A burst of inventory writes will burn the bucket fast. SyncApp's rate-limiter must back off when `currentlyAvailable < 200`. See [`platform.md`](platform.md) §4.

### Products & variants

15. **`productInput` no longer accepts a `variants` field (as of 2024-10).** You must use `productCreate` then `productVariantsBulkCreate`, or `productSet` if you want a destructive set. See [`products-variants.md`](products-variants.md) §11.

16. **`productVariantsBulkUpdate` cannot touch inventory quantities.** It will return `NO_INVENTORY_QUANTITIES_ON_VARIANTS_UPDATE`. Use inventory mutations for that. See [`products-variants.md`](products-variants.md) §6.

17. **Tag updates are list-replacement, not list-merge.** Sending `tags: ["x"]` removes every other tag. Read-modify-write or use `tagsAdd`/`tagsRemove`. See [`products-variants.md`](products-variants.md) §11.

18. **Barcode is the SyncApp join key, but Shopify treats it as a free-text variant field.** Multiple variants in one shop can share a barcode (we log a warning when this happens). The variant-level uniqueness is `(shopId, barcode)` only as a SyncApp convention. See [`products-variants.md`](products-variants.md) §11.

### Orders & fulfillment

19. **`orderCancel(restock: true)` and `refundCreate` with `restockType != NO_RESTOCK` DO cause inventory changes.** Those changes fire `inventory_levels/update` webhooks. If you process the cancel/refund AND the inventory webhook, you'll double-count. See [`orders-fulfillment.md`](orders-fulfillment.md) §SyncApp angles.

20. **`refunds/create` webhook has its own root `id`** — dedupe with that, not with the parent order id. See [`orders-fulfillment.md`](orders-fulfillment.md) §SyncApp angles + [`webhooks.md`](webhooks.md).

21. **`LineItem.quantity` is the originally-ordered quantity. `LineItem.currentQuantity` is what's left after refunds/returns.** For sales-velocity calculations, use `currentQuantity`. See [`orders-fulfillment.md`](orders-fulfillment.md) §2.

22. **`read_all_orders` scope is required to read orders >60 days old** without protected-customer-data access. See [`adjacent-types.md`](adjacent-types.md) §12.

### Platform

23. **GIDs are strings, not numbers.** `gid://shopify/Product/123456` is the format. The numeric portion can exceed JS Number precision (`UnsignedInt64`). Never parse to int — keep as string. See [`adjacent-types.md`](adjacent-types.md) §4.

24. **Money is always `MoneyV2 { amount: Decimal!, currencyCode: CurrencyCode! }`.** The `amount` is a string-Decimal — never use JS Float for it. For order-touching fields, you get `MoneyBag { shopMoney, presentmentMoney }` instead (multi-currency). See [`adjacent-types.md`](adjacent-types.md) §2.

25. **`DateTime` is ISO 8601 UTC** — parse as `new Date(...)` is fine, but be wary of timezone vs the shop's `ianaTimezone` when computing day boundaries. SyncApp's snapshot pipeline uses each shop's timezone for "today" calculations.

26. **Bulk Operations: one query and one mutation can run concurrently per shop (raised to 5 each in 2026-01).** Use them for anything that would otherwise paginate >100 pages. Output is JSONL via a URL valid for 7 days. See [`platform.md`](platform.md) §6.

27. **Quarterly version cadence, 12-month support.** Stable versions: YYYY-01, YYYY-04, YYYY-07, YYYY-10. Each gets 12 months of support with the prior 9 months overlapping. Check active version via `X-Shopify-API-Version` response header. SyncApp pins to **2026-04**.

## Universal search query syntax

Used in every `query: "..."` argument across the Admin API (products, orders, customers, etc.):

```
field:value                  # exact match
field:>value                 # greater than
field:>=value                # greater than or equal
field:<value
field:<=value
field:value1..value2         # range (inclusive)
field:value*                 # prefix wildcard
field1:v1 AND field2:v2      # implicit AND between bare terms
field1:v1 OR field2:v2       # explicit OR
NOT field:value              # negation
(field1:v1 OR field2:v2)     # parentheses for grouping
"quoted phrase"              # multi-word phrase match
field:"value with spaces"
saved_search:my_search       # reference a saved search by name
```

Per-resource supported `field:` keys are listed in each topic file.

## SyncApp-specific patterns

These are the patterns we've codified in SyncApp. Re-use them rather than re-deriving:

| Pattern | Where it lives | What it solves |
|---|---|---|
| `rateLimitedShopifyFetch(shopId, domain, gql, vars, costHint)` | `app/services/rate-limiter.server.ts` | Tracks `extensions.cost.throttleStatus`, queues requests, breaks circuit on sustained throttle |
| `markAsSelfPush(itemGid, locationGid, qty, shopId)` + `isSelfPushAsync` | `app/lib/sync-origin.server.ts` | Suppresses webhook echoes from our own writes (60s Redis TTL) |
| Per-barcode-group Redis lock (`acquireLock`) | `app/lib/lock.server.ts` | Serializes pool recalc + push so concurrent webhooks don't corrupt allocations |
| `markDirty(workspaceId, groupId, reason)` + 2-min flush | `app/services/dirty-groups.server.ts` | Debounces webhook-triggered syncs into batches |
| `newIdempotencyKey(prefix)` UUID | `app/graphql/mutations.ts` | Generates 24h-stable idempotency keys for inventory mutations |
| Composite event key for INVENTORY_LEVELS_UPDATE | `app/routes/webhooks.tsx:52-59` | Workaround for the no-root-id problem |

## Things deliberately NOT covered

These categories are documented in shopify.dev but not in this skill because they're not relevant to SyncApp's data flow. If a future feature crosses into them, add a dedicated file.

- Storefront API (this is Admin API only — different endpoint, different auth)
- B2B catalogs, draft companies, B2B price lists
- Themes & Online Store (theme files, sections, sections-everywhere)
- Marketing campaigns, automations, abandoned-cart flows
- Customer accounts (new login flow, customer account API)
- Gift cards (issuance, redemption)
- Discounts beyond a basic overview (we don't create or modify discounts)
- POS-specific objects (cart, register, POS device)
- Translations / localization (`translatableResource`)
- Subscriptions / Selling Plans implementation (we read `sellingPlan` references in orders but don't manage them)
- Shipping rates, delivery profiles, carrier services
- Files API beyond the staged-upload pattern needed for product media + bulk-mutation input

## Source attribution

All content was pulled from `shopify.dev/docs/api/admin-graphql/latest/...` and `shopify.dev/docs/api/usage/...` on **2026-05-24**. Each file has a "Sources" section at the end with the exact URLs fetched. Re-verify against shopify.dev if a SyncApp behavior contradicts what's documented here — Shopify ships breaking changes quarterly.
