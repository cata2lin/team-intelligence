# Shopify Admin GraphQL API — Orders, Fulfillment, Refunds, Returns, Draft Orders

Reference for SyncApp's order-import + sales-velocity pipeline. Authoritative against the live Shopify docs (API version `2026-04` / `latest`, fetched 2026-05-24). All GIDs use `gid://shopify/...` format.

**Required scopes (cheat sheet):**

| Resource | Read | Write |
|---|---|---|
| Orders (last 60 days) | `read_orders` | `write_orders` |
| Orders (all history) | `read_all_orders` + `read_orders` | `write_orders` |
| Marketplace orders | `read_marketplace_orders` | `write_marketplace_orders` |
| Fulfillment orders (own) | `read_merchant_managed_fulfillment_orders` | `write_merchant_managed_fulfillment_orders` |
| Fulfillment orders (assigned to FS) | `read_assigned_fulfillment_orders` | `write_assigned_fulfillment_orders` |
| Fulfillment orders (3p) | `read_third_party_fulfillment_orders` | `write_third_party_fulfillment_orders` |
| Returns | `read_returns` | `write_returns` |
| Draft orders | `read_draft_orders` | `write_draft_orders` |

SyncApp currently requests `write_orders`, `read_all_orders`, `read_orders`, `write_inventory`, `read_inventory`, `read_products`, `read_locations`, `read_fulfillments`, `write_fulfillments`.

---

## Table of contents

