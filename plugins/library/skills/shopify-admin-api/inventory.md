# Shopify Admin GraphQL API — Inventory Reference

> Scope: Inventory items, levels, quantities, queries, and mutations.
> Target API version: `2026-04` (April26). Where 2026-04 differs from earlier versions, the difference is called out inline.
> Source: `shopify.dev/docs/api/admin-graphql/latest/*` (fetched 2026-05-24).
> Audience: engineers maintaining SyncApp, a multi-store inventory-sync Shopify app.

---

## Table of contents

1. [Inventory concepts](#1-inventory-concepts)
2. [InventoryItem object](#2-inventoryitem-object)
3. [InventoryLevel object](#3-inventorylevel-object)
4. [InventoryQuantity object](#4-inventoryquantity-object)
5. [Queries](#5-queries)
6. [Mutations](#6-mutations)
7. [SyncApp gotchas](#7-syncapp-gotchas)
8. [Decision tree: which mutation should I use?](#8-decision-tree-which-mutation-should-i-use)

---

## 1. Inventory concepts

### 1.1 Quantity states (the eight names)

A Shopify shop tracks inventory at each `(InventoryItem, Location)` pair as a vector of named quantities, not a single integer. There are **eight** canonical state names. Use the `inventoryProperties` query at runtime to discover which are actually enabled for the merchant — `safety_stock`, `quality_control`, and `damaged` are opt-in per shop.

| `name` value      | Meaning                                                                                                              | Mutatable via API?                                  |
| ----------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `available`       | Units ready for sale; not allocated to orders or reserved for other purposes.                                        | Yes (`adjust`, `set`, `move`)                       |
| `on_hand`         | Total units physically stocked at the location. Aggregate of the other states.                                       | Yes (`adjust`, `set` — but see side-effect warning) |
| `committed`       | Units allocated to existing orders.                                                                                  | **No** — only the order lifecycle changes this.     |
| `reserved`        | Units held temporarily — e.g. carts/drafts, awaiting transfer.                                                       | Yes (`move` only)                                   |
| `incoming`        | Units expected from purchase orders or transfers not yet received.                                                   | Yes (`move`)                                        |
| `damaged`         | Units unsuitable for sale (opt-in for the shop).                                                                     | Yes (`move`)                                        |
| `safety_stock`    | Buffer inventory to protect against demand spikes (opt-in).                                                          | Yes (`move`)                                        |
| `quality_control` | Units in inspection (opt-in).                                                                                        | Yes (`move`)                                        |

### 1.2 The `on_hand` identity

`on_hand` is **not** an independent counter — it is defined as the sum of the other "physical-presence" states:

```
on_hand = available + committed + reserved + damaged + safety_stock + quality_control
```

`incoming` is **not** part of `on_hand` because incoming stock isn't physically at the location yet.

Two consequences engineers must internalize:

1. **Setting `available` cascades.** When you `inventorySetQuantities(name: "available", quantity: N)`, Shopify computes the delta vs. the previous `available` and applies that **same delta to `on_hand`**. There is no way to "set available without touching on_hand" in a single call — the identity is enforced server-side. (See the worked example in section 6.3.)
2. **Moving between non-available states does not change `on_hand`.** Moving 5 units from `available` to `damaged` at the same location decreases `available` by 5 and increases `damaged` by 5 — `on_hand` is unchanged because both buckets count toward it.

### 1.3 Fulfillment service locations

Locations created by a `FulfillmentService` app (3PLs, dropshippers, etc.) are first-class `Location` records but Shopify hides them from common queries unless you opt in:

- `locations(first: N)` query: **excludes** FS locations by default. Pass `includeLegacy: true` to include them.
- `inventoryLevels(...)` on an InventoryItem: includes them, but the FS owns the inventory.
- FS locations **reject** several mutation paths:
  - `inventorySetQuantities` with `name: "available"` is generally rejected on FS locations whose stock is not managed by Shopify (you get an FS-specific error or a `NON_MUTABLE_INVENTORY_ITEM`-style failure).
  - `inventoryActivate` requires `stockAtLegacyLocation: true` to opt into activating at an FS location when SKU sharing is off.
  - `inventoryBulkToggleActivation` fails with `INVENTORY_MANAGED_BY_3RD_PARTY` when you try to stock an FS-managed item at a normal location, and vice versa.

> SyncApp angle: any "redistribute pool across stores" worker must filter out FS-only locations before writing, or pre-check `inventoryLevel.canDeactivate` / location ownership. We learned this the hard way (see [PRODUCTION-AUDIT.md](../../../Desktop/SyncApp/PRODUCTION-AUDIT.md)).

### 1.4 Webhook echo loop

When your app writes to inventory (`adjust`, `set`, `move`, `activate`, `bulkToggleActivation`), Shopify fires `inventory_levels/update` back to your webhook endpoint within a few seconds. The payload does **not** identify the originator app. To avoid an infinite loop in a sync app, you must:

1. Record the `(itemGid, locationGid, qty)` you are about to write **before** the mutation completes, with a short TTL (60s is typical).
2. In the webhook worker, look up the incoming `(itemGid, locationGid, qty)` and skip if it matches a recent self-write.

SyncApp implements this via `markAsSelfPush` / `isSelfPushAsync` in [app/lib/sync-origin.server.ts](../../../Desktop/SyncApp/app/lib/sync-origin.server.ts). State lives in Redis because the web process (which writes) and the worker process (which reads) have separate memory.

Webhooks **do not fire** for changes to `committed`, `reserved`, `damaged`, `safety_stock`, or `quality_control`. Only `available` and (by side-effect) `on_hand` changes produce webhooks. Plan reconciliation passes accordingly — drift in those other buckets is invisible to your real-time pipeline.

---

## 2. InventoryItem object

> Access scope: `read_inventory` (or `read_products`).
> Implements: `LegacyInteroperability`, `Node`.

A product variant's inventory metadata — a single record per variant, regardless of how many locations stock it.

### 2.1 Fields

| Field                            | Type                                  | Description                                                                                            |
| -------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `id`                             | `ID!`                                 | Globally unique GID. Format: `gid://shopify/InventoryItem/<numeric>`.                                  |
| `legacyResourceId`               | `UnsignedInt64!`                      | The numeric ID used in the REST Admin API.                                                             |
| `sku`                            | `String`                              | Case-sensitive SKU. May be null/empty.                                                                 |
| `tracked`                        | `Boolean!`                            | Whether inventory levels are tracked for the item. If `false`, levels exist but are not adjusted by orders. |
| `trackedEditable`                | `EditableProperty!`                   | Indicates whether `tracked` can currently be modified (e.g., locked by a FS).                          |
| `requiresShipping`               | `Boolean!`                            | Whether the inventory item must be physically shipped.                                                 |
| `countryCodeOfOrigin`            | `CountryCode`                         | ISO 3166-1 alpha-2 country code.                                                                       |
| `provinceCodeOfOrigin`           | `String`                              | ISO 3166-2 province code.                                                                              |
| `harmonizedSystemCode`           | `String`                              | HS code, 6–13 digits.                                                                                  |
| `countryHarmonizedSystemCodes`   | `CountryHarmonizedSystemCodeConnection!` | Per-country HS code overrides. Paginated (`first`, `after`, `last`, `before`, `reverse`).            |
| `measurement`                    | `InventoryItemMeasurement!`           | Packaging dimensions and weight.                                                                       |
| `unitCost`                       | `MoneyV2`                             | Unit cost. Requires the staff "View product costs" permission.                                         |
| `duplicateSkuCount`              | `Int!`                                | How many other inventory items in the shop share this SKU.                                             |
| `locationsCount`                 | `Count`                               | How many locations stock this item.                                                                    |
| `inventoryHistoryUrl`            | `URL`                                 | Admin URL pointing to the item's history view.                                                         |
| `createdAt`                      | `DateTime!`                           |                                                                                                        |
| `updatedAt`                      | `DateTime!`                           |                                                                                                        |
| `inventoryLevel(locationId, includeInactive)` | `InventoryLevel`         | Level at the given location. `locationId: ID!` required. `includeInactive: Boolean` optional.          |
| `inventoryLevels(includeInactive, first, after, last, before, reverse, query)` | `InventoryLevelConnection!` | Paginated levels across locations.                                                                     |
| `variants`                       | `ProductVariantConnection`            | Variants referencing this item (usually one).                                                          |
| `variant`                        | `ProductVariant!`                     | **Deprecated** — use `variants`.                                                                       |

### 2.2 InventoryItemMeasurement (returned object)

```graphql
type InventoryItemMeasurement {
  id: ID!
  weight: Weight
  # plus shipping package info if set
}
```

`weight` follows the `Weight` object: `{ unit: WeightUnit!, value: Float! }` with `WeightUnit` being one of `GRAMS | KILOGRAMS | OUNCES | POUNDS`.

### 2.3 Read example

```graphql
query InventoryItemForVariant($id: ID!) {
  inventoryItem(id: $id) {
    id
    sku
    tracked
    requiresShipping
    countryCodeOfOrigin
    harmonizedSystemCode
    measurement {
      weight { unit value }
    }
    inventoryLevels(first: 50, includeInactive: false) {
      edges {
        node {
          id
          location { id name isActive }
          quantities(names: ["available", "on_hand", "committed", "incoming"]) {
            name
            quantity
            updatedAt
          }
        }
      }
    }
  }
}
```

---

## 3. InventoryLevel object

> Access scope: `read_inventory`. Implements `Node`.

Represents the bag of quantities for one `(InventoryItem, Location)` pair.

### 3.1 Fields

| Field                                        | Type                                  | Description                                                                                                                                                       |
| -------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                         | `ID!`                                 | GID, format `gid://shopify/InventoryLevel/<n>?inventory_item_id=<itemId>`. Note the trailing query param — pasting into a `Node` lookup requires the full string. |
| `item`                                       | `InventoryItem!`                      | The item this level belongs to.                                                                                                                                   |
| `location`                                   | `Location!`                           | The location this level belongs to.                                                                                                                               |
| `quantities(names: [String!]!)`              | `[InventoryQuantity!]!`               | The quantity vector. **Required** `names` argument — pass the names you want. Unrecognized names return `0` with `updatedAt: null`.                              |
| `isActive`                                   | `Boolean!`                            | Whether the level is currently active (stocked) at the location.                                                                                                  |
| `canDeactivate`                              | `Boolean!`                            | Whether the level can currently be deactivated (e.g., not blocked by committed/incoming/reserved stock).                                                          |
| `deactivationAlert`                          | `String`                              | Human-readable message explaining either the impact of deactivation or why it's blocked. Display this verbatim in UI before triggering `inventoryDeactivate`.     |
| `scheduledChanges`                           | `InventoryScheduledChangeConnection!` | Pending state transitions (e.g., transfers that will land later). **Deprecated** field name but still functional in 2026-04.                                       |
| `createdAt` / `updatedAt`                    | `DateTime!`                           | Timestamps. `updatedAt` is the level-level timestamp, not the per-quantity timestamp — for per-state granularity, read `quantities[].updatedAt`.                  |

There is **no** `available: Int` field on InventoryLevel — the legacy `available` shortcut was removed. You must use `quantities(names: ["available"])`.

### 3.2 Read example

```graphql
query LevelAtLocation($itemId: ID!, $locationId: ID!) {
  inventoryItem(id: $itemId) {
    inventoryLevel(locationId: $locationId) {
      id
      isActive
      canDeactivate
      deactivationAlert
      quantities(names: ["available", "on_hand", "committed"]) {
        name
        quantity
        updatedAt
      }
    }
  }
}
```

---

## 4. InventoryQuantity object

> Access scope: `read_inventory`. Implements `Node`.

The leaf object returned from `InventoryLevel.quantities`. Three useful fields plus an ID.

| Field       | Type        | Description                                                                                                                  |
| ----------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `id`        | `ID!`       | Stable GID for the `(level, name)` tuple.                                                                                    |
| `name`      | `String!`   | One of: `available`, `on_hand`, `committed`, `reserved`, `incoming`, `damaged`, `safety_stock`, `quality_control`.            |
| `quantity`  | `Int!`      | The integer quantity. Can be negative for `available` (oversold).                                                            |
| `updatedAt` | `DateTime`  | When **this specific state** was last touched. Use this — not `InventoryLevel.updatedAt` — to detect drift in a single state. |

`InventoryQuantity` is only returned from `InventoryLevel.quantities` and `InventoryAdjustmentGroup.changes`. There is no top-level query for it.

### 4.1 Valid `name` values

The eight names listed in section 1.1. Use `inventoryProperties.quantityNames` (section 5.3) to confirm which are enabled in the current shop.

---

## 5. Queries

### 5.1 `inventoryItem(id: ID!): InventoryItem`

Fetch one inventory item by GID. Required `id` argument; returns null if not found.

```graphql
query InventoryItem($id: ID!) {
  inventoryItem(id: $id) {
    id
    sku
    tracked
    inventoryLevels(first: 50) {
      edges { node { id location { id } quantities(names: ["available", "on_hand"]) { name quantity } } }
    }
  }
}
```

Cost: ~1–5 cost units depending on connections.

### 5.2 `inventoryItems(...): InventoryItemConnection!`

List inventory items with pagination and a search query.

**Arguments:**
- `first: Int` / `after: String` — forward pagination.
- `last: Int` / `before: String` — reverse pagination.
- `reverse: Boolean` (default `false`).
- `query: String` — Shopify search syntax over inventory item fields.

**Search query fields:**
- `sku:XYZ-12345` — exact SKU match (case-sensitive).
- `id:1234`, `id:>=1234` — numeric ID with range operators.
- `created_at:>2026-01-01` / `updated_at:>2026-01-01` — ISO-8601 timestamps.
- Boolean operators: `(created_at:>2026-01-01) OR (sku:'element-151')`.

**Page size cap:** 250 per page is the practical maximum (Shopify-wide).

```graphql
query InventoryItems($cursor: String) {
  inventoryItems(first: 250, after: $cursor, query: "updated_at:>2026-05-01") {
    edges {
      cursor
      node { id sku updatedAt }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

### 5.3 `inventoryProperties: InventoryProperties!`

No arguments. Returns the shop's enabled inventory state vocabulary.

```graphql
query InventoryProperties {
  inventoryProperties {
    quantityNames {
      name
      displayName
      isInUse
      belongsTo
      comprises
    }
  }
}
```

Returns one `InventoryQuantityName` per state:

| Field         | Type          | Meaning                                                              |
| ------------- | ------------- | -------------------------------------------------------------------- |
| `name`        | `String!`     | The canonical name — pass this into mutation `name` fields.          |
| `displayName` | `String`      | Localized label for UI ("Available", "On hand", ...).                |
| `isInUse`     | `Boolean!`    | Whether the merchant has enabled this state. **Check before reading.** |
| `belongsTo`   | `[String!]!`  | Aggregate states this name belongs to (e.g., `available` belongs to `on_hand`). |
| `comprises`   | `[String!]!`  | Names that sum into this name (e.g., `on_hand` comprises the other six physical states). |

Typical response:

```json
{
  "inventoryProperties": {
    "quantityNames": [
      { "name": "available", "isInUse": true, "displayName": "Available", "belongsTo": ["on_hand"], "comprises": [] },
      { "name": "committed", "isInUse": true, "displayName": "Committed", "belongsTo": ["on_hand"], "comprises": [] },
      { "name": "damaged", "isInUse": false, "displayName": "Damaged", "belongsTo": ["on_hand"], "comprises": [] },
      { "name": "incoming", "isInUse": true, "displayName": "Incoming", "belongsTo": [], "comprises": [] },
      { "name": "on_hand", "isInUse": true, "displayName": "On hand", "belongsTo": [], "comprises": ["available","committed","damaged","quality_control","reserved","safety_stock"] },
      { "name": "quality_control", "isInUse": false, "displayName": "Quality control", "belongsTo": ["on_hand"], "comprises": [] },
      { "name": "reserved", "isInUse": true, "displayName": "Reserved", "belongsTo": ["on_hand"], "comprises": [] },
      { "name": "safety_stock", "isInUse": false, "displayName": "Safety stock", "belongsTo": ["on_hand"], "comprises": [] }
    ]
  }
}
```

### 5.4 SyncApp angle: which query for which use case

| Use case                                         | Query                                                                                | Notes                                                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| Read N items' available qty at a known location  | `inventoryItem(id) { inventoryLevel(locationId) { quantities(names: ["available"]) { quantity } } }` per item, OR batch via `nodes(ids: [...])` | For ≤10 items, use parallel `inventoryItem` calls. For more, use `nodes(ids)` with inline fragments to stay under the cost budget. |
| List **all** inventory levels for an item        | `inventoryItem(id) { inventoryLevels(first: 50) { ... } }`                           | Paginate. FS locations included by default here; cross-check `location.fulfillmentService` if you need to skip them.            |
| Find all items with a SKU                        | `inventoryItems(first: 1, query: "sku:'XYZ-123'")`                                   | SKU is case-sensitive. Wrap in single quotes if it contains spaces/special chars.                                              |
| Discover which states are enabled                | `inventoryProperties`                                                                | Cache for the session — this rarely changes.                                                                                   |
| Detect drift across all locations                | `productVariants(query: "updated_at:>X") { inventoryItem { inventoryLevels { quantities } } }` | More efficient than walking inventory items directly because the variant connection is what you actually want to reconcile.    |

---

## 6. Mutations

All inventory mutations require the `write_inventory` access scope **plus** a staff user permission. In 2026-04 most write mutations are `@idempotent` — you must pass an idempotency key, see section 6.0.

### 6.0 Idempotency rules (apply to all sections below)

As of **API version 2026-04**:

- `inventoryActivate`, `inventoryAdjustQuantities`, `inventorySetQuantities`, `inventoryMoveQuantities` all **require** the `@idempotent(key: $idempotencyKey)` directive.
- Key format: any unique string. UUIDv4 is the convention (`b8f0b172-1ffc-41ff-90c5-14c254e3c202`).
- TTL: ~24h. A replay within that window returns the original response (or its cached error).
- Replaying the **same key with different parameters** raises `IDEMPOTENCY_KEY_PARAMETER_MISMATCH`.
- Two concurrent in-flight calls with the same key raise `IDEMPOTENCY_CONCURRENT_REQUEST` (retry-able).

SyncApp pattern: derive the key deterministically from `(workspaceId, syncBatchId, itemGid, locationGid, name)` so retries by the worker are absorbed by Shopify, but legitimately-different writes get fresh keys.

---

### 6.1 `inventoryActivate` — stock an item at a location

Activates inventory tracking for a given `(item, location)`. Optional initial quantities. Returns the new `InventoryLevel`.

**Signature:**

```graphql
mutation inventoryActivate(
  $inventoryItemId: ID!
  $locationId: ID!
  $available: Int
  $onHand: Int
  $stockAtLegacyLocation: Boolean
  $idempotencyKey: String!
) {
  inventoryActivate(
    inventoryItemId: $inventoryItemId
    locationId: $locationId
    available: $available
    onHand: $onHand
    stockAtLegacyLocation: $stockAtLegacyLocation
  ) @idempotent(key: $idempotencyKey) {
    inventoryLevel { id quantities(names: ["available","on_hand"]) { name quantity } }
    userErrors { field message }
  }
}
```

**Arguments:**

| Arg                       | Type       | Notes                                                                                                                                                                                                                                                                                                          |
| ------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inventoryItemId`         | `ID!`      | Required. GID of the item.                                                                                                                                                                                                                                                                                     |
| `locationId`              | `ID!`      | Required. GID of the location.                                                                                                                                                                                                                                                                                 |
| `available`               | `Int`      | Optional. Initial available quantity. Defaults to 0.                                                                                                                                                                                                                                                           |
| `onHand`                  | `Int`      | Optional. Initial on_hand quantity. Defaults to 0. Must be ≥ `available`.                                                                                                                                                                                                                                      |
| `stockAtLegacyLocation`   | `Boolean`  | Optional. Allow activation at or away from a fulfillment-service location whose SKU-sharing is off. Default `false`. **Set `true` to activate at a FS location** — otherwise the call fails silently with no FS levels created. |

**Return:** `InventoryActivatePayload`:
- `inventoryLevel: InventoryLevel` — newly activated level.
- `userErrors: [UserError!]!` — generic UserError (no dedicated enum at the time of writing).

**Rate-limit cost:** ~10 (write mutation baseline).

**Example:**

```graphql
mutation Activate(
  $inventoryItemId: ID!
  $locationId: ID!
  $available: Int
  $idempotencyKey: String!
) {
  inventoryActivate(
    inventoryItemId: $inventoryItemId
    locationId: $locationId
    available: $available
  ) @idempotent(key: $idempotencyKey) {
    inventoryLevel {
      id
      quantities(names: ["available"]) { name quantity }
    }
    userErrors { field message }
  }
}
```

Variables:
```json
{
  "inventoryItemId": "gid://shopify/InventoryItem/43729076",
  "locationId": "gid://shopify/Location/346779380",
  "available": 42,
  "idempotencyKey": "6c423d89-5ddd-42b5-a9c5-b4af92657453"
}
```

Response:
```json
{
  "inventoryActivate": {
    "inventoryLevel": {
      "id": "gid://shopify/InventoryLevel/523463154?inventory_item_id=43729076",
      "quantities": [{ "name": "available", "quantity": 42 }]
    },
    "userErrors": []
  }
}
```

---

### 6.2 `inventoryAdjustQuantities` — delta adjust (safe atomic)

The **safe, atomic, delta-based** mutation. Use this for anything that can be expressed as `current ± n`. Supports CAS via `changeFromQuantity` and writes audit metadata (reason, reference document).

**Signature:**

```graphql
mutation inventoryAdjustQuantities(
  $input: InventoryAdjustQuantitiesInput!
  $idempotencyKey: String!
) {
  inventoryAdjustQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      id
      createdAt
      reason
      referenceDocumentUri
      changes { name delta quantityAfterChange ledgerDocumentUri }
      app { id }
      staffMember { id }
    }
    userErrors { field message code }
  }
}
```

**`InventoryAdjustQuantitiesInput` fields:**

| Field                  | Type                          | Required | Description                                                                                                                                                  |
| ---------------------- | ----------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `reason`               | `String`                      | Yes      | One of the reason values in 6.2.2 below.                                                                                                                     |
| `name`                 | `String`                      | Yes      | The quantity state being adjusted. Typically `available` or `on_hand`.                                                                                       |
| `referenceDocumentUri` | `String`                      | No       | Free-form URI for audit trail (e.g., `app://syncapp/sync/group_123/v7`). Required `INVALID_REFERENCE_DOCUMENT` is raised on a `gid://shopify/...` prefix.   |
| `changes`              | `[InventoryChangeInput!]!`    | Yes      | One or more per-item changes (see below). Cap roughly 250 per call.                                                                                          |

**`InventoryChangeInput` fields:**

| Field                | Type      | Required | Description                                                                                                                                                                                                                  |
| -------------------- | --------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `delta`              | `Int!`    | Yes      | Signed integer. Positive to add, negative to remove. Magnitude capped at 2,000,000,000.                                                                                                                                       |
| `inventoryItemId`    | `ID!`     | Yes      |                                                                                                                                                                                                                              |
| `locationId`         | `ID!`     | Yes      |                                                                                                                                                                                                                              |
| `ledgerDocumentUri`  | `String`  | Cond.    | **Forbidden** when adjusting `name: "available"` (raises `INVALID_AVAILABLE_DOCUMENT`). **Required** when adjusting any non-available state (raises `INVALID_QUANTITY_DOCUMENT` if missing). Use a unique URI per ledger entry. |
| `changeFromQuantity` | `Int`     | No       | Optimistic-concurrency expected current value. If supplied and the persisted value doesn't match, the entire mutation fails with `CHANGE_FROM_QUANTITY_STALE` — no partial writes.                                            |

#### 6.2.1 Compare-and-set semantics (`changeFromQuantity`)

- When `changeFromQuantity` is **omitted**, the delta is applied unconditionally.
- When supplied, Shopify reads the persisted value inside the transaction, compares it byte-for-byte to `changeFromQuantity`, and aborts if different (returning `CHANGE_FROM_QUANTITY_STALE`).
- Useful for "I last read 10, increment by -2, but only if it's still 10". If it's now 9 because another write landed first, you want to refetch and re-decide.

> Newer Shopify schemas added a separate `compareQuantity` concept on `inventorySetQuantities` (section 6.3). They are **not** interchangeable: `changeFromQuantity` belongs to `inventoryAdjustQuantities` (delta) and to the `from`/`to` terminals of `inventoryMoveQuantities`; `compareQuantity` belongs to `inventorySetQuantities` (absolute set).

#### 6.2.2 `reason` enum values

The full list accepted in 2026-04:

| Value                     | When to use                                                                |
| ------------------------- | -------------------------------------------------------------------------- |
| `correction`              | Generic merchant-driven correction. Safest default.                        |
| `cycle_count_available`   | Result of an inventory cycle-count audit.                                  |
| `damaged`                 | Adjusting damaged stock.                                                   |
| `movement_created`        | A transfer/shipment was created.                                           |
| `movement_updated`        | A transfer/shipment was edited.                                            |
| `movement_received`       | A transfer landed.                                                         |
| `movement_canceled`       | A transfer was canceled.                                                   |
| `other`                   | None of the above; provide a `referenceDocumentUri` describing your action. |
| `promotion`               | Promotional give-away / sample.                                            |
| `quality_control`         | Moving into/out of QC.                                                     |
| `received`                | Generic receipt of new stock.                                              |
| `reservation_created`     | Reservation lifecycle.                                                     |
| `reservation_deleted`     | Reservation lifecycle.                                                     |
| `reservation_updated`     | Reservation lifecycle.                                                     |
| `restock`                 | Returned product re-shelved.                                               |
| `safety_stock`            | Adjusting buffer.                                                          |
| `shrinkage`               | Unaccounted loss.                                                          |

> SyncApp uses `correction` for sync-driven writes and embeds `app://syncapp/...` in `referenceDocumentUri` so the merchant can grep the inventory history for our changes.

#### 6.2.3 `referenceDocumentUri` examples

- `logistics://some.warehouse/take/2026-02/13`
- `app://syncapp/sync/wsk_<workspaceId>/grp_<groupId>/v<version>`
- `https://docs.example.com/transfer/123`

It must **not** start with `gid://shopify/` — those are reserved and rejected with `INVALID_REFERENCE_DOCUMENT` or `INTERNAL_LEDGER_DOCUMENT`.

#### 6.2.4 `InventoryAdjustQuantitiesUserErrorCode` — every code

| Code                                | Meaning                                                                                                          |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `ADJUST_QUANTITIES_FAILED`          | Generic transient failure. Retry.                                                                                |
| `CHANGE_FROM_QUANTITY_STALE`        | The persisted value didn't match `changeFromQuantity`. Refetch and decide.                                       |
| `IDEMPOTENCY_CONCURRENT_REQUEST`    | Another call with the same key is in flight. Retry after a short backoff.                                        |
| `IDEMPOTENCY_KEY_PARAMETER_MISMATCH`| Same key, different parameters. Use a fresh key for the new payload.                                             |
| `INTERNAL_LEDGER_DOCUMENT`          | `ledgerDocumentUri` started with `gid://shopify/`. Use your own URI scheme.                                      |
| `INVALID_AVAILABLE_DOCUMENT`        | You passed `ledgerDocumentUri` while adjusting `available`. Strip it.                                            |
| `INVALID_INVENTORY_ITEM`            | Item GID is wrong or item not found.                                                                             |
| `INVALID_LEDGER_DOCUMENT`           | The ledger URI is malformed.                                                                                     |
| `INVALID_LOCATION`                  | Location GID is wrong or not found.                                                                              |
| `INVALID_QUANTITY_DOCUMENT`         | You omitted `ledgerDocumentUri` while adjusting a non-available state. Add it.                                   |
| `INVALID_QUANTITY_NAME`             | `name` is not one of the valid eight.                                                                            |
| `INVALID_QUANTITY_TOO_HIGH`         | Total would exceed 2,000,000,000.                                                                                |
| `INVALID_QUANTITY_TOO_LOW`          | Total would go below -2,000,000,000.                                                                             |
| `INVALID_REASON`                    | `reason` is not in the enum.                                                                                     |
| `INVALID_REFERENCE_DOCUMENT`        | `referenceDocumentUri` is malformed or reserved.                                                                 |
| `ITEM_NOT_STOCKED_AT_LOCATION`      | Activate first (or call `inventoryActivate`).                                                                    |
| `MAX_ONE_LEDGER_DOCUMENT`           | All non-available changes in one call must share the same `ledgerDocumentUri`. Split across calls if you need different URIs. |
| `NON_MUTABLE_INVENTORY_ITEM`        | The item is locked (bundle parent, FS-managed, etc.). Don't retry.                                               |
| `SERVICE_UNAVAILABLE`               | Transient. Retry with backoff.                                                                                   |

#### 6.2.5 Example

```graphql
mutation AdjustAvailable(
  $input: InventoryAdjustQuantitiesInput!
  $idempotencyKey: String!
) {
  inventoryAdjustQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      createdAt
      reason
      referenceDocumentUri
      changes(quantityNames: ["available","on_hand"]) {
        name
        delta
        quantityAfterChange
      }
    }
    userErrors { field message code }
  }
}
```

Variables (decrement 4 units of "available" at one location, with CAS):

```json
{
  "input": {
    "reason": "correction",
    "name": "available",
    "referenceDocumentUri": "app://syncapp/sync/wsk_42/grp_99/v17",
    "changes": [{
      "delta": -4,
      "inventoryItemId": "gid://shopify/InventoryItem/30322695",
      "locationId": "gid://shopify/Location/124656943",
      "changeFromQuantity": 1
    }]
  },
  "idempotencyKey": "b8f0b172-1ffc-41ff-90c5-14c254e3c202"
}
```

Response (note: both `available` and `on_hand` are debited because `available` cascades):

```json
{
  "inventoryAdjustQuantities": {
    "inventoryAdjustmentGroup": {
      "createdAt": "2026-04-14T17:33:25Z",
      "reason": "correction",
      "referenceDocumentUri": "app://syncapp/sync/wsk_42/grp_99/v17",
      "changes": [
        { "name": "available", "delta": -4, "quantityAfterChange": -3 },
        { "name": "on_hand", "delta": -4, "quantityAfterChange": 6 }
      ]
    },
    "userErrors": []
  }
}
```

---

### 6.3 `inventorySetQuantities` — absolute set (destructive)

The **destructive, absolute** mutation. Replaces the named quantity with a new value at one or more `(item, location)` pairs. Use it only when you genuinely have a fresh authoritative number; otherwise prefer `adjust`.

> CRITICAL SIDE EFFECT for SyncApp: setting `name: "available"` to `N` computes `delta = N - prior_available` and applies that same delta to `on_hand`. If you call `set(available, 100)` repeatedly because a stale read keeps showing 90, you keep dragging `on_hand` down by 10 each time you re-set after a concurrent committed-state change. This is the **buffer-compounding bug** we fixed in commit 1a21577. Use `inventoryAdjustQuantities` for relative changes.

**Signature:**

```graphql
mutation inventorySetQuantities(
  $input: InventorySetQuantitiesInput!
  $idempotencyKey: String!
) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      id
      createdAt
      reason
      referenceDocumentUri
      changes { name delta quantityAfterChange }
    }
    userErrors { field message code }
  }
}
```

**`InventorySetQuantitiesInput` fields:**

| Field                    | Type                            | Required | Description                                                                                          |
| ------------------------ | ------------------------------- | -------- | ---------------------------------------------------------------------------------------------------- |
| `name`                   | `String`                        | Yes      | **Only `available` or `on_hand`** are accepted. Other names raise `INVALID_NAME`.                    |
| `reason`                 | `String`                        | Yes      | Subset of the adjust reasons (`correction`, `inventory_transfer`, `stock_reconciliation`, `warehouse_transfer`, plus standard reasons). |
| `referenceDocumentUri`   | `String`                        | No       | Audit URI.                                                                                           |
| `quantities`             | `[InventoryQuantityInput!]!`    | Yes      | One entry per `(item, location)` you want to set.                                                    |
| `ignoreCompareQuantity`  | `Boolean`                       | No       | Default `false`. When `false`, every quantity entry **must** include `compareQuantity` (or the call fails with `COMPARE_QUANTITY_REQUIRED`). When `true`, skip the CAS check entirely. |

**`InventoryQuantityInput` fields:**

| Field             | Type    | Required | Description                                                                                                                       |
| ----------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `inventoryItemId` | `ID!`   | Yes      |                                                                                                                                   |
| `locationId`      | `ID!`   | Yes      |                                                                                                                                   |
| `quantity`        | `Int!`  | Yes      | The absolute target value. Range `-1,000,000,000` to `1,000,000,000`.                                                              |
| `compareQuantity` | `Int`   | Cond.    | Required unless `ignoreCompareQuantity: true`. The value you expect Shopify to currently hold. Stale value → `COMPARE_QUANTITY_STALE`. |

#### 6.3.1 `compareQuantity` vs `changeFromQuantity`

These are the two CAS mechanisms; pick by mutation type:

| Mutation                     | Field                 | Semantics                                                                 |
| ---------------------------- | --------------------- | ------------------------------------------------------------------------- |
| `inventoryAdjustQuantities`  | `changeFromQuantity`  | Optional. If set, mutation aborts on mismatch.                            |
| `inventoryMoveQuantities`    | `changeFromQuantity`  | Optional on each `from`/`to` terminal.                                    |
| `inventorySetQuantities`     | `compareQuantity`     | **Required** unless `ignoreCompareQuantity: true`. Mutation aborts on mismatch. |

History note: pre-2026 versions used `changeFromQuantity` everywhere. The split into `compareQuantity` for `set` makes the contract clearer ("I want to **set** quantity to N, **only if** current is X").

#### 6.3.2 `InventorySetQuantitiesUserErrorCode` — every code

| Code                                       | Meaning                                                                          |
| ------------------------------------------ | -------------------------------------------------------------------------------- |
| `CHANGE_FROM_QUANTITY_STALE`               | (Legacy compatibility path.) Persisted value differs.                            |
| `COMPARE_QUANTITY_REQUIRED`                | Missing `compareQuantity` and `ignoreCompareQuantity` is false.                  |
| `COMPARE_QUANTITY_STALE`                   | `compareQuantity` doesn't match persisted value.                                 |
| `IDEMPOTENCY_CONCURRENT_REQUEST`           | Retry-able.                                                                      |
| `IDEMPOTENCY_KEY_PARAMETER_MISMATCH`       | Use a fresh key for a different payload.                                         |
| `INVALID_INVENTORY_ITEM`                   |                                                                                  |
| `INVALID_LOCATION`                         |                                                                                  |
| `INVALID_NAME`                             | `name` must be `available` or `on_hand`.                                         |
| `INVALID_QUANTITY_NEGATIVE`                | Target quantity is negative for a state that disallows it.                       |
| `INVALID_QUANTITY_TOO_HIGH`                | > 1,000,000,000.                                                                 |
| `INVALID_QUANTITY_TOO_LOW`                 | < -1,000,000,000.                                                                |
| `INVALID_REASON`                           |                                                                                  |
| `INVALID_REFERENCE_DOCUMENT`               |                                                                                  |
| `ITEM_NOT_STOCKED_AT_LOCATION`             | Activate first.                                                                  |
| `NO_DUPLICATE_INVENTORY_ITEM_ID_GROUP_ID_PAIR` | You cannot include the same `(itemId, locationId)` twice in one call.        |
| `NON_MUTABLE_INVENTORY_ITEM`               | Item is a parent bundle / FS-managed.                                            |

#### 6.3.3 Example

```graphql
mutation SetAvailable(
  $input: InventorySetQuantitiesInput!
  $idempotencyKey: String!
) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      createdAt
      reason
      changes { name delta }
    }
    userErrors { field message code }
  }
}
```

Variables (set available to 11 at one location; previous available was 1, so delta is +10):

```json
{
  "input": {
    "name": "available",
    "reason": "correction",
    "referenceDocumentUri": "app://syncapp/reconcile/wsk_42/2026-05-24",
    "quantities": [{
      "inventoryItemId": "gid://shopify/InventoryItem/30322695",
      "locationId": "gid://shopify/Location/124656943",
      "quantity": 11,
      "compareQuantity": 1
    }]
  },
  "idempotencyKey": "2a3a92b8-0c8e-4af1-9eac-1a2b3c4d5e6f"
}
```

Response — note both `available` AND `on_hand` shifted by +10:

```json
{
  "inventorySetQuantities": {
    "inventoryAdjustmentGroup": {
      "createdAt": "2026-04-14T17:33:25Z",
      "reason": "correction",
      "changes": [
        { "name": "available", "delta": 10 },
        { "name": "on_hand", "delta": 10 }
      ]
    },
    "userErrors": []
  }
}
```

---

### 6.4 `inventoryMoveQuantities` — move between states or locations

The "reallocate without inventing or destroying stock" mutation. Each `change` declares a `from` terminal and a `to` terminal; the same `quantity` is debited from one and credited to the other.

**Signature:**

```graphql
mutation inventoryMoveQuantities(
  $input: InventoryMoveQuantitiesInput!
  $idempotencyKey: String!
) {
  inventoryMoveQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      id
      createdAt
      reason
      referenceDocumentUri
      changes(quantityNames: ["available","on_hand","reserved","damaged"]) {
        name delta quantityAfterChange ledgerDocumentUri
      }
    }
    userErrors { field message code }
  }
}
```

**`InventoryMoveQuantitiesInput` fields:**

| Field                  | Type                              | Required | Description                                                                |
| ---------------------- | --------------------------------- | -------- | -------------------------------------------------------------------------- |
| `reason`               | `String`                          | Yes      | Same enum as `adjust` (section 6.2.2).                                     |
| `referenceDocumentUri` | `String`                          | Yes      | **Required**, unlike `adjust` and `set`. Audit URI.                        |
| `changes`              | `[InventoryMoveQuantityChange!]!` | Yes      | One or more moves.                                                         |

**`InventoryMoveQuantityChange` fields:**

| Field             | Type                                  | Required | Description                                                                 |
| ----------------- | ------------------------------------- | -------- | --------------------------------------------------------------------------- |
| `inventoryItemId` | `ID!`                                 | Yes      | The item being moved.                                                       |
| `quantity`        | `Int!`                                | Yes      | Magnitude. Must be > 0.                                                     |
| `from`            | `InventoryMoveQuantityTerminalInput!` | Yes      | Where stock leaves.                                                         |
| `to`              | `InventoryMoveQuantityTerminalInput!` | Yes      | Where stock arrives.                                                        |

**`InventoryMoveQuantityTerminalInput` fields:**

| Field                | Type      | Required | Description                                                                                            |
| -------------------- | --------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `locationId`         | `ID!`     | Yes      | The two terminals **must** share the same `locationId` — cross-location moves are not supported here (use transfers/orders). |
| `name`               | `String!` | Yes      | A quantity-state name (one of the eight). `from.name` and `to.name` must differ.                       |
| `ledgerDocumentUri`  | `String`  | Cond.    | Required if `name` is non-`available` (same rule as `adjust`).                                         |
| `changeFromQuantity` | `Int`     | No       | CAS expected value for the corresponding state.                                                        |

#### 6.4.1 `InventoryMoveQuantitiesUserErrorCode` — every code

| Code                                | Meaning                                                                                            |
| ----------------------------------- | -------------------------------------------------------------------------------------------------- |
| `CHANGE_FROM_QUANTITY_STALE`        | A terminal's `changeFromQuantity` didn't match.                                                    |
| `DIFFERENT_LOCATIONS`               | `from.locationId !== to.locationId`. Cross-location moves are not supported.                       |
| `IDEMPOTENCY_CONCURRENT_REQUEST`    | Retry-able.                                                                                        |
| `IDEMPOTENCY_KEY_PARAMETER_MISMATCH`|                                                                                                    |
| `INTERNAL_LEDGER_DOCUMENT`          | A `gid://shopify/...` ledger URI was supplied.                                                     |
| `INVALID_AVAILABLE_DOCUMENT`        | `ledgerDocumentUri` set on an `available` terminal.                                                |
| `INVALID_INVENTORY_ITEM`            |                                                                                                    |
| `INVALID_LEDGER_DOCUMENT`           |                                                                                                    |
| `INVALID_LOCATION`                  |                                                                                                    |
| `INVALID_QUANTITY_DOCUMENT`         | Missing `ledgerDocumentUri` on a non-available terminal.                                           |
| `INVALID_QUANTITY_NAME`             |                                                                                                    |
| `INVALID_QUANTITY_NEGATIVE`         | Resulting quantity at a terminal would be negative.                                                |
| `INVALID_QUANTITY_TOO_HIGH`         | > 2,000,000,000.                                                                                   |
| `INVALID_REASON`                    |                                                                                                    |
| `INVALID_REFERENCE_DOCUMENT`        |                                                                                                    |
| `ITEM_NOT_STOCKED_AT_LOCATION`      |                                                                                                    |
| `MAXIMUM_LEDGER_DOCUMENT_URIS`      | At most 2 distinct `ledgerDocumentUri` values across all changes in one call.                      |
| `MOVE_QUANTITIES_FAILED`            | Transient. Retry.                                                                                  |
| `NON_MUTABLE_INVENTORY_ITEM`        | Bundle parent / FS-managed.                                                                        |
| `SAME_QUANTITY_NAME`                | `from.name === to.name`. Pick different states.                                                    |
| `SERVICE_UNAVAILABLE`               | Transient.                                                                                         |

#### 6.4.2 Example: move 10 units from `available` into `reserved`

```graphql
mutation MoveAvailableToReserved(
  $input: InventoryMoveQuantitiesInput!
  $quantityNames: [String!]
  $idempotencyKey: String!
) {
  inventoryMoveQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      createdAt
      reason
      referenceDocumentUri
      changes(quantityNames: $quantityNames) {
        name delta ledgerDocumentUri
      }
    }
    userErrors { field message code }
  }
}
```

Variables:

```json
{
  "input": {
    "reason": "reservation_created",
    "referenceDocumentUri": "logistics://warehouse/take/2026-02-23T13:14:15Z",
    "changes": [{
      "quantity": 10,
      "inventoryItemId": "gid://shopify/InventoryItem/30322695",
      "from": {
        "locationId": "gid://shopify/Location/124656943",
        "name": "available",
        "ledgerDocumentUri": null,
        "changeFromQuantity": 100
      },
      "to": {
        "locationId": "gid://shopify/Location/124656943",
        "name": "reserved",
        "ledgerDocumentUri": "logistics://warehouse/orders/2026-02-04/2",
        "changeFromQuantity": 0
      }
    }]
  },
  "quantityNames": ["available","reserved","on_hand"],
  "idempotencyKey": "2824bdc5-5365-45a0-9c81-ad8c0661d4f0"
}
```

Response — `available` -10, `reserved` +10, **`on_hand` unchanged** because both states count toward it:

```json
{
  "inventoryMoveQuantities": {
    "inventoryAdjustmentGroup": {
      "createdAt": "2026-04-13T22:17:50Z",
      "reason": "reservation_created",
      "referenceDocumentUri": "logistics://warehouse/take/2026-02-23T13:14:15Z",
      "changes": [
        { "name": "available", "delta": -10, "ledgerDocumentUri": null },
        { "name": "reserved", "delta": 10, "ledgerDocumentUri": "logistics://warehouse/orders/2026-02-04/2" }
      ]
    },
    "userErrors": []
  }
}
```

---

### 6.5 `inventoryDeactivate` — unstock at a location

Removes the `InventoryLevel` for one `(item, location)` pair. Inverse of `inventoryActivate`. Cannot be undone — you'd `inventoryActivate` again with new quantities.

**Signature:**

```graphql
mutation inventoryDeactivate($inventoryLevelId: ID!) {
  inventoryDeactivate(inventoryLevelId: $inventoryLevelId) {
    userErrors { field message }
  }
}
```

**Arguments:**

| Arg                | Type    | Description                                                                                                   |
| ------------------ | ------- | ------------------------------------------------------------------------------------------------------------- |
| `inventoryLevelId` | `ID!`   | The full level GID — including the `?inventory_item_id=...` suffix from `InventoryLevel.id`.                  |

**Return:** `InventoryDeactivatePayload { userErrors: [UserError!]! }` — no echo of the now-gone level.

**Idempotency:** Not currently `@idempotent` (a retry after success is harmless because the level no longer exists, but a retry will return a `userErrors` complaint).

**Pre-flight check:** Read `inventoryLevel.canDeactivate` first. If false, `inventoryLevel.deactivationAlert` explains why (committed/incoming/reserved stock, only-stocked-location, etc.) — show that to the user; don't try the mutation.

**Example:**

```json
{ "inventoryLevelId": "gid://shopify/InventoryLevel/820859520?inventory_item_id=826867926" }
```

Response on success:

```json
{ "inventoryDeactivate": { "userErrors": [] } }
```

---

### 6.6 `inventoryItemUpdate` — edit item metadata

Updates the non-quantity fields on an inventory item — SKU, cost, tracked, shipping/customs metadata, dimensions. **Does not touch any quantities.**

**Signature:**

```graphql
mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
  inventoryItemUpdate(id: $id, input: $input) {
    inventoryItem {
      id sku tracked requiresShipping
      countryCodeOfOrigin provinceCodeOfOrigin harmonizedSystemCode
      unitCost { amount currencyCode }
      measurement { weight { unit value } }
    }
    userErrors { field message }
  }
}
```

**`InventoryItemInput` fields:**

| Field                         | Type                                  | Required | Description                                                  |
| ----------------------------- | ------------------------------------- | -------- | ------------------------------------------------------------ |
| `sku`                         | `String`                              | No       | The new SKU.                                                 |
| `cost`                        | `Decimal`                             | No       | Unit cost in the shop's default currency.                    |
| `tracked`                     | `Boolean`                             | No       | Toggle inventory tracking.                                   |
| `requiresShipping`            | `Boolean`                             | No       | Whether shipping is physically required.                     |
| `countryCodeOfOrigin`         | `CountryCode`                         | No       | ISO 3166-1 alpha-2.                                          |
| `provinceCodeOfOrigin`        | `String`                              | No       | ISO 3166-2.                                                  |
| `harmonizedSystemCode`        | `String`                              | No       | 6–13 digit HS code.                                          |
| `countryHarmonizedSystemCodes`| `[CountryHarmonizedSystemCodeInput!]` | No       | Per-country HS overrides.                                    |
| `measurement`                 | `InventoryItemMeasurementInput`       | No       | Weight and shipping package.                                 |

**`InventoryItemMeasurementInput` fields:**

| Field                | Type           | Description                                       |
| -------------------- | -------------- | ------------------------------------------------- |
| `weight`             | `WeightInput`  | `{ unit: WeightUnit!, value: Float! }`            |
| `shippingPackageId`  | `ID`           | A shipping-package definition to associate.       |

**Return:** `InventoryItemUpdatePayload { inventoryItem: InventoryItem, userErrors: [UserError!]! }`. No dedicated error-code enum; errors come back as standard `UserError`.

**Example:**

```graphql
mutation UpdateInventoryItem($id: ID!, $input: InventoryItemInput!) {
  inventoryItemUpdate(id: $id, input: $input) {
    inventoryItem { id sku unitCost { amount currencyCode } tracked }
    userErrors { field message }
  }
}
```

Variables:

```json
{
  "id": "gid://shopify/InventoryItem/30322695",
  "input": {
    "sku": "ELEMENT-151-V2",
    "cost": "12.50",
    "tracked": true,
    "countryCodeOfOrigin": "US",
    "harmonizedSystemCode": "611020"
  }
}
```

---

### 6.7 `inventoryBulkToggleActivation` — bulk activate/deactivate

Activate or deactivate one inventory item across many locations in a single call. Cheaper than calling `inventoryActivate`/`inventoryDeactivate` per location.

**Signature:**

```graphql
mutation inventoryBulkToggleActivation(
  $inventoryItemId: ID!
  $inventoryItemUpdates: [InventoryBulkToggleActivationInput!]!
) {
  inventoryBulkToggleActivation(
    inventoryItemId: $inventoryItemId
    inventoryItemUpdates: $inventoryItemUpdates
  ) {
    inventoryItem { id locationsCount { count } }
    inventoryLevels { id location { id } isActive }
    userErrors { field message code }
  }
}
```

**Arguments:**

| Arg                     | Type                                       | Description                                                                  |
| ----------------------- | ------------------------------------------ | ---------------------------------------------------------------------------- |
| `inventoryItemId`       | `ID!`                                      | The item to modify.                                                          |
| `inventoryItemUpdates`  | `[InventoryBulkToggleActivationInput!]!`   | One entry per location.                                                      |

**`InventoryBulkToggleActivationInput` fields:**

| Field        | Type        | Required | Description                                |
| ------------ | ----------- | -------- | ------------------------------------------ |
| `locationId` | `ID`        | Yes      | Location GID.                              |
| `activate`   | `Boolean`   | Yes      | `true` to stock, `false` to unstock.       |

**Return:** `InventoryBulkToggleActivationPayload`:
- `inventoryItem: InventoryItem` — refreshed item.
- `inventoryLevels: [InventoryLevel!]` — only the **activated** levels (deactivated rows return nothing).
- `userErrors: [InventoryBulkToggleActivationUserError!]!`.

#### 6.7.1 `InventoryBulkToggleActivationUserErrorCode` — every code

| Code                                          | Meaning                                                                                                        |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `CANNOT_DEACTIVATE_FROM_ONLY_LOCATION`        | Cannot unstock an item from its only stocked location.                                                         |
| `COMMITTED_INVENTORY_AT_LOCATION`             | Cannot unstock — committed (order-attached) inventory exists.                                                  |
| `FAILED_TO_STOCK_AT_LOCATION`                 | Generic activation failure.                                                                                    |
| `FAILED_TO_UNSTOCK_FROM_LOCATION`             | Generic deactivation failure.                                                                                  |
| `GENERIC_ERROR`                               | Catch-all.                                                                                                     |
| `INCOMING_INVENTORY_AT_LOCATION`              | Cannot unstock — incoming (transfer) inventory exists.                                                          |
| `INVENTORY_ITEM_NOT_FOUND`                    |                                                                                                                |
| `INVENTORY_MANAGED_BY_3RD_PARTY`              | Cannot stock at this location because a FulfillmentService owns it.                                            |
| `INVENTORY_MANAGED_BY_SHOPIFY`                | Cannot stock at this location because Shopify owns it (the inverse — you tried to add a FS-managed location to a Shopify-only item). |
| `LOCATION_NOT_FOUND`                          |                                                                                                                |
| `MISSING_SKU`                                 | Cannot stock — the variant has no SKU and the location requires one.                                           |
| `RESERVED_INVENTORY_AT_LOCATION`              | Cannot unstock — reserved inventory exists ("unavailable" in older docs).                                      |
| `COMMITTED_AND_INCOMING_INVENTORY_AT_LOCATION`| **Deprecated** — split into the two more-specific codes above.                                                 |

Maximum locations per call is not documented but in practice keep it under 50 to stay under the cost ceiling.

---

## 7. SyncApp gotchas

This section consolidates inventory-API foot-guns discovered while building SyncApp. Every item here is referenced from at least one production bug.

### 7.1 `inventorySetQuantities(name: "available")` reduces `on_hand` as a side effect

Documented in section 6.3 and reinforced here. Setting `available` to `N` applies the delta `N - prior_available` to **both** `available` and `on_hand`. If you have a per-store safety buffer that subtracts from `available`, and you call `set(available)` after each webhook, every iteration shrinks `on_hand`. The fix is to either:

- Use `inventoryAdjustQuantities` with explicit deltas computed once per sync cycle, or
- Use `inventorySetQuantities(name: "on_hand", ...)` to set the authoritative physical count and let Shopify recompute `available` based on `committed`/`reserved`, or
- (What we did in commit 1a21577) Stop re-applying the buffer; treat `available` after buffer as the authoritative target and use CAS so a stale buffer doesn't compound.

### 7.2 Fulfillment-service locations reject `set` writes

FS-owned locations whose `Location.fulfillmentService` is non-null and `Location.fulfillmentService.handle` is not `manual` will reject `inventorySetQuantities(name: "available")` with `NON_MUTABLE_INVENTORY_ITEM` or `INVALID_LOCATION`. Symptoms in SyncApp's redistribution worker: `userErrors` come back, the level appears not updated, the worker logs success because we weren't checking `userErrors[].code`. Two defenses:

1. **Filter FS locations** before allocation: `locations(first: 50)` defaults to **excluding** FS locations; only include them via `includeLegacy: true` when you want to read their stock.
2. **Treat `NON_MUTABLE_INVENTORY_ITEM` as fatal-for-this-target** in the orchestrator — don't retry, mark the level as read-only and reallocate elsewhere.

### 7.3 InventoryLevel writes echo back as webhooks

Every successful write to `available`/`on_hand` fires `inventory_levels/update` to your webhook endpoint within seconds. The payload has no app attribution. Without self-push suppression, your worker re-syncs the level, which fires another webhook, etc.

SyncApp's defense lives in [app/lib/sync-origin.server.ts](../../../Desktop/SyncApp/app/lib/sync-origin.server.ts):
- `markAsSelfPush(itemGid, locationGid, qty)` writes `(itemGid, locationGid, qty)` to Redis with a 60s TTL **before** the mutation is sent.
- The webhook worker calls `isSelfPushAsync(itemGid, locationGid, qty)` and drops the event if it matches.

Tighten the suppression key to include `qty` (not just `(item, location)`) so a webhook from a *different* (concurrent) merchant edit isn't suppressed.

### 7.4 `locations(first: 50)` silently excludes FS locations

The Shopify default of `includeLegacy: false` means that a sync app querying `locations(first: 50)` will not see fulfillment-service-app locations. If you need to discover **all** locations the merchant has (e.g., to know which `(item, location)` pairs to track), pass `includeLegacy: true` **and** `includeInactive: true`:

```graphql
query AllLocations {
  locations(first: 50, includeLegacy: true, includeInactive: true) {
    edges {
      node { id name isActive fulfillmentService { handle } }
    }
  }
}
```

Then filter writes by `fulfillmentService == null` (or `.handle === "manual"`) before calling `inventorySetQuantities`/`inventoryAdjustQuantities`.

### 7.5 `inventoryAdjustQuantities` is the safe choice; `inventorySetQuantities` is destructive

| Property                              | `adjust`                    | `set`                                                          |
| ------------------------------------- | --------------------------- | -------------------------------------------------------------- |
| Semantics                             | Relative delta              | Absolute value                                                 |
| Effect of running twice (no key)      | Doubled delta               | Same final value (last write wins)                             |
| Side effect on `on_hand`              | Equal to delta on `available` | Equal to `target - prior` on `available`                     |
| Concurrent safety                     | Use `changeFromQuantity`    | **Must** use `compareQuantity` unless `ignoreCompareQuantity:true` |
| Use when                              | You computed a delta        | You have a fresh authoritative absolute number                 |

Default to `adjust`. Reach for `set` only after a reconciliation pass that you trust more than the local delta.

### 7.6 `compareQuantity` vs `changeFromQuantity` history

- Pre-2026 schemas: only `changeFromQuantity`, on both `adjust` and `set` inputs.
- 2026-01: `inventorySetQuantities` introduced `compareQuantity` as a clearer name for the "set-only" CAS check. `ignoreCompareQuantity` flag added to opt out per-call.
- 2026-04: `@idempotent` directive becomes mandatory on `activate`, `adjust`, `set`, `move`.

When porting older code, change `changeFromQuantity` → `compareQuantity` inside `InventoryQuantityInput` and add `ignoreCompareQuantity: true` if your code was setting `changeFromQuantity: 0` as a "don't care" signal.

### 7.7 `committed` and friends don't fire webhooks

Only `available` and `on_hand` changes produce `inventory_levels/update`. Order-driven changes to `committed`, draft-driven changes to `reserved`, and any move into `damaged`/`safety_stock`/`quality_control` are invisible to your real-time pipeline.

SyncApp consequence: the fast-reconciliation worker (10-minute cycle) MUST re-read `committed` to detect drift between Shopify's order subsystem and our pool accounting. A pure webhook-driven model leaks here.

### 7.8 `ledgerDocumentUri` rules

- **Required** when adjusting/moving any non-`available` state.
- **Forbidden** when adjusting/moving `available` (raises `INVALID_AVAILABLE_DOCUMENT`).
- All non-available changes in one call **must** share the same URI (`adjust`), or differ by at most one URI per terminal (`move`, with `MAXIMUM_LEDGER_DOCUMENT_URIS` capping the total at 2).
- Must not start with `gid://shopify/` (raises `INTERNAL_LEDGER_DOCUMENT`).

### 7.9 InventoryLevel GID format

The level GID returned from `InventoryLevel.id` always includes the query-string suffix:

```
gid://shopify/InventoryLevel/523463154?inventory_item_id=43729076
```

When you store or look up levels via the generic `node(id:)` query, pass the **full** GID including the `?inventory_item_id=` segment. Stripping it breaks lookups.

---

## 8. Decision tree: which mutation should I use?

```
+- I want to ...
|
|-- Read current quantities
|   `-> Query: inventoryItem(id).inventoryLevel(locationId).quantities(names)
|
|-- Add an item to a new location
|   `-> Mutation: inventoryActivate(itemId, locationId, available?, onHand?)
|       Use stockAtLegacyLocation:true if locationId is a fulfillment-service location.
|
|-- Remove an item from a location entirely
|   `-> 1. Read inventoryLevel.canDeactivate (must be true; if false, show deactivationAlert)
|       2. Mutation: inventoryDeactivate(inventoryLevelId)
|
|-- Stock/unstock the SAME item across many locations in one shot
|   `-> Mutation: inventoryBulkToggleActivation(itemId, [{locationId, activate}])
|
|-- Change available/on_hand by a known delta (e.g. -2 for a sale, +10 for restock)
|   `-> Mutation: inventoryAdjustQuantities
|       - name: "available" (or "on_hand")
|       - changes[].delta: signed int
|       - For non-available states, also supply ledgerDocumentUri.
|       - For CAS, supply changeFromQuantity.
|
|-- Replace available/on_hand with an absolute number from an authoritative system
|   `-> Mutation: inventorySetQuantities
|       - name: "available" or "on_hand"
|       - quantities[].quantity: absolute int
|       - quantities[].compareQuantity: REQUIRED (or set ignoreCompareQuantity:true)
|       - WARNING: setting "available" cascades to on_hand by the same delta.
|
|-- Reclassify stock between states at the same location (e.g. available -> damaged)
|   `-> Mutation: inventoryMoveQuantities
|       - changes[].from.{locationId, name}
|       - changes[].to.{locationId, name}   (locationId MUST equal from.locationId)
|       - changes[].quantity (positive)
|       - referenceDocumentUri is REQUIRED on this mutation.
|       - For non-available terminals, supply ledgerDocumentUri.
|
|-- Move stock between LOCATIONS
|   `-> Not supported by inventoryMoveQuantities (raises DIFFERENT_LOCATIONS).
|       Use a Transfer / draft order / per-side adjust pair instead, or:
|       1. inventoryAdjustQuantities(name:"available", delta:-N) at source
|       2. inventoryAdjustQuantities(name:"available", delta:+N) at destination
|       Make both calls share the same referenceDocumentUri so the audit trail links them.
|
|-- Change item metadata (SKU, cost, weight, HS code, tracked flag)
|   `-> Mutation: inventoryItemUpdate(id, input)
|
|-- Sync inventory from an external source of truth across multiple Shopify stores
|   `-> Default: inventoryAdjustQuantities with a per-(store,group,version) referenceDocumentUri.
|       Use changeFromQuantity for optimistic concurrency.
|       Only fall back to inventorySetQuantities when reconciliation tells you the local model is stale.
|       Always markAsSelfPush BEFORE the mutation to suppress the echo webhook.
```

### 8.1 Operation cheat-sheet

| Goal                                  | Best mutation                  | Required fields                                                                      | Key warning                                                                  |
| ------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Sale-driven decrement                 | `inventoryAdjustQuantities`    | `name:"available"`, `delta:-N`, `reason:"correction"` (or do nothing — orders auto-update) | Don't double-write what orders already do.                                   |
| Restock receiving                     | `inventoryAdjustQuantities`    | `name:"on_hand"`, `delta:+N`, `reason:"received"`, `ledgerDocumentUri` required.    | `on_hand` increase flows into `available` automatically.                     |
| Periodic reconciliation               | `inventorySetQuantities`       | `name:"available"`, `quantity`, `compareQuantity`, `reason:"stock_reconciliation"`. | Pair every set with a fresh compareQuantity from the same read transaction.  |
| Move damaged out of sale              | `inventoryMoveQuantities`      | `from{name:"available"}`, `to{name:"damaged", ledgerDocumentUri:...}`, `quantity:N`. | `referenceDocumentUri` REQUIRED; `available` terminal must not have a ledger URI. |
| Stock new SKU at a 3PL                | `inventoryActivate`            | `inventoryItemId`, `locationId`, `stockAtLegacyLocation:true`.                       | Verify the SKU is set first (or `MISSING_SKU`).                              |
| Remove a discontinued SKU at one site | `inventoryDeactivate`          | `inventoryLevelId` (with `?inventory_item_id=` suffix).                              | Read `canDeactivate` first.                                                  |
| Add SKU to 5 retail locations         | `inventoryBulkToggleActivation`| `inventoryItemId`, `[{locationId, activate:true}]×5`.                                | Watch for `INVENTORY_MANAGED_BY_3RD_PARTY` on FS locations.                  |

---

## Appendix: API version notes

This reference targets **API version 2026-04 (April26)**, which is what SyncApp pins in [shopify.app.toml](../../../Desktop/SyncApp/shopify.app.toml), [app/shopify.server.ts](../../../Desktop/SyncApp/app/shopify.server.ts), and the centralized mutation strings in [app/graphql/mutations.ts](../../../Desktop/SyncApp/app/graphql/mutations.ts).

Differences vs earlier versions worth knowing if you read older code:

- 2025-10 → 2026-01: `compareQuantity` and `ignoreCompareQuantity` added to `inventorySetQuantities` (replacing the older `changeFromQuantity` usage in that input).
- 2026-01 → 2026-04: `@idempotent` directive becomes **required** on `inventoryActivate`, `inventoryAdjustQuantities`, `inventorySetQuantities`, `inventoryMoveQuantities`. Code that omitted the directive (legal in 2026-01 as optional) now fails with a directive-required error.
- 2026-04 deprecation: `InventoryLevel.scheduledChanges` is flagged deprecated though still functional.

When you bump versions, run the type generator (`npx shopify api codegen`) and search the codebase for `changeFromQuantity` inside `inventorySetQuantities` payloads — those are the lines most likely to need a rename to `compareQuantity`.