1. [Order object — full schema](#order-object--full-schema)
2. [LineItem object — full schema](#lineitem-object--full-schema)
3. [Order search query syntax](#order-search-query-syntax)
4. [orders, order, ordersCount queries](#queries-orders-order-orderscount)
5. [Fulfillment + FulfillmentOrder model](#fulfillment--fulfillmentorder-model)
6. [Refund object + refundCreate workflow](#refund-object--refundcreate-workflow)
7. [Return object + return lifecycle](#return-object--return-lifecycle)
8. [Draft Order workflow](#draft-order-workflow)
9. [Mutations reference (orderCreate / orderUpdate / orderCancel / orderClose / orderMarkAsPaid / orderEdit\*)](#order-mutations)
10. [Webhooks emitted per lifecycle event](#webhooks-emitted-per-lifecycle-event)
11. [Inventory side effects](#inventory-side-effects-summary)
12. [Pagination patterns](#pagination-patterns)
13. [SyncApp angles — what to import and how](#syncapp-angles)

---

## Order object — full schema

The `Order` is the central commerce object. Last 60 days only by default; older history requires `read_all_orders`.

### Core identity

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | `gid://shopify/Order/1234567890` |
| `legacyResourceId` | `UnsignedInt64!` | REST numeric ID, e.g. `1234567890` |
| `name` | `String!` | Admin display name, e.g. `#1001` |
| `number` | `Int!` | Order number; NOT guaranteed consecutive or unique across shops |
| `confirmationNumber` | `String` | Customer-facing alphanumeric (e.g. `XPAV284CT`); NOT guaranteed unique |

### Timestamps

| Field | Type | Notes |
|---|---|---|
| `createdAt` | `DateTime!` | When created in Shopify |
| `processedAt` | `DateTime!` | When the order was processed (canonical for sales-velocity windows) |
| `updatedAt` | `DateTime!` | Last modification |
| `closedAt` | `DateTime` | Null when open |
| `cancelledAt` | `DateTime` | Null when not cancelled |

**SyncApp note:** For sales velocity we use `processedAt` (matches Shopify Analytics + survives back-dated imports). `createdAt` is what `ORDERS_CREATE` webhooks fire on; the two diverge for imported orders, POS offline orders, and `orderCreate` mutations.

### Customer + contact

| Field | Type | Notes |
|---|---|---|
| `customer` | `Customer` | Null for guest checkout |
| `email` | `String` | |
| `phone` | `String` | |
| `customerLocale` | `String` | e.g. `en`, `fr-CA` |
| `customerAcceptsMarketing` | `Boolean!` | |

### Addresses

| Field | Type | Notes |
|---|---|---|
| `shippingAddress` | `MailingAddress` | Null for digital-only orders |
| `billingAddress` | `MailingAddress` | |
| `billingAddressMatchesShippingAddress` | `Boolean!` | |
| `displayAddress` | `MailingAddress` | Falls back to billing if no shipping |

### Financial status

| Field | Type | Notes |
|---|---|---|
| `displayFinancialStatus` | `OrderDisplayFinancialStatus` | Enum below |
| `fullyPaid` | `Boolean!` | |
| `unpaid` | `Boolean!` | |
| `canMarkAsPaid` | `Boolean!` | True for COD / manual payment |
| `refundable` | `Boolean!` | |
| `capturable` | `Boolean!` | |

**`OrderDisplayFinancialStatus` enum:**
- `PENDING`
- `AUTHORIZED`
- `PAID`
- `PARTIALLY_PAID`
- `PARTIALLY_REFUNDED`
- `REFUNDED`
- `VOIDED`
- `EXPIRED`

### Fulfillment status

| Field | Type | Notes |
|---|---|---|
| `displayFulfillmentStatus` | `OrderDisplayFulfillmentStatus!` | Enum below |
| `requiresShipping` | `Boolean!` | |
| `fulfillable` | `Boolean!` | |
| `confirmed` | `Boolean!` | Inventory reserved |
| `restockable` | `Boolean!` | At least one item still restockable |

**`OrderDisplayFulfillmentStatus` enum:**
- `UNFULFILLED` (sometimes shown as `UNSHIPPED`)
- `PARTIAL`
- `FULFILLED`
- `SHIPPED`
- `SCHEDULED`
- `ON_HOLD`
- `REQUEST_DECLINED`

### Cancellation

| Field | Type | Notes |
|---|---|---|
| `cancelReason` | `OrderCancelReason` | Null if not cancelled |
| `cancellation` | `OrderCancellation` | Has reason, staff note, source |
| `closed` | `Boolean!` | All items fulfilled or cancelled |

**`OrderCancelReason` enum:**
- `CUSTOMER` (a.k.a. `CUSTOMER_REQUEST` in older versions)
- `DECLINED`
- `FRAUD`
- `INVENTORY`
- `STAFF`
- `OTHER`

### Money (always `MoneyBag` — shop + presentment currency)

**Current (after returns / refunds / edits):**

| Field | Type | Notes |
|---|---|---|
| `currentSubtotalPriceSet` | `MoneyBag!` | |
| `currentTotalPriceSet` | `MoneyBag!` | |
| `currentTotalDiscountsSet` | `MoneyBag!` | |
| `currentTotalTaxSet` | `MoneyBag!` | |
| `currentTotalDutiesSet` | `MoneyBag` | Null if not applicable |
| `currentShippingPriceSet` | `MoneyBag!` | |
| `currentTotalAdditionalFeesSet` | `MoneyBag` | |
| `currentTotalWeight` | `UnsignedInt64!` | Grams |
| `currentCartDiscountAmountSet` | `MoneyBag!` | Order-level discount |

**Original (at creation):**

| Field | Type | Notes |
|---|---|---|
| `originalTotalPriceSet` | `MoneyBag!` | |
| `totalPriceSet` | `MoneyBag!` | Total before returns |
| `subtotalPriceSet` | `MoneyBag` | Null if modified |
| `totalDiscountsSet` | `MoneyBag` | |
| `totalTaxSet` | `MoneyBag` | |
| `totalShippingPriceSet` | `MoneyBag!` | |
| `originalTotalDutiesSet` | `MoneyBag` | |
| `originalTotalAdditionalFeesSet` | `MoneyBag` | |
| `totalWeight` | `UnsignedInt64` | |
| `cartDiscountAmountSet` | `MoneyBag` | Original |

**Refunds / payment totals:**

| Field | Type | Notes |
|---|---|---|
| `totalRefundedSet` | `MoneyBag!` | |
| `totalRefundedShippingSet` | `MoneyBag!` | |
| `refundDiscrepancySet` | `MoneyBag!` | Suggested - actual |
| `netPaymentSet` | `MoneyBag!` | Received − refunded |
| `totalReceivedSet` | `MoneyBag!` | |
| `totalOutstandingSet` | `MoneyBag!` | |
| `totalCapturableSet` | `MoneyBag!` | |

### Currency

| Field | Type | Notes |
|---|---|---|
| `currencyCode` | `CurrencyCode!` | Shop currency |
| `presentmentCurrencyCode` | `CurrencyCode!` | What the customer saw |

### Line items, fulfillments, refunds, returns, transactions

| Field | Type | Notes |
|---|---|---|
| `lineItems` | `LineItemConnection!` | Paginated; `first`, `after`, `last`, `before`, `reverse` |
| `nonFulfillableLineItems` | `LineItemConnection!` | Subset that can't be fulfilled |
| `subtotalLineItemsQuantity` | `Int!` | Sum of original quantities |
| `currentSubtotalLineItemsQuantity` | `Int!` | After returns/refunds |
| `fulfillments` | `[Fulfillment!]!` | Supports `first: Int`, `query: String` |
| `fulfillmentsCount` | `Count` | |
| `fulfillmentOrders` | `FulfillmentOrderConnection!` | `displayable`, `first`, `after`, `query: String` |
| `refunds` | `[Refund!]!` | Supports `first: Int` |
| `returns` | `ReturnConnection!` | `first`, `after`, `query: String` |
| `returnStatus` | `OrderReturnStatus!` | |
| `disputes` | `[OrderDisputeSummary!]!` | Chargebacks |
| `transactions` | `[OrderTransaction!]!` | `first`, `capturable`, `manuallyResolvable` |
| `transactionsCount` | `Count` | |

**`OrderReturnStatus` enum:**
- `RETURN_REQUESTED`
- `IN_PROGRESS`
- `INSPECTION_COMPLETE`
- `RETURNED`
- `RETURN_FAILED`
- `NO_RETURN`

### Discounts

| Field | Type | Notes |
|---|---|---|
| `discountCode` | `String` | Single code (legacy) |
| `discountCodes` | `[String!]!` | All codes |
| `discountApplications` | `DiscountApplicationConnection!` | |

### Shipping

| Field | Type | Notes |
|---|---|---|
| `shippingLine` | `ShippingLine` | Summary |
| `shippingLines` | `ShippingLineConnection!` | `includeRemovals: Boolean (default false)`, pagination |
| `retailLocation` | `Location` | For POS orders |

### Channel / source

| Field | Type | Notes |
|---|---|---|
| `sourceName` | `String` | `web`, `pos`, `shopify_draft_order`, `iphone`, etc. |
| `sourceIdentifier` | `String` | POS / third-party order ID |
| `app` | `OrderApp` | App that created it |
| `publication` | `Publication` | Sales channel |
| `channelInformation` | `ChannelInformation` | App type + name |
| `merchantOfRecordApp` | `OrderApp` | |

### Tax

| Field | Type | Notes |
|---|---|---|
| `taxesIncluded` | `Boolean!` | Whether subtotal includes tax |
| `taxExempt` | `Boolean!` | |
| `estimatedTaxes` | `Boolean!` | |
| `taxLines` | `[TaxLine!]!` | Pre-return |
| `currentTaxLines` | `[TaxLine!]!` | Post-return |
| `dutiesIncluded` | `Boolean!` | |

### Risk & disputes

| Field | Type | Notes |
|---|---|---|
| `risk` | `OrderRiskSummary!` | Current API |
| `shopifyProtect` | `ShopifyProtectOrderSummary` | |
| ~~`riskLevel`~~ | `OrderRiskLevel!` | **Deprecated** — use `risk` |
| ~~`risks`~~ | `[OrderRisk!]!` | **Deprecated** — use `risk` |

### Metadata

| Field | Type | Notes |
|---|---|---|
| `note` | `String` | |
| `tags` | `[String!]!` | |
| `customAttributes` | `[Attribute!]!` | |
| `metafield` | `Metafield` | |
| `metafields` | `MetafieldConnection!` | |
| `poNumber` | `String` | B2B |

### Miscellaneous

| Field | Type | Notes |
|---|---|---|
| `test` | `Boolean!` | Test order |
| `edited` | `Boolean!` | Has been edited |
| `merchantEditable` | `Boolean!` | |
| `merchantEditableErrors` | `[String!]!` | Why it can't be edited |
| `canNotifyCustomer` | `Boolean!` | |
| `clientIp` | `String` | |
| `statusPageUrl` | `URL!` | Customer order status page |
| `staffMember` | `StaffMember` | |
| `alerts` | `[ResourceAlert!]!` | |
| `hasTimelineComment` | `Boolean!` | |
| `paymentGatewayNames` | `[String!]!` | |
| `paymentTerms` | `PaymentTerms` | |
| `customerJourneySummary` | `CustomerJourneySummary` | |
| `purchasingEntity` | `PurchasingEntity` | B2B |
| `merchantBusinessEntity` | `BusinessEntity!` | |
| `suggestedRefund` | `SuggestedRefund` | Helper for refundCreate |
| `additionalFees` | `[AdditionalFee!]!` | Duties, imports |
| `agreements` | `SalesAgreementConnection!` | |
| `events` | `EventConnection!` | |
| `localizedFields` | `LocalizedFieldConnection!` | |
| `productNetwork` | `Boolean!` | Cross-store network |

### Deprecated (do not use)

Replaced by `*Set` variants returning `MoneyBag`:

- `cartDiscountAmount` → `cartDiscountAmountSet`
- `subtotalPrice` → `subtotalPriceSet`
- `totalPrice` → `totalPriceSet`
- `totalDiscounts` → `totalDiscountsSet`
- `totalTax` → `totalTaxSet`
- `totalShippingPrice` → `totalShippingPriceSet`
- `totalRefunded` → `totalRefundedSet`
- `totalReceived` → `totalReceivedSet`
- `totalCapturable` → `totalCapturableSet`
- `totalTipReceived` → `totalTipReceivedSet`
- `netPayment` → `netPaymentSet`

Other deprecations: `channel` → `channelInformation`, `customerJourney` → `customerJourneySummary`, `physicalLocation` → `retailLocation`, `localizationExtensions` → `localizedFields`, `riskLevel`/`risks` → `risk`, `metafieldDefinitions` → (no replacement), `referralCode`/`landingPageUrl`/`landingPageDisplayText`/`referrerDisplayText`/`referrerUrl` → (no replacement).

---

## LineItem object — full schema

### Identity

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `name` | `String!` | "Product title — Variant title" |
| `title` | `String!` | Product title at order time |
| `variantTitle` | `String` | Variant title at order time |
| `sku` | `String` | At order time (snapshot) |
| `vendor` | `String` | At order time |

### Variant + product link

| Field | Type | Notes |
|---|---|---|
| `variant` | `ProductVariant` | Null if variant deleted |
| `product` | `Product` | Null if product deleted |
| `image` | `Image` | |
| `lineItemGroup` | `LineItemGroup` | For bundles |

### Quantities (CRITICAL for SyncApp)

| Field | Type | Notes |
|---|---|---|
| `quantity` | `Int!` | Original ordered including refunded + removed |
| `currentQuantity` | `Int!` | After returns / refunds / edits |
| `refundableQuantity` | `Int!` | What's still refundable (= `currentQuantity` minus tips/etc.) |
| `unfulfillableQuantity` | `Int!` | Cannot be fulfilled |

**SyncApp note:** `quantity - currentQuantity` = units removed by refunds/returns/edits. For sales velocity use `currentQuantity` (net sold).

Deprecated quantity fields: `fulfillableQuantity`, `refundedQuantity` (use `quantity - currentQuantity` math), `fulfillmentStatus`, `fulfillmentService`. These still exist but new code should use the FulfillmentOrder graph.

### Flags

| Field | Type | Notes |
|---|---|---|
| `requiresShipping` | `Boolean!` | |
| `taxable` | `Boolean!` | |
| `restockable` | `Boolean!` | Whether refund can restock |
| `merchantEditable` | `Boolean!` | |
| `isGiftCard` | `Boolean!` | |

### Pricing (all `MoneyBag`)

| Field | Type | Notes |
|---|---|---|
| `originalUnitPriceSet` | `MoneyBag!` | Unit price at creation, pre-discount |
| `originalTotalSet` | `MoneyBag!` | Original × quantity |
| `discountedUnitPriceSet` | `MoneyBag!` | Unit price after line-level discounts |
| `discountedUnitPriceAfterAllDiscountsSet` | `MoneyBag!` | After ALL discounts incl. order-level |
| `discountedTotalSet` | `MoneyBag!` | Line total after line discounts |
| `totalDiscountSet` | `MoneyBag!` | Total discount allocated to this line |
| `unfulfilledDiscountedTotalSet` | `MoneyBag!` | For unfulfilled portion only |
| `unfulfilledOriginalTotalSet` | `MoneyBag!` | Original price × unfulfilled qty |

Deprecated singular variants: `originalUnitPrice`, `originalTotal`, `discountedUnitPrice`, `discountedTotal`, `totalDiscount`, `unfulfilledDiscountedTotal`, `unfulfilledOriginalTotal` — use the `*Set` versions.

### Tax + duties + discounts

| Field | Type | Notes |
|---|---|---|
| `taxLines` | `[TaxLine!]!` | |
| `duties` | `[Duty!]!` | |
| `discountAllocations` | `[DiscountAllocation!]!` | From `discountApplications` |
| `customAttributes` | `[Attribute!]!` | |

### Subscription / staff

| Field | Type | Notes |
|---|---|---|
| `contract` | `SubscriptionContract` | |
| `sellingPlan` | `LineItemSellingPlan` | |
| `staffMember` | `StaffMember` | |
| `suggestedReturnReasonDefinitions` | `ReturnReasonDefinitionConnection` | |

### Deprecated LineItem fields

- `canRestock` → `restockable`
- `fulfillableQuantity` → use FulfillmentOrder graph
- `fulfillmentService` → use FulfillmentOrder.assignedLocation / FulfillmentOrder.deliveryMethod
- `fulfillmentStatus` → use FulfillmentOrder.status
- All singular money fields → `*Set` versions

---

## Order search query syntax

The `query` argument on `orders`, `ordersCount`, and other connections uses Shopify's search syntax. Spaces = AND, commas = OR (within a single field), supports range operators `<`, `<=`, `>`, `>=`, and `..` (range).

### Supported parameters

**Financial:**

| Param | Values | Example |
|---|---|---|
| `financial_status` | `paid`, `pending`, `authorized`, `partially_paid`, `partially_refunded`, `refunded`, `voided`, `expired` | `financial_status:paid` |
| `chargeback_status` | `accepted`, `charge_refunded`, `lost`, `needs_response`, `under_review`, `won` | |
| `gateway` | gateway name | `gateway:shopify_payments` |
| `payment_id` | string | |
| `payment_provider_id` | string | |
| `credit_card_last4` | digits | |

**Fulfillment:**

| Param | Values | Example |
|---|---|---|
| `fulfillment_status` | `unshipped`, `shipped`, `fulfilled`, `partial`, `scheduled`, `on_hold`, `unfulfilled`, `request_declined` | `fulfillment_status:fulfilled` |
| `delivery_method` | `shipping`, `pick-up`, `retail`, `local`, `pickup-point`, `none` | |

**Status (the order itself):**

| Param | Values | Example |
|---|---|---|
| `status` | `open`, `closed`, `cancelled`, `not_closed` | `status:open` |

**Time-based (all support `<`, `<=`, `>`, `>=`):**

| Param | Example |
|---|---|
| `created_at` | `created_at:>=2026-04-01` |
| `updated_at` | `updated_at:>2026-05-01T00:00:00Z` |
| `processed_at` | `processed_at:>=2026-04-01 processed_at:<=2026-05-24` |

**Identity:**

| Param | Example |
|---|---|
| `customer_id` | `customer_id:123456` |
| `email` | `email:foo@bar.com` |
| `name` | `name:#1001` |
| `confirmation_number` | |
| `cart_token`, `checkout_token` | |

**Location / channel:**

| Param | Example |
|---|---|
| `location_id` | `location_id:12345` |
| `fulfillment_location_id` | |
| `reference_location_id` | Match across order, refunds, fulfillments |
| `sales_channel` | |
| `channel`, `channel_id` | `channel:web` |
| `source_name` | `web`, `shopify_draft_order`, `pos`, `iphone` |
| `source_identifier` | |

**Items / weight:**

| Param | Example |
|---|---|
| `sku` | `sku:WIDGET-RED-M` |
| `subtotal_line_items_quantity` | `subtotal_line_items_quantity:5..20` |
| `total_weight` | `total_weight:>=1kg` |

**Tags / discount / PO / metafields:**

| Param | Example |
|---|---|
| `tag` | `tag:wholesale` |
| `tag_not` | `tag_not:test` |
| `discount_code` | |
| `po_number` | |
| `metafields.{namespace}.{key}` | `metafields.custom.priority:high` |

**Risk + protection:**

| Param | Values |
|---|---|
| `fraud_protection_level` | `fully_protected`, `partially_protected`, `not_protected`, `pending`, `not_eligible`, `not_available` |
| `risk_level` | `high`, `medium`, `low`, `none`, `pending` |

**Returns:**

| Param | Values |
|---|---|
| `return_status` | `return_requested`, `in_progress`, `inspection_complete`, `returned`, `return_failed`, `no_return` |

**Test:**

| Param | Values |
|---|---|
| `test` | `true`, `false` |

### Combining filters

```
# AND (space-separated)
financial_status:paid created_at:>=2026-04-01

# OR (comma-separated within a field)
financial_status:paid,partially_refunded

# Range with two operators
created_at:>=2026-04-01 created_at:<2026-05-01

# Numeric range
subtotal_line_items_quantity:5..20

# Combined complex
status:open fulfillment_status:unfulfilled location_id:123 -tag:test
```

### Examples for SyncApp imports

```graphql
# Last 30 days of paid orders
query: "financial_status:paid processed_at:>=2026-04-24"

# Date-range bulk import
query: "processed_at:>=2026-04-01 AND processed_at:<=2026-05-24"

# Only orders that affected inventory (paid + not cancelled)
query: "financial_status:paid status:open,closed -test:true"

# Recent updates for incremental sync
query: "updated_at:>=2026-05-23T00:00:00Z"
```

---

## Queries: orders, order, ordersCount

### `orders` (connection)

```graphql
query Orders(
  $first: Int!
  $after: String
  $query: String
  $sortKey: OrderSortKeys
  $reverse: Boolean
  $savedSearchId: ID
) {
  orders(
    first: $first
    after: $after
    query: $query
    sortKey: $sortKey
    reverse: $reverse
    savedSearchId: $savedSearchId
  ) {
    edges {
      cursor
      node {
        id
        name
        processedAt
        displayFinancialStatus
        displayFulfillmentStatus
        currentTotalPriceSet { shopMoney { amount currencyCode } }
        lineItems(first: 50) {
          edges {
            node {
              id
              sku
              quantity
              currentQuantity
              variant { id legacyResourceId inventoryItem { id } }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    pageInfo {
      hasNextPage
      hasPreviousPage
      startCursor
      endCursor
    }
  }
}
```

**Arguments:**

| Arg | Type | Default | Notes |
|---|---|---|---|
| `first` | `Int` | | Page size (max 250) |
| `last` | `Int` | | Reverse pagination |
| `after` | `String` | | Cursor |
| `before` | `String` | | |
| `query` | `String` | | Search syntax (above) |
| `sortKey` | `OrderSortKeys` | `PROCESSED_AT` | See below |
| `reverse` | `Boolean` | `false` | |
| `savedSearchId` | `ID` | | Use admin saved search |

**`OrderSortKeys` enum:**
- `PROCESSED_AT` (default)
- `ID`
- `CREATED_AT`
- `UPDATED_AT`
- `ORDER_NUMBER`
- `CUSTOMER_NAME`
- `FINANCIAL_STATUS`
- `FULFILLMENT_STATUS`
- `TOTAL_PRICE`
- `UPDATED_AT`
- `RELEVANCE` (only with `query`)

### `order` (single)

```graphql
query GetOrder($id: ID!) {
  order(id: $id) {
    id
    name
    processedAt
    currentSubtotalPriceSet { shopMoney { amount currencyCode } }
    lineItems(first: 100) { edges { node { sku currentQuantity } } }
  }
}
```

Variables: `{ "id": "gid://shopify/Order/123" }`.

### `ordersCount`

```graphql
query CountOrders($query: String, $limit: Int) {
  ordersCount(query: $query, limit: $limit) {
    count
    precision     # EXACT or ESTIMATED
  }
}
```

Default `limit: 10000` (hard cap). Use `null` for "no limit" (returns `precision: ESTIMATED` for large counts). Useful for "how many orders match this filter before I paginate?".

### `orderByIdentifier`

Use when you only have a customer-facing identifier (e.g. confirmation number) and not the GID.

```graphql
query ByIdentifier($id: OrderIdentifierInput!) {
  orderByIdentifier(identifier: $id) { id name }
}
```

---

## Fulfillment + FulfillmentOrder model

The modern fulfillment graph is rooted in `FulfillmentOrder`. The legacy direct `Fulfillment.create` REST endpoint is **retired** — every new fulfillment goes through `fulfillmentCreate` with `lineItemsByFulfillmentOrder`.

### Mental model

```
Order
 ├── FulfillmentOrder #1  (location A, status: OPEN)
 │    ├── FulfillmentOrderLineItem (SKU, totalQty, remainingQty)
 │    └── FulfillmentOrderLineItem
 ├── FulfillmentOrder #2  (location B, status: IN_PROGRESS)
 │    └── FulfillmentOrderLineItem
 └── Fulfillments[]   <-- created by fulfillmentCreate from one or more FOs
       └── trackingInfo, status, location, line items
```

One `Order` → N `FulfillmentOrder` (one per assigned location). One `FulfillmentOrder` → 0..N `Fulfillment` (each shipment).

### FulfillmentOrder schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `status` | `FulfillmentOrderStatus!` | |
| `requestStatus` | `FulfillmentOrderRequestStatus!` | |
| `supportedActions` | `[FulfillmentOrderSupportedAction!]!` | What you can call right now |
| `lineItems` | `FulfillmentOrderLineItemConnection!` | |
| `assignedLocation` | `FulfillmentOrderAssignedLocation!` | |
| `destination` | `FulfillmentOrderDestination` | Where it ships |
| `fulfillBy` | `DateTime` | Deadline |
| `fulfillAt` | `DateTime` | When it becomes fulfillable |
| `internationalDuties` | `FulfillmentOrderInternationalDuties` | |
| `deliveryMethod` | `DeliveryMethod` | |
| `channelId` | `ID` | |
| `createdAt` | `DateTime!` | |
| `updatedAt` | `DateTime!` | |
| `order` | `Order!` | |
| `orderId` | `ID!` | |
| `orderName` | `String!` | e.g. `#1001` |
| `orderProcessedAt` | `DateTime!` | |
| `merchantRequests` | `FulfillmentOrderMerchantRequestConnection!` | |
| `fulfillmentHolds` | `[FulfillmentHold!]!` | |
| `fulfillments` | `FulfillmentConnection!` | Created shipments |
| `locationsForMove` | `FulfillmentOrderLocationForMoveConnection!` | Where you can reroute it |

**`FulfillmentOrderStatus` enum:**
- `OPEN` — ready for fulfillment
- `SCHEDULED` — waiting on `fulfillAt`
- `IN_PROGRESS` — partially fulfilled
- `CLOSED` — fully fulfilled or cancelled
- `ON_HOLD` — paused (e.g. fraud review)
- `INCOMPLETE` — internal state
- `CANCELLED`

**`FulfillmentOrderRequestStatus` enum** (for 3PL workflows):
- `UNSUBMITTED`
- `SUBMITTED`
- `ACCEPTED`
- `REJECTED`
- `CANCELLATION_REQUESTED`
- `CANCELLATION_ACCEPTED`
- `CANCELLATION_REJECTED`
- `CLOSED`

### FulfillmentOrderLineItem schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `lineItem` | `LineItem!` | Original order LineItem |
| `totalQuantity` | `Int!` | Original ordered |
| `remainingQuantity` | `Int!` | Yet to fulfill |
| `inventoryItemId` | `ID` | InventoryItem GID |
| `productTitle` | `String!` | |
| `sku` | `String` | |
| `variant` | `ProductVariant` | |
| `variantTitle` | `String` | |
| `weight` | `Weight` | |
| `vendor` | `String` | |
| `requiresShipping` | `Boolean!` | |
| `image` | `Image` | |
| `financialSummaries` | `[FulfillmentOrderLineItemFinancialSummary!]!` | Pricing |
| `warnings` | `[FulfillmentOrderLineItemWarning!]!` | |

### Fulfillment object

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `legacyResourceId` | `UnsignedInt64!` | |
| `status` | `FulfillmentStatus!` | |
| `displayStatus` | `FulfillmentDisplayStatus` | |
| `name` | `String!` | e.g. `#1001.1` |
| `createdAt` | `DateTime!` | |
| `updatedAt` | `DateTime!` | |
| `deliveredAt` | `DateTime` | |
| `estimatedDeliveryAt` | `DateTime` | |
| `inTransitAt` | `DateTime` | |
| `requiresShipping` | `Boolean!` | |
| `totalQuantity` | `Int!` | |
| `location` | `Location` | |
| `originAddress` | `FulfillmentOriginAddress` | |
| `service` | `FulfillmentService` | |
| `order` | `Order!` | |
| `trackingInfo` | `[FulfillmentTrackingInfo!]!` | Supports `first: Int` |
| `fulfillmentLineItems` | `FulfillmentLineItemConnection!` | |
| `events` | `FulfillmentEventConnection!` | Status milestones |

**`FulfillmentStatus` enum:**
- `PENDING`
- `OPEN`
- `SUCCESS`
- `CANCELLED`
- `ERROR`
- `FAILURE`

**`FulfillmentDisplayStatus` enum** (richer for UI):
- `ATTEMPTED_DELIVERY`
- `CANCELED`
- `CONFIRMED`
- `DELIVERED`
- `FAILURE`
- `FULFILLED`
- `IN_TRANSIT`
- `LABEL_PRINTED`
- `LABEL_PURCHASED`
- `LABEL_VOIDED`
- `MARKED_AS_FULFILLED`
- `NOT_DELIVERED`
- `OUT_FOR_DELIVERY`
- `PICKED_UP`
- `READY_FOR_PICKUP`
- `SUBMITTED`

### How to fulfill an order (modern flow)

1. Query the order's `fulfillmentOrders`.
2. Pick the FO(s) you want to fulfill (status must be `OPEN` or `IN_PROGRESS`, possibly `ON_HOLD` after release).
3. Call `fulfillmentCreate` with `lineItemsByFulfillmentOrder` (omit individual line items to fulfill everything remaining).

```graphql
mutation FulfillOrder($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment {
      id
      status
      displayStatus
      trackingInfo { company number url }
      fulfillmentLineItems(first: 50) {
        edges { node { id quantity lineItem { sku } } }
      }
    }
    userErrors { field message }
  }
}
```

Variables:

```json
{
  "fulfillment": {
    "lineItemsByFulfillmentOrder": [
      {
        "fulfillmentOrderId": "gid://shopify/FulfillmentOrder/123",
        "fulfillmentOrderLineItems": [
          { "id": "gid://shopify/FulfillmentOrderLineItem/456", "quantity": 1 }
        ]
      }
    ],
    "trackingInfo": {
      "number": "1Z999AA10123456784",
      "company": "UPS",
      "url": "https://wwwapps.ups.com/tracking?tracknum=1Z999AA10123456784"
    },
    "notifyCustomer": true
  }
}
```

**Key behaviors:**

- If `fulfillmentOrderLineItems` is omitted for an FO, ALL remaining items are fulfilled.
- Combining multiple FOs in one call requires they belong to the **same order** and **same location**.
- Carrier name in `trackingInfo.company` auto-generates the tracking URL if Shopify recognizes the carrier (UPS, FedEx, USPS, DHL, etc.).

### Related fulfillment mutations

| Mutation | Purpose |
|---|---|
| `fulfillmentCreate` | Create fulfillment from FO line items |
| `fulfillmentTrackingInfoUpdate` | Update tracking after creation |
| `fulfillmentCancel` | Cancel a fulfillment — reverses effect on FO, items become re-fulfillable |
| `fulfillmentOrderAcceptCancellationRequest` | (3PL) Accept merchant cancellation request |
| `fulfillmentOrderRejectCancellationRequest` | (3PL) Reject cancellation |
| `fulfillmentOrderSubmitCancellationRequest` | Merchant requests 3PL cancel |
| `fulfillmentOrderClose` | Close FO manually |
| `fulfillmentOrderMove` | Reassign FO to another location |
| `fulfillmentOrderHold` | Apply a hold |
| `fulfillmentOrderReleaseHold` | Remove a hold |
| `fulfillmentOrderReschedule` | Update `fulfillAt` |
| `fulfillmentOrderAcceptFulfillmentRequest` | (3PL) Accept fulfillment request |
| `fulfillmentOrderRejectFulfillmentRequest` | (3PL) Reject |
| `fulfillmentOrderSubmitFulfillmentRequest` | Merchant requests 3PL fulfill |

### fulfillmentTrackingInfoUpdate

```graphql
mutation UpdateTracking(
  $fulfillmentId: ID!
  $trackingInfoInput: FulfillmentTrackingInput!
  $notifyCustomer: Boolean
) {
  fulfillmentTrackingInfoUpdate(
    fulfillmentId: $fulfillmentId
    trackingInfoInput: $trackingInfoInput
    notifyCustomer: $notifyCustomer
  ) {
    fulfillment { id trackingInfo { number url company } }
    userErrors { field message }
  }
}
```

`FulfillmentTrackingInput`:
- `company: String`
- `number: String` (single)
- `numbers: [String!]` (multi-package)
- `url: String`
- `urls: [String!]`

### fulfillmentCancel

```graphql
mutation CancelFulfillment($id: ID!) {
  fulfillmentCancel(id: $id) {
    fulfillment { id status }
    userErrors { field message }
  }
}
```

**Side effects:**
- Fully-fulfilled FOs reopen.
- Partially-fulfilled FOs increase `remainingQuantity` on affected line items.
- Shopify creates new FOs at the original location (or by priority rules) so items become re-fulfillable.
- **Does NOT restock inventory** by itself — inventory was decremented at order creation (depending on `inventoryBehaviour`), not at fulfillment.

### FulfillmentService

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `serviceName` | `String!` | Merchant-facing name |
| `handle` | `String!` | Unique slug |
| `callbackUrl` | `URL` | Where Shopify sends requests |
| `inventoryManagement` | `Boolean!` | Does the FS push inventory updates? |
| `location` | `Location` | The auto-created location for this FS |
| `requiresShippingMethod` | `Boolean!` | |
| `trackingSupport` | `Boolean!` | Implements `/fetch_tracking_numbers` |
| `type` | `FulfillmentServiceType!` | `MANUAL`, `THIRD_PARTY`, etc. |
| ~~`fulfillmentOrdersOptIn`~~ | `Boolean!` | **Deprecated** — everyone is opted in |

---

## Refund object + refundCreate workflow

### Refund schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `legacyResourceId` | `UnsignedInt64!` | |
| `createdAt` | `DateTime` | |
| `updatedAt` | `DateTime!` | |
| `processedAt` | `DateTime!` | When money moved |
| `note` | `String` | |
| `order` | `Order!` | |
| `return` | `Return` | If refund came from a return |
| `staffMember` | `StaffMember` | Who issued it |
| `totalRefundedSet` | `MoneyBag!` | |
| `refundLineItems` | `RefundLineItemConnection!` | |
| `refundShippingLines` | `RefundShippingLineConnection!` | |
| `duties` | `[RefundDuty!]` | |
| `orderAdjustments` | `OrderAdjustmentConnection!` | Tax/shipping adjustments tied to refund |
| `transactions` | `OrderTransactionConnection!` | Actual payment movements |

Existence of a `Refund` does NOT guarantee money has moved — check `transactions[].status`:
- `PENDING`
- `PROCESSING`
- `SUCCESS`
- `FAILURE`
- `ERROR`
- `UNRESOLVED`
- `AWAITING_RESPONSE`

### RefundLineItem schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID` | |
| `lineItem` | `LineItem!` | Original line item |
| `quantity` | `Int!` | Units refunded |
| `restockType` | `RefundLineItemRestockType!` | See below |
| `restocked` | `Boolean!` | Whether inventory was actually restocked |
| `location` | `Location` | Where it was restocked |
| `priceSet` | `MoneyBag!` | Price refunded |
| `subtotalSet` | `MoneyBag!` | |
| `totalTaxSet` | `MoneyBag!` | |

### RefundLineItemRestockType enum

| Value | Behavior |
|---|---|
| `NO_RESTOCK` | Money refunded, no inventory change. |
| `RETURN` | Item marked as returned; **inventory restocked at `locationId`**; fires `INVENTORY_LEVELS_UPDATE`. |
| `CANCEL` | Item cancelled retroactively; **inventory restocked at `locationId`**; fires `INVENTORY_LEVELS_UPDATE`. |
| `LEGACY_RESTOCK` | Legacy restock behavior (pre-FulfillmentOrder world). |

**SyncApp implication:** Only `RETURN` and `CANCEL` raise the inventory webhook. `NO_RESTOCK` refunds do NOT trigger any inventory webhook — they only affect the order's money and `currentQuantity`.

### refundCreate mutation

```graphql
mutation CreateRefund($input: RefundInput!) {
  refundCreate(input: $input) {
    refund {
      id
      totalRefundedSet { shopMoney { amount currencyCode } }
      refundLineItems(first: 50) {
        edges {
          node {
            quantity
            restockType
            restocked
            location { id }
            lineItem { id sku }
          }
        }
      }
    }
    order { id displayFinancialStatus }
    userErrors { field message }
  }
}
```

Variables example:

```json
{
  "input": {
    "orderId": "gid://shopify/Order/123",
    "note": "Customer returned damaged item.",
    "notify": true,
    "currency": "USD",
    "shipping": { "amount": "9.99" },
    "refundLineItems": [
      {
        "lineItemId": "gid://shopify/LineItem/456",
        "quantity": 1,
        "restockType": "RETURN",
        "locationId": "gid://shopify/Location/789"
      }
    ],
    "transactions": [
      {
        "orderId": "gid://shopify/Order/123",
        "amount": "29.99",
        "kind": "REFUND",
        "gateway": "shopify_payments",
        "parentId": "gid://shopify/OrderTransaction/111"
      }
    ]
  }
}
```

**`RefundInput` fields:**

| Field | Type | Notes |
|---|---|---|
| `orderId` | `ID!` | |
| `note` | `String` | |
| `notify` | `Boolean` | Customer notification |
| `currency` | `CurrencyCode` | |
| `shipping` | `ShippingRefundInput` | `{ amount, fullRefund }` |
| `refundLineItems` | `[RefundLineItemInput!]` | See below |
| `refundDuties` | `[RefundDutyInput!]` | |
| `transactions` | `[OrderTransactionInput!]` | Payment movements |
| `refundMethods` | `[RefundMethodInput!]` | Store credit, gift card, etc. |

**`RefundLineItemInput`:**

| Field | Required | Notes |
|---|---|---|
| `lineItemId` | yes | |
| `quantity` | yes | |
| `restockType` | yes | `NO_RESTOCK` / `RETURN` / `CANCEL` / `LEGACY_RESTOCK` |
| `locationId` | required when restocking | Where the inventory returns |

**Tip:** Query `order.suggestedRefund` first to get a pre-computed refund plan including which line items are refundable and at what amount.

---

## Return object + return lifecycle

A `Return` represents the buyer's intent to ship items back. Lifecycle:

```
returnRequest        →  status: REQUESTED  (customer or staff initiates)
returnApproveRequest →  status: OPEN       (merchant approves)
returnDeclineRequest →  status: DECLINED   (merchant declines)
returnClose          →  status: CLOSED     (merchant marks complete)
returnCancel         →  status: CANCELED   (merchant cancels)
```

`returnCreate` skips the request step — it goes straight to `OPEN`.

### Return schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `name` | `String!` | e.g. `#1001-R1` |
| `status` | `ReturnStatus!` | |
| `totalQuantity` | `Int!` | |
| `createdAt` | `DateTime!` | |
| `closedAt` | `DateTime` | |
| `requestApprovedAt` | `DateTime` | When approved |
| `order` | `Order!` | |
| `returnLineItems` | `ReturnLineItemTypeConnection!` | |
| `exchangeLineItems` | `ExchangeLineItemConnection!` | |
| `returnShippingFees` | `[ReturnShippingFee!]!` | |
| `refunds` | `RefundConnection!` | |
| `reverseFulfillmentOrders` | `ReverseFulfillmentOrderConnection!` | Logistics tracking |
| `transactions` | `OrderTransactionConnection!` | |
| `decline` | `ReturnDecline` | If declined |
| `staffMember` | `StaffMember` | |
| `suggestedFinancialOutcome` | `SuggestedReturnFinancialOutcome` | |
| ~~`suggestedRefund`~~ | `SuggestedReturnRefund` | **Deprecated** — use `suggestedFinancialOutcome` |

**`ReturnStatus` enum:**
- `REQUESTED`
- `OPEN`
- `CLOSED`
- `DECLINED`
- `CANCELED`

### ReturnLineItem schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `customerNote` | `String` | Max 300 chars |
| `quantity` | `Int!` | |
| `processableQuantity` | `Int!` | |
| `processedQuantity` | `Int!` | |
| `unprocessedQuantity` | `Int!` | |
| `refundableQuantity` | `Int!` | |
| `refundedQuantity` | `Int!` | |
| `returnReason` | `ReturnReason!` | **Deprecated** — use `returnReasonDefinition` |
| `returnReasonNote` | `String!` | Max 255 chars |
| `returnReasonDefinition` | `ReturnReasonDefinition` | Standardized |
| `fulfillmentLineItem` | `FulfillmentLineItem!` | Links to the original fulfilled item |
| `totalWeight` | `Weight` | |
| `restockingFee` | `RestockingFee` | |
| `withCodeDiscountedTotalPrice` | `MoneyBag!` | Line total after discounts |

**`ReturnReason` enum (deprecated but still in use):**
- `SIZE_TOO_SMALL`
- `SIZE_TOO_LARGE`
- `UNWANTED`
- `NOT_AS_DESCRIBED`
- `WRONG_ITEM`
- `DEFECTIVE`
- `STYLE`
- `COLOR`
- `OTHER`
- `UNKNOWN`

### returnRequest mutation

```graphql
mutation RequestReturn($input: ReturnRequestInput!) {
  returnRequest(input: $input) {
    return {
      id
      name
      status      # REQUESTED
      returnLineItems(first: 10) {
        edges { node { quantity returnReasonNote } }
      }
    }
    userErrors { field message }
  }
}
```

Variables:

```json
{
  "input": {
    "orderId": "gid://shopify/Order/123",
    "returnLineItems": [
      {
        "fulfillmentLineItemId": "gid://shopify/FulfillmentLineItem/456",
        "quantity": 1,
        "returnReason": "DEFECTIVE",
        "customerNote": "Stitching came apart after one wash."
      }
    ]
  }
}
```

### returnApproveRequest mutation

```graphql
mutation ApproveReturn($input: ReturnApproveRequestInput!) {
  returnApproveRequest(input: $input) {
    return { id status }   # OPEN after approval
    userErrors { field message }
  }
}
```

Variables: `{ "input": { "id": "gid://shopify/Return/789" } }`.

**Only returns with status `REQUESTED` can be approved.**

### returnClose mutation

```graphql
mutation CloseReturn($id: ID!) {
  returnClose(id: $id) {
    return { id status closedAt }  # CLOSED
    userErrors { field message }
  }
}
```

Use when: (1) refund issued + items restocked, or (2) items marked as received without a refund.

### Related return mutations

| Mutation | Purpose |
|---|---|
| `returnCreate` | Create return directly in `OPEN` (skip request) |
| `returnRequest` | Customer/staff-initiated request → `REQUESTED` |
| `returnApproveRequest` | Approve → `OPEN` |
| `returnDeclineRequest` | Decline → `DECLINED` |
| `returnClose` | Close → `CLOSED` |
| `returnCancel` | Cancel → `CANCELED` |
| `returnReopen` | Reopen a closed return |
| `returnLineItemRemoveFromReturn` | Drop a line from a return |
| `refundCreate` (with `return` link) | Issue the refund + optionally restock |
| `reverseDeliveryCreateWithShipping` | Create reverse delivery (return shipment) |

---

## Draft Order workflow

Draft orders are merchant-built carts (phone sales, custom invoicing, B2B quotes). They live separately until `draftOrderComplete` converts them into a real `Order`.

### DraftOrder schema (key fields)

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `legacyResourceId` | `UnsignedInt64!` | |
| `name` | `String!` | e.g. `#D1223` |
| `status` | `DraftOrderStatus!` | |
| `order` | `Order` | Set once completed |
| `customer` | `Customer` | |
| `email`, `phone` | `String` | |
| `shippingAddress`, `billingAddress` | `MailingAddress` | |
| `billingAddressMatchesShippingAddress` | `Boolean!` | |
| `lineItems` | `DraftOrderLineItemConnection!` | Paginated |
| `shippingLine` | `ShippingLine` | |
| `totalQuantityOfLineItems` | `Int!` | |
| `totalPriceSet` | `MoneyBag!` | Includes tax + shipping + discounts |
| `subtotalPriceSet` | `MoneyBag!` | |
| `lineItemsSubtotalPrice` | `MoneyBag!` | After discounts |
| `totalShippingPriceSet` | `MoneyBag!` | |
| `totalTaxSet` | `MoneyBag!` | |
| `totalDiscountsSet` | `MoneyBag!` | |
| `totalLineItemsPriceSet` | `MoneyBag!` | |
| `taxExempt` | `Boolean!` | |
| `taxesIncluded` | `Boolean!` | |
| `taxLines` | `[TaxLine!]!` | |
| `currencyCode` | `CurrencyCode!` | |
| `presentmentCurrencyCode` | `CurrencyCode!` | |
| `appliedDiscount` | `DraftOrderAppliedDiscount` | Order-level custom discount |
| `discountCodes` | `[String!]!` | |
| `allowDiscountCodesInCheckout` | `Boolean!` | |
| `acceptAutomaticDiscounts` | `Boolean` | |
| `platformDiscounts` | `[DraftOrderPlatformDiscount!]!` | |
| `note2` | `String` | |
| `tags` | `[String!]!` | |
| `customAttributes` | `[Attribute!]!` | |
| `poNumber` | `String` | |
| `createdAt`, `updatedAt`, `completedAt`, `invoiceSentAt` | `DateTime` | |
| `invoiceUrl` | `URL` | Customer checkout link |
| `reserveInventoryUntil` | `DateTime` | Inventory auto-releases at this time |
| `paymentTerms` | `PaymentTerms` | |
| `ready` | `Boolean!` | Completable |
| `visibleToCustomer` | `Boolean!` | |
| `metafield`, `metafields` | | |
| `localizedFields` | | |
| `defaultCursor` | `String!` | Pagination helper |
| `purchasingEntity` | `PurchasingEntity` | B2B |
| `invoiceEmailTemplateSubject` | `String!` | |
| `hasTimelineComment` | `Boolean!` | |
| `allVariantPricesOverridden`, `anyVariantPricesOverridden` | `Boolean!` | |
| `totalWeight` | `UnsignedInt64!` | Grams |
| `transformerFingerprint` | `String` | Bundle cart fingerprint |
| `warnings` | `[DraftOrderWarning!]!` | |
| `events` | `EventConnection!` | |

**`DraftOrderStatus` enum:**
- `OPEN`
- `INVOICE_SENT`
- `COMPLETED`

**Note:** Draft orders created on/after 2025-04-01 are auto-purged after 1 year of inactivity.

### DraftOrderLineItem schema

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `variant` | `ProductVariant` | |
| `product` | `Product` | |
| `quantity` | `Int!` | For bundles, this is bundle quantity |
| `sku` | `String` | |
| `title` | `String!` | Custom-item title |
| `name` | `String!` | |
| `vendor` | `String` | |
| `taxable` | `Boolean!` | |
| `requiresShipping` | `Boolean!` | |
| `originalUnitPriceSet` | `MoneyBag!` | Pre-discount |
| `approximateDiscountedUnitPriceSet` | `MoneyBag!` | Per-unit after line discounts |
| `appliedDiscount` | `DraftOrderAppliedDiscount` | |
| `customAttributes` | `[Attribute!]!` | |
| `image` | `Image` | |
| `taxLines` | `[TaxLine!]!` | |
| `isGiftCard` | `Boolean!` | |
| `weight` | `Weight` | |
| `custom` | `Boolean!` | Custom item vs. variant |
| `uuid` | `String!` | Required for bundle manipulation |

### Draft order mutations

**`draftOrderCreate`**

```graphql
mutation CreateDraft($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name status invoiceUrl }
    userErrors { field message }
  }
}
```

`DraftOrderInput` (key fields):

| Field | Type | Notes |
|---|---|---|
| `lineItems` | `[DraftOrderLineItemInput!]` | Required ≥1 |
| `appliedDiscount` | `DraftOrderAppliedDiscountInput` | Order-level |
| `customAttributes` | `[AttributeInput!]` | |
| `customerId` | `ID` | |
| `email`, `phone` | `String` | |
| `note` | `String` | |
| `tags` | `[String!]` | |
| `taxExempt` | `Boolean` | |
| `visibleToCustomer` | `Boolean` | |
| `shippingLine` | `ShippingLineInput` | `{ title, price }` |
| `shippingAddress`, `billingAddress` | `MailingAddressInput` | |
| `useCustomerDefaultAddress` | `Boolean` | |
| `presentmentCurrencyCode` | `CurrencyCode` | |
| `metafields` | `[MetafieldInput!]` | |
| `sourceName` | `String` | Channel attribution |
| `reserveInventoryUntil` | `DateTime` | Inventory hold |
| `poNumber` | `String` | |
| `paymentTerms` | `PaymentTermsInput` | B2B |
| `purchasingEntity` | `PurchasingEntityInput` | B2B |

`DraftOrderLineItemInput` (per item):

- `variantId: ID` (or `title` + custom)
- `quantity: Int!`
- `customAttributes: [AttributeInput!]`
- `originalUnitPrice: Decimal`
- `title: String`
- `weight: WeightInput`
- `appliedDiscount: DraftOrderAppliedDiscountInput`
- `components: [DraftOrderLineItemComponentInput!]` (bundles)

**`draftOrderUpdate`**

```graphql
mutation UpdateDraft($id: ID!, $input: DraftOrderInput!) {
  draftOrderUpdate(id: $id, input: $input) {
    draftOrder { id status updatedAt }
    userErrors { field message }
  }
}
```

**Important:** Updating a draft after a customer has started checkout **unlinks the checkout**. Customer must re-enter checkout.

**`draftOrderComplete`**

```graphql
mutation CompleteDraft(
  $id: ID!
  $paymentGatewayId: ID
  $sourceName: String
) {
  draftOrderComplete(
    id: $id
    paymentGatewayId: $paymentGatewayId
    sourceName: $sourceName
  ) {
    draftOrder {
      id
      status     # COMPLETED
      order { id name processedAt }
    }
    userErrors { field message }
  }
}
```

**Side effects on completion:**

1. A real `Order` is created (returned in `draftOrder.order`).
2. Inventory is **committed** for line item quantities (subtracted from `available`, added to `committed`).
3. `ORDERS_CREATE` webhook fires.
4. `INVENTORY_LEVELS_UPDATE` webhooks fire for each affected variant/location.
5. Customer notification email sent if `visibleToCustomer: true`.

The deprecated `paymentPending: Boolean` arg used to defer payment — superseded by `paymentTerms`.

**`draftOrderDelete`**

```graphql
mutation DeleteDraft($input: DraftOrderDeleteInput!) {
  draftOrderDelete(input: $input) {
    deletedId
    userErrors { field message }
  }
}
```

Variables: `{ "input": { "id": "gid://shopify/DraftOrder/123" } }`. Completed drafts cannot be deleted (use the real Order's lifecycle).

### Draft order queries

**`draftOrder(id: ID!)`** — single.

**`draftOrders`** — connection. Args: `first`, `last`, `after`, `before`, `query`, `sortKey: DraftOrderSortKeys`, `reverse`, `savedSearchId`.

Supported `query` filters: `status` (`OPEN`/`INVOICE_SENT`/`COMPLETED`), `created_at`, `updated_at`, `customer_id`, `email`, `tag`, `id`.

`DraftOrderSortKeys`:
- `ID` (default)
- `NUMBER`
- `UPDATED_AT`
- `CUSTOMER_NAME`
- `STATUS`
- `TOTAL_PRICE`
- `RELEVANCE`

---

## Order mutations

### orderCreate

Creates an order outside the standard checkout. Useful for migrations from other platforms or wholesale imports.

```graphql
mutation CreateOrder(
  $order: OrderCreateOrderInput!
  $options: OrderCreateOptionsInput
) {
  orderCreate(order: $order, options: $options) {
    order { id name processedAt }
    userErrors { field message }
  }
}
```

**Trial/dev store limit: 5 orders/minute.**

`OrderCreateOrderInput` highlights:
- `lineItems: [OrderCreateLineItemInput!]!`
- `customer: OrderCreateCustomerInput` (`toAssociate` or `toUpsert`)
- `currency: CurrencyCode`
- `email: String`
- `financialStatus: OrderCreateFinancialStatus` (`PAID`, `PENDING`, `REFUNDED`, `VOIDED`, `PARTIALLY_REFUNDED`, `AUTHORIZED`, `PARTIALLY_PAID`, `EXPIRED`)
- `fulfillmentStatus: OrderCreateFulfillmentStatus` (`FULFILLED`, `PARTIAL`, `UNSHIPPED`, `UNCONFIRMED`)
- `shippingAddress`, `billingAddress`
- `taxLines`
- `transactions`
- `discountCode` (single: `itemFixedDiscountCode` or `itemPercentageDiscountCode`)
- `presentmentCurrency`

`OrderCreateOptionsInput`:
- `sendReceipt: Boolean`
- `sendFulfillmentReceipt: Boolean`
- `inventoryBehaviour: OrderCreateInputsInventoryBehavior` — `BYPASS` (don't touch inventory), `DECREMENT_IGNORING_POLICY`, `DECREMENT_OBEYING_POLICY`

**SyncApp warning:** `inventoryBehaviour: BYPASS` is the only safe choice when importing historical orders that already had their inventory effects in another system — otherwise you'll double-decrement and trigger phantom `INVENTORY_LEVELS_UPDATE` webhooks.

### orderUpdate

Modifies metadata only (no line item structural changes).

```graphql
mutation UpdateOrder($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id note tags shippingAddress { address1 city } }
    userErrors { field message }
  }
}
```

`OrderInput` editable fields:
- `id: ID!` (required)
- `email`, `phone`
- `note`
- `tags: [String!]` (overwrites existing — to append, fetch+merge first)
- `customAttributes: [AttributeInput!]`
- `shippingAddress: MailingAddressInput`
- `metafields: [MetafieldInput!]`
- `poNumber`

For line item / quantity changes, use the `orderEdit*` flow.

### orderCancel

Asynchronously cancels an order. **Returns a `Job` — poll `job.done` for completion.**

```graphql
mutation CancelOrder(
  $orderId: ID!
  $reason: OrderCancelReason!
  $restock: Boolean!
  $refundMethod: OrderCancelRefundMethodInput
  $notifyCustomer: Boolean
  $staffNote: String
) {
  orderCancel(
    orderId: $orderId
    reason: $reason
    restock: $restock
    refundMethod: $refundMethod
    notifyCustomer: $notifyCustomer
    staffNote: $staffNote
  ) {
    job { id done }
    orderCancelUserErrors { field message code }
  }
}
```

`OrderCancelReason` (mutation-side, slightly different from `Order.cancelReason`):
- `CUSTOMER`
- `DECLINED`
- `FRAUD`
- `INVENTORY`
- `STAFF`
- `OTHER`

**`restock` behavior:**
- `true` + active locations: inventory returns to original locations → fires `INVENTORY_LEVELS_UPDATE` per variant/location.
- `true` + **paid** order at deactivated location: cancel **fails** (cannot restock to inactive location).
- `true` + **unpaid** order at deactivated location: succeeds, but inventory is NOT restocked anywhere (it becomes unavailable).

Authorized payments are voided regardless of `refundMethod`.

### orderClose / orderOpen

```graphql
mutation CloseOrder($input: OrderCloseInput!) {
  orderClose(input: $input) {
    order { id closed closedAt }
    userErrors { field message }
  }
}
```

Marks an order with all items fulfilled/cancelled and all payments processed as "done". `orderOpen` reverses.

### orderMarkAsPaid

```graphql
mutation MarkPaid($input: OrderMarkAsPaidInput!) {
  orderMarkAsPaid(input: $input) {
    order { id displayFinancialStatus }
    userErrors { field message }
  }
}
```

Records a manual/external payment. Only works on orders with a positive outstanding balance and non-`PAID` status. Either creates a new `SALE` transaction or captures an existing `AUTHORIZATION`.

### Order edit flow (orderEdit*)

Multi-step transactional edit:

```
orderEditBegin(id)          →  returns orderEditSession + calculatedOrder
orderEditAddVariant(id, variantId, quantity, locationId)
orderEditAddLineItemDiscount(...)
orderEditSetQuantity(id, lineItemId, quantity, restock)
orderEditAddCustomItem(...)
orderEditAddShippingLine(...)
orderEditUpdateShippingLine(...)
orderEditAddDiscount(...)
orderEditApplyDiscount(...) / orderEditRemoveDiscount(...)
orderEditCommit(id, notifyCustomer, staffNote)   →  applies all staged edits atomically
```

```graphql
mutation BeginEdit($id: ID!) {
  orderEditBegin(id: $id) {
    calculatedOrder { id }
    orderEditSession { id }
    userErrors { field message }
  }
}

mutation CommitEdit(
  $id: ID!
  $notifyCustomer: Boolean
  $staffNote: String
) {
  orderEditCommit(id: $id, notifyCustomer: $notifyCustomer, staffNote: $staffNote) {
    order { id edited }
    userErrors { field message }
    successMessages
  }
}
```

**Constraints:** only **unfulfilled** line items are editable. Price adjustments may trigger customer refunds or additional payment requests.

### Other order mutations worth knowing

| Mutation | Purpose |
|---|---|
| `orderCreateManualPayment` | Record an out-of-band payment |
| `orderCustomerRemove` | Detach customer from order |
| `orderCustomerSet` | Attach/replace customer |
| `orderInvoiceSend` | Email an invoice |
| `orderRiskAssessmentCreate` | Add a risk assessment (3p fraud apps) |

---

## Webhooks emitted per lifecycle event

Cross-reference with `docs/SHOPIFY-API-REFERENCE.md` in the SyncApp repo for the topic catalog. Below is the per-action map.

| Event | Webhooks fired |
|---|---|
| **Customer checks out** (standard order) | `orders/create`, `inventory_levels/update` (×N) |
| **`draftOrderComplete`** | `orders/create`, `draft_orders/update` (status → COMPLETED), `inventory_levels/update` |
| **`orderCreate` with `inventoryBehaviour: BYPASS`** | `orders/create` (no inventory webhook) |
| **`orderCreate` with default inventory behaviour** | `orders/create`, `inventory_levels/update` |
| **`orderUpdate` (note, tags, etc.)** | `orders/updated` |
| **`orderCancel` with `restock: true`** | `orders/cancelled`, `orders/updated`, `refunds/create` (if refund issued), `inventory_levels/update` (×N) |
| **`orderCancel` with `restock: false`** | `orders/cancelled`, `orders/updated`, `refunds/create` (if refund) |
| **`orderClose`** | `orders/updated` (closed_at set) |
| **`orderEditCommit`** | `orders/updated`, possibly `inventory_levels/update` (if line items added/removed touching available inventory), `order_transactions/create` (if payment delta) |
| **`fulfillmentCreate`** | `fulfillments/create`, `orders/updated` (fulfillment_status), `fulfillment_orders/fulfillment_request_accepted` (if 3PL) |
| **`fulfillmentTrackingInfoUpdate`** | `fulfillments/update` |
| **`fulfillmentCancel`** | `fulfillments/update`, `orders/updated`, `fulfillment_orders/cancelled` |
| **`refundCreate` with `restockType: RETURN`/`CANCEL`** | `refunds/create`, `orders/updated`, `inventory_levels/update` (×N at `locationId`) |
| **`refundCreate` with `restockType: NO_RESTOCK`** | `refunds/create`, `orders/updated` (no inventory webhook) |
| **`returnRequest`** | `returns/request` |
| **`returnApproveRequest`** | `returns/approve` |
| **`returnDeclineRequest`** | `returns/decline` |
| **`returnClose`** | `returns/close` |
| **`returnCancel`** | `returns/cancel` |
| **`returnCreate`** (skip request) | `returns/create` |
| **`draftOrderCreate`** | `draft_orders/create` |
| **`draftOrderUpdate`** | `draft_orders/update` |
| **`draftOrderDelete`** | `draft_orders/delete` |

**SyncApp's subscribed topics for order import:**
- `orders/create` — primary sales-velocity signal
- `orders/updated` — tags, status changes, line item edits
- `orders/cancelled` — release allocated stock
- `orders/paid` — financial transition (if subscribed; otherwise look for `orders/updated` with `financial_status` delta)
- `orders/fulfilled` (and `orders/partially_fulfilled`) — fulfillment progress
- `refunds/create` — refunded units affect velocity windows

The `inventory_levels/update` webhooks that fire downstream are handled by SyncApp's main sync engine, NOT the order importer — but the order importer should be aware that an order action it triggered (cancel, refund-with-restock) will produce inventory webhooks the sync engine then processes. Use `markAsSelfPush()` if SyncApp itself is the one issuing the order mutation that causes inventory to move.

---

## Inventory side effects summary

Quick lookup for "which order mutation moves inventory?":

| Mutation | Inventory side effect |
|---|---|
| `orderCreate` (default `inventoryBehaviour`) | Decrements `available`, increments `committed` |
| `orderCreate` with `inventoryBehaviour: BYPASS` | **No inventory change** (use for imports) |
| `orderCreate` with `DECREMENT_IGNORING_POLICY` | Decrements even if oversold |
| `orderCreate` with `DECREMENT_OBEYING_POLICY` | Decrements only if available; can fail |
| `orderUpdate` | None (metadata only) |
| `orderCancel(restock: true)` at active loc | Returns inventory → `available` |
| `orderCancel(restock: true)` at inactive loc (paid) | **Fails** |
| `orderCancel(restock: false)` | None (inventory remains "lost") |
| `orderClose` / `orderOpen` | None |
| `orderEditCommit` (added line items) | Decrements `available` for the added quantity |
| `orderEditCommit` (removed line items, `restock: true`) | Returns inventory |
| `fulfillmentCreate` | Decrements `committed`, increments `fulfilled` (no `available` change) |
| `fulfillmentCancel` | Reverses; `committed` goes back up |
| `refundCreate(restockType: NO_RESTOCK)` | None |
| `refundCreate(restockType: RETURN)` | Returns inventory to `available` at `locationId` |
| `refundCreate(restockType: CANCEL)` | Returns inventory to `available` at `locationId` |
| `refundCreate(restockType: LEGACY_RESTOCK)` | Legacy behavior; treat like RETURN |
| `returnRequest` / `returnApproveRequest` | None (no money moves, no inventory) |
| `returnClose` | None |
| `returnCancel` | None |
| `reverseDeliveryCreateWithShipping` | None directly; inventory moves on the linked refundCreate |
| `draftOrderCreate` | None (unless `reserveInventoryUntil` set — then holds reservation) |
| `draftOrderComplete` | Decrements `available`, increments `committed` (like a normal order) |
| `draftOrderDelete` | Releases any reservation |

---

## Pagination patterns

All connections (`orders`, `lineItems`, `fulfillmentOrders`, `refunds`, `returns`, `draftOrders`) use cursor-based pagination. **Maximum page size: 250.**

### Forward pagination

```graphql
query OrdersPage($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: PROCESSED_AT) {
    edges {
      cursor
      node {
        id
        # ...
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
```

Loop pattern:

```ts
let after: string | null = null;
let hasNext = true;
while (hasNext) {
  const res = await admin.graphql(QUERY, { variables: { first: 250, after, query: "processed_at:>=2026-04-01" } });
  const { edges, pageInfo } = res.data.orders;
  for (const e of edges) await processOrder(e.node);
  hasNext = pageInfo.hasNextPage;
  after = pageInfo.endCursor;
}
```

### Nested pagination caveat

When fetching `orders { lineItems(first: 50) }`, if any order has > 50 line items you must re-query that order's `lineItems` with cursor pagination separately. The pageInfo lives on the inner connection:

```graphql
order(id: $orderId) {
  lineItems(first: 100, after: $lineCursor) {
    edges { node { id sku currentQuantity } }
    pageInfo { hasNextPage endCursor }
  }
}
```

Most orders have < 50 lines; the safe default for SyncApp imports is `lineItems(first: 100)` then audit any orders where `pageInfo.hasNextPage == true`.

### Bulk operations (better for large imports)

For multi-million-order historical imports, use Shopify's bulk operations API:

```graphql
mutation StartBulk {
  bulkOperationRunQuery(
    query: """
    {
      orders(query: "processed_at:>=2026-04-01 AND processed_at:<=2026-05-24") {
        edges {
          node {
            id
            name
            processedAt
            displayFinancialStatus
            lineItems {
              edges {
                node { id sku currentQuantity quantity variant { id } }
              }
            }
          }
        }
      }
    }
    """
  ) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
```

Poll `currentBulkOperation { status url }`. When `status: COMPLETED`, download the JSONL from `url`. Bulk ops bypass GraphQL rate limits and stream results.

---

## SyncApp angles

### What SyncApp imports from each webhook

**`orders/create`** — primary new-order signal. Fields we need:

```graphql
{
  id, legacyResourceId, name, processedAt, createdAt,
  displayFinancialStatus, displayFulfillmentStatus,
  currencyCode, presentmentCurrencyCode,
  totalPriceSet { shopMoney { amount currencyCode } presentmentMoney { amount currencyCode } },
  currentTotalPriceSet { shopMoney { amount } },
  totalDiscountsSet { shopMoney { amount } },
  customer { id email },
  retailLocation { id },
  test,
  lineItems(first: 100) {
    edges {
      node {
        id
        sku
        quantity              # original
        currentQuantity       # net after edits/refunds
        variant {
          id
          legacyResourceId
          inventoryItem { id }
        }
        originalUnitPriceSet { shopMoney { amount } }
        discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

The `variant.inventoryItem.id` is what we tie to our `BarcodeGroup` (via the `Variant.barcode` we synced earlier). `currentQuantity` is what we add to the sales-velocity counter.

**`orders/updated`** — re-fetch line items (currentQuantity may have changed from an edit), check `displayFinancialStatus` and `displayFulfillmentStatus` transitions.

**`refunds/create`** — fields we need:

```graphql
{
  id,
  createdAt, processedAt,
  order { id legacyResourceId },
  totalRefundedSet { shopMoney { amount } },
  refundLineItems(first: 50) {
    edges {
      node {
        quantity,
        restockType,
        restocked,
        location { id legacyResourceId },
        lineItem { id sku variant { id inventoryItem { id } } }
      }
    }
  }
}
```

When `restockType` is `RETURN` or `CANCEL` and `restocked: true`, the inventory webhook will fire separately (handled by the inventory sync engine). For sales-velocity purposes, subtract `quantity` from the velocity counter for the affected SKU/variant.

**`orders/cancelled`** — fields we need:

```graphql
{
  id, cancelledAt, cancelReason,
  lineItems(first: 100) {
    edges { node { id sku quantity currentQuantity variant { id inventoryItem { id } } } }
  }
}
```

If `cancelReason` is set and `restock` was true at cancel-time, expect `INVENTORY_LEVELS_UPDATE` webhooks at the order's original fulfillment location for each line item.

### How an order cancellation restocks inventory

1. Merchant (or app) calls `orderCancel(orderId, reason, restock: true, ...)`.
2. Shopify enqueues an async job.
3. Job runs:
   - Voids any authorized payments.
   - Issues refund per `refundMethod` if specified.
   - For each fulfilled line item: nothing (it's already shipped).
   - For each un-fulfilled line item: returns the committed quantity to `available` at the original location.
   - Marks order `cancelled_at`, `cancel_reason`.
4. Fires `orders/cancelled` + `orders/updated`.
5. Fires `refunds/create` if a refund was issued.
6. Fires one `inventory_levels/update` per (variant, location) pair that moved.
7. SyncApp's webhook worker:
   - `inventory_levels/update` is checked against `isSelfPushAsync()` — if SyncApp didn't push the cancel itself, it's a real change → mark group dirty → flush sync.
   - `orders/cancelled` updates our order shadow + decrements velocity counters.

### How refunds with `restock: true` trigger inventory webhooks

`refundCreate` with `refundLineItems[].restockType: RETURN | CANCEL` and a valid `locationId` causes Shopify to:

1. Create the `Refund` record.
2. Increment inventory `available` by `quantity` at `locationId`.
3. Fire `refunds/create` webhook.
4. Fire `inventory_levels/update` webhook per (variant, location).

SyncApp's flow:

1. Webhook worker receives `refunds/create` → parse + persist refund shadow + reduce velocity by `quantity`.
2. Webhook worker receives `inventory_levels/update` (a few seconds later usually) → checks `isSelfPushAsync` (won't be self-push unless SyncApp triggered the refund) → marks barcode group dirty.
3. Sync engine flush picks up dirty group, runs allocation, pushes updated stock to all other stores in the group.

### Recommended bulk-import pattern

For backfilling sales velocity from historical orders:

```graphql
query OrdersBatch($first: Int!, $after: String) {
  orders(
    first: $first
    after: $after
    sortKey: PROCESSED_AT
    query: "processed_at:>=2026-04-01 AND processed_at:<=2026-05-24 -test:true"
  ) {
    edges {
      cursor
      node {
        id
        legacyResourceId
        processedAt
        displayFinancialStatus
        currentTotalPriceSet { shopMoney { amount currencyCode } }
        lineItems(first: 100) {
          edges {
            node {
              id
              sku
              currentQuantity
              variant { id legacyResourceId inventoryItem { id } }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

**Pagination strategy:** `first: 250`, persist `endCursor` to DB after each batch, resume on failure. Filter out `test:true` (Bogus Gateway etc.). Filter out orders with `displayFinancialStatus: VOIDED` for sales velocity (they never collected money). Use `processed_at` (not `created_at`) for the date window so back-dated orders fall in the right bucket.

**Rate budgeting:** an `orders(first: 250)` query with nested `lineItems(first: 100)` costs roughly 50-100 credits depending on result count. With the 1000-credit bucket + 50/s refill, you can sustain ~250 orders/sec sustained. Use `rateLimitedShopifyFetch` to read the `extensions.cost.throttleStatus` and throttle proactively when `currentlyAvailable < 200`.

**For multi-million-order shops:** prefer `bulkOperationRunQuery` — see Bulk operations section above. The JSONL output is streamable and unaffected by GraphQL throttle.

### SyncApp-specific gotchas

1. **`test` orders** — Shopify's Bogus Gateway and dev-store flagged orders set `test: true`. Always exclude these from velocity counters; never push their inventory effects.
2. **Multi-currency** — always read `currentTotalPriceSet.shopMoney.amount` for internal accounting; `presentmentMoney.amount` is only for what the customer paid.
3. **`currentQuantity` vs `quantity`** — for sales velocity, use `currentQuantity`. Tracking `quantity` instead means refunds and edits won't decrement velocity, and our restock recommendations will overshoot.
4. **Workspace isolation** — every order fetched via webhook must be tagged with the receiving shop's `workspaceId` before persistence. The order's GID is shop-local; a `gid://shopify/Order/123` from shop A is a different order than `gid://shopify/Order/123` from shop B.
5. **Self-push suppression on order-level mutations** — if SyncApp ever calls `orderCancel(restock: true)` or `refundCreate(restockType: RETURN)`, the downstream `inventory_levels/update` webhook(s) need to be suppressed. Call `markAsSelfPush(inventoryItemGid, locationGid, expectedNewQty)` for each (variant, location) BEFORE the mutation.
6. **Webhook dedup keys** — `orders/*` topics have a root `id` so `payload.id` works for the BullMQ jobId. `refunds/create` has `payload.id` (the refund ID) — use that, not the order ID, or batched refunds collapse to one job. See `webhooks.tsx` lines 52-59 + Blocker 3 in `PRODUCTION-AUDIT.md`.

---
