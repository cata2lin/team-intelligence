# Shopify Admin API — Adjacent Types Reference

Reference for SyncApp covering the scalar, type, and enum infrastructure that surrounds the inventory-sync core: Customer/Order context, Money, Country/Currency, Discounts, Selling Plans, Collections, Saved Searches, Staff, and Access Scopes.

**API version baseline:** `2026-04` (April26).

For inventory and orders, see the dedicated skill files. This file is the lookup table when you encounter an unfamiliar scalar, an enum value, or a field that points into a less-trodden corner of the API.

---

## Table of contents

1. [Scalar types](#1-scalar-types)
2. [MoneyV2 / MoneyBag](#2-moneyv2--moneybag)
3. [Common enums](#3-common-enums)
4. [GID format](#4-gid-format)
5. [Search query syntax](#5-search-query-syntax)
6. [Customer object](#6-customer-object)
7. [Collection object](#7-collection-object)
8. [Discount automatic / Discount code](#8-discount-automatic--discount-code)
9. [Selling Plans](#9-selling-plans)
10. [Saved Searches](#10-saved-searches)
11. [Staff members / Permissions](#11-staff-members--permissions)
12. [Access scopes](#12-access-scopes)

---

## 1. Scalar types

Shopify's GraphQL schema relies on a small set of custom scalars beyond the GraphQL spec defaults. Getting any of these wrong leads to silent precision loss, malformed IDs, or comparison bugs that bite hard later.

### `ID`

A globally-unique identifier. Always opaque — never parse it, never construct it manually unless you're certain of the format. Shopify uses the **GID format**: `gid://shopify/<Type>/<numeric_id>`.

**Wire format:** JSON string.

**Example:** `"gid://shopify/Product/10079785100"`

**Things to know:**
- Two equal-looking IDs from different shops may collide in the numeric part but differ in shop scope (the ID does not encode the shop).
- The `legacyResourceId` field on most objects returns the bare numeric part as `UnsignedInt64` for REST API compatibility — useful but does not include the type prefix.
- An ID in a mutation input must include the full `gid://shopify/...` prefix.

See [GID format](#4-gid-format) for the full list of resource types SyncApp uses.

### `DateTime`

ISO 8601-encoded date-time string, always **UTC** (suffix `Z`).

**Wire format:** JSON string.

**Example:** `"2026-05-24T15:50:00Z"`

**Things to know:**
- Shopify never returns a local-time string — always UTC. Convert at the UI layer if you need to display in the merchant's timezone.
- When sending DateTimes in queries or mutations, also send UTC (`Z` suffix) to avoid Shopify rejecting your filter or interpreting it as the shop's local time.
- Sub-second precision: typically millisecond resolution. Do not depend on microseconds.
- For search query syntax, dates are wrapped in single quotes: `created_at:>'2026-05-01T00:00:00Z'`.

### `Decimal`

Arbitrary-precision signed decimal number, **serialized as a string**.

**Wire format:** JSON string.

**Example:** `"29.99"`, `"29.999"`, `"-1.5"`, `"0"`

**Things to know:**
- **Never** parse this into `Number` for monetary work. JS `Number` is IEEE 754 double — `0.1 + 0.2 !== 0.3` issues. Use a Decimal library (`decimal.js`, `big.js`) or string-based arithmetic.
- For SyncApp, Decimals appear in `MoneyV2.amount`, weight values, tax rates, conversion rates. Treat them as opaque strings until you hit a controlled boundary that does the arithmetic.
- Comparison with strict equality (`===`) will not work across precisions: `"10.0"` and `"10.00"` are not `===` equal but represent the same value.

### `URL`

RFC 3986 / 3987-compliant URI. Includes scheme (typically `https`) and host.

**Wire format:** JSON string.

**Example:** `"https://example.myshopify.com"`, `"https://cdn.shopify.com/s/files/1/..."`

**Things to know:**
- Always validate at boundaries — Shopify mostly returns its own URLs but custom-set fields (theme settings, webhook callback URLs) may be user-provided.
- Storefront URLs end in `.myshopify.com`; custom domains do not — both are valid.

### `UnsignedInt64`

Whole numeric value between `0` and `2^64 - 1` (`18446744073709551615`), **serialized as a string** of base-10 digits.

**Wire format:** JSON string.

**Example:** `"50"`, `"10079785100"`, `"18446744073709551615"`

**Things to know:**
- JS `Number` safely represents only integers up to `2^53 - 1` (`9007199254740991`). Values above that **silently lose precision** when parsed with `Number()` or `parseInt()`.
- For SyncApp: `legacyResourceId` is `UnsignedInt64`. Today Shopify's resource IDs fit in 53 bits, but the type doesn't guarantee that. Either keep the string verbatim, or convert with `BigInt(value)`.
- Other UnsignedInt64 fields: `Customer.numberOfOrders`, various count fields, transaction IDs.
- Avoid `parseInt()` — at minimum, route through `BigInt()` or keep the string.

```ts
// BAD — silent precision loss
const id = Number(customer.legacyResourceId);

// OK — preserve precision
const id = BigInt(customer.legacyResourceId);

// BEST for SyncApp — keep as string, compare as string
const id = customer.legacyResourceId; // already a string
```

### `Int`

Standard GraphQL 32-bit signed integer. Range: `-2^31` to `2^31 - 1`.

**Wire format:** JSON number.

Used for: pagination `first` / `last` parameters, small counts, position indices.

### `Float`

Standard GraphQL double-precision float. **Avoid for money** — Shopify uses `Decimal` everywhere money matters. `Float` is used for things like rule weights, percentage-like values that aren't financial.

### `String`

Standard GraphQL UTF-8 string. No max length enforced at the schema level.

### `Boolean`

Standard GraphQL boolean.

### `JSON`

Arbitrary JSON value. Used for things like `Metafield.value` when the metafield type is `json`, app data blobs, custom payloads. Always validate the shape before using.

---

## 2. MoneyV2 / MoneyBag

Money in Shopify is **always a typed pair** — amount plus currency. There is no anonymous numeric money field anywhere in modern Shopify GraphQL.

### `MoneyV2`

```graphql
type MoneyV2 {
  amount: Decimal!
  currencyCode: CurrencyCode!
}
```

Used for single-currency monetary values — for instance, an item's price in the shop's base currency, a customer's lifetime spend, or a payout amount.

**Example payload:**

```json
{
  "amount": "29.99",
  "currencyCode": "USD"
}
```

**Always handle both fields together.** `amount: "29.99"` with no currency tells you nothing about value.

### `MoneyBag`

```graphql
type MoneyBag {
  shopMoney: MoneyV2!
  presentmentMoney: MoneyV2!
}
```

A **pair of MoneyV2 values** — one in the shop's base currency (what the merchant accounts for) and one in the customer's presentment currency (what the customer paid).

**Example payload:**

```json
{
  "shopMoney":       { "amount": "29.99", "currencyCode": "USD" },
  "presentmentMoney":{ "amount": "27.50", "currencyCode": "EUR" }
}
```

### When MoneyBag is used vs. MoneyV2

As of the modern Shopify API, **MoneyBag is the default for any field that touches an order, draft order, refund, return, or fulfillment** — anything customer-facing where multi-currency matters. This includes:

- Order subtotals, totals, taxes, discounts, shipping, duties, tips
- Refund line items, refund totals
- Return line item amounts
- Draft order totals
- Discount allocations
- Additional fees (e.g., `AdditionalFeeSale.totalAmount`)

`MoneyV2` continues to appear for:

- Product variant `price`, `compareAtPrice` (priced in shop currency, presentment derived via price lists / markets)
- Customer `amountSpent` (lifetime total, shop currency)
- `InventoryItem.unitCost`
- Payout amounts (single shop currency)
- Wholesale price list adjustments

**Why two values?** Even a USD shop that doesn't enable Shopify Markets can receive an order in EUR if a customer checks out via a third-party storefront with multi-currency. The two fields let merchants reconcile their books (shopMoney) while displaying what the customer was charged (presentmentMoney). When the shop has no multi-currency enabled, `shopMoney === presentmentMoney`.

**Practical rule for SyncApp:**
- When reading product prices for analytics or pricing decisions → `variant.price` is `MoneyV2`. Use the `amount` (in shop currency).
- When reading order line items for velocity calculations → use `MoneyBag.shopMoney.amount` for consistency. Mixing shop and presentment will corrupt the per-SKU velocity numbers across multi-currency orders.

### CurrencyExchangeAdjustment

When a refund crosses a currency boundary and the FX rate has moved since the order was placed, Shopify records a `currencyExchangeAdjustment` on the refund. The `originalAmountSet` and `finalAmountSet` may differ — the difference is the FX gain/loss. SyncApp does not need to track this directly but be aware when summing refund totals.

---

## 3. Common enums

### `CurrencyCode`

Three-letter currency code. **~180 values** — every active ISO 4217 currency plus a handful of legacy and non-standard codes (including digital currencies like `USDC`).

**Common values SyncApp will see:**

| Code | Name |
|---|---|
| `USD` | United States Dollar |
| `EUR` | Euro |
| `GBP` | United Kingdom Pound |
| `CAD` | Canadian Dollar |
| `AUD` | Australian Dollar |
| `JPY` | Japanese Yen |
| `CHF` | Swiss Franc |
| `NZD` | New Zealand Dollar |
| `SEK` | Swedish Krona |
| `DKK` | Danish Krone |
| `NOK` | Norwegian Krone |
| `MXN` | Mexican Peso |
| `BRL` | Brazilian Real |
| `INR` | Indian Rupee |
| `SGD` | Singapore Dollar |
| `HKD` | Hong Kong Dollar |
| `CNY` | Chinese Yuan |
| `KRW` | South Korean Won |
| `ZAR` | South African Rand |
| `PLN` | Polish Złoty |
| `RON` | Romanian Leu |
| `HUF` | Hungarian Forint |
| `CZK` | Czech Koruna |
| `TRY` | Turkish Lira |
| `AED` | UAE Dirham |
| `USDC` | USD Coin (digital) |

**Deprecated values** (still in the enum, returned for historical data, do not use for new code):
- `BYR` (Belarusian Ruble, replaced by `BYN`)
- `STD` (São Tomé Dobra, replaced by `STN`)
- `VEF` (Venezuelan Bolívar, replaced by `VES`)

**Special:** `XXX` — sometimes returned for "no currency / non-currency transaction". Treat as a sentinel, not a real currency.

### `CountryCode`

Two-letter ISO 3166-1 alpha-2 country code. **249 values total** — every recognized territory plus historical and special administrative codes.

**Common values SyncApp will see:**

| Code | Country |
|---|---|
| `US` | United States |
| `CA` | Canada |
| `GB` | United Kingdom |
| `DE` | Germany |
| `FR` | France |
| `IT` | Italy |
| `ES` | Spain |
| `NL` | Netherlands |
| `AU` | Australia |
| `NZ` | New Zealand |
| `JP` | Japan |
| `RO` | Romania |
| `IN` | India |

**Special values:**
- `XK` — Kosovo (no ISO 3166-1 code assigned; Shopify uses XK)
- `UM` — U.S. Outlying Islands
- `AN` — Netherlands Antilles (historical, deprecated)
- `ZZ` — **Unknown Region**. Sometimes returned when geolocation fails. Handle defensively.

### `WeightUnit`

Four units, no others:

| Value | Notes |
|---|---|
| `GRAMS` | Metric. Smallest practical unit Shopify uses. |
| `KILOGRAMS` | Metric. 1 kg = 1000 g. |
| `OUNCES` | Imperial. |
| `POUNDS` | Imperial. 1 lb = 16 oz. |

When reading variant weight from Shopify, **always read both `weight` and `weightUnit`** — never assume a unit. Some shops mix units across variants.

### `CurrencyExchangeAdjustmentReason`

Not retrievable from current docs (404 on direct fetch) — possible values seen in practice:

- `REFUND` — adjustment recorded when the refund FX rate differs from the order FX rate.
- `ORDER` — adjustment recorded when capture rate differs from authorization rate.

SyncApp does not need to interpret these — just be aware they exist on refund/transaction objects.

---

## 4. GID format

Shopify's Global ID (GID) format:

```
gid://shopify/<Type>/<numeric_id>
```

Always a string. Always opaque. Always the full form including `gid://shopify/` prefix when sending to a mutation.

### Resource types SyncApp uses

| GID prefix | Description | Where SyncApp uses it |
|---|---|---|
| `gid://shopify/Product/123` | Product | webhook handlers, product reconciliation |
| `gid://shopify/ProductVariant/123` | Product variant | variant indexing, barcode group membership |
| `gid://shopify/InventoryItem/123` | Inventory item (the SKU-bound stock holder) | inventory level lookups, mutations |
| `gid://shopify/InventoryLevel/123?inventory_item_id=...&location_id=...` | Inventory level (item × location). **Note: includes query string** | level reads, but mutations use itemId + locationId pair instead |
| `gid://shopify/Location/123` | Physical/virtual location | location index, per-location strategy config |
| `gid://shopify/Order/123` | Order | order webhook ingestion, velocity computation |
| `gid://shopify/LineItem/123` | Order line item | order line scanning for SKU velocity |
| `gid://shopify/Refund/123` | Refund | refund handling for velocity adjustments |
| `gid://shopify/Return/123` | Return (post-fulfillment returns flow) | return-driven inventory restocks |
| `gid://shopify/Customer/123` | Customer | minimal — only as references in orders |
| `gid://shopify/Shop/123` | Shop | shop-level webhooks, shop info |
| `gid://shopify/App/123` | App | app installation queries |
| `gid://shopify/AppInstallation/123` | App installation | reading current install metadata |
| `gid://shopify/AppSubscription/123` | App subscription (billing) | billing tier verification |
| `gid://shopify/MetafieldDefinition/123` | Metafield definition | metafield-driven sync rules (future) |
| `gid://shopify/Metafield/123` | Metafield instance | per-product custom config |
| `gid://shopify/Fulfillment/123` | Fulfillment | fulfillment webhook → inventory decrement |
| `gid://shopify/FulfillmentOrder/123` | Fulfillment order | new-style fulfillment routing |
| `gid://shopify/FulfillmentService/123` | Fulfillment service | for shops using 3PL integrations |
| `gid://shopify/StaffMember/123` | Staff member | rarely — staff who installed the app |
| `gid://shopify/Collection/123` | Collection (manual or smart) | future feature — collection-scoped sync |
| `gid://shopify/DiscountAutomatic/123` | Automatic discount | not used by SyncApp |
| `gid://shopify/DiscountCode/123` | Discount code | not used by SyncApp |
| `gid://shopify/SellingPlan/123` | Selling plan | order line context only |
| `gid://shopify/SellingPlanGroup/123` | Selling plan group | order line context only |

### Parsing GIDs

When you need to extract the numeric part (for logs, comparison with REST data, etc.):

```ts
// Simple extraction — works for the standard form
function parseGid(gid: string): { type: string; id: string } {
  const match = gid.match(/^gid:\/\/shopify\/([^/]+)\/(\d+)/);
  if (!match) throw new Error(`Invalid GID: ${gid}`);
  return { type: match[1], id: match[2] };
}

// InventoryLevel is a special case — it has a query string
const il = "gid://shopify/InventoryLevel/123?inventory_item_id=456&location_id=789";
// match still works for type and "id" but the real composite is in the query string
```

**Things to know:**
- Comparing GIDs across two queries: strict string equality is safe.
- Comparing across REST/GraphQL boundaries: REST gives you the bare numeric, GraphQL gives you the GID. Use `legacyResourceId` field or strip the prefix.
- Don't construct GIDs from REST IDs unless you know the type — `gid://shopify/Product/123` is not interchangeable with `gid://shopify/ProductVariant/123`.

---

## 5. Search query syntax

A universal pattern across **every `query: String` argument** in Shopify queries — `products`, `orders`, `customers`, `collections`, `staffMembers`, etc. Same syntax, different supported fields per resource.

### Operators

| Operator | Meaning | Example |
|---|---|---|
| `:` | Equality (default) | `first_name:Bob` |
| `:>` | Greater than | `orders_count:>16` |
| `:<` | Less than | `total_spent:<100` |
| `:>=` | Greater or equal | `orders_count:>=5` |
| `:<=` | Less or equal | `orders_count:<=30` |
| `AND` | Conjunction (implicit between terms) | `state:enabled AND tag:vip` |
| `OR` | Disjunction | `bob OR norman` |
| `NOT` or `-` | Negation | `-first_name:Bob` or `NOT tag:archived` |

### Structure rules

- **No whitespace between field and value:** `title:"Caramel Apple"` is correct; `title: Apple` is **invalid**.
- **Phrase queries:** wrap multi-word values in double quotes — `first_name:"Bob Norman"`.
- **Date values:** wrap in single quotes — `created_at:>'2026-05-01T00:00:00Z'`.
- **Implicit AND:** consecutive terms are AND-ed — `state:enabled tag:vip` ≡ `state:enabled AND tag:vip`.
- **Parentheses for grouping:** `(tag:vip OR tag:wholesale) AND state:enabled`.
- **Ranges:** combine two operators on the same field — `inventory_total:>500 inventory_total:<=1000`.

### Wildcards

- `field:value*` — prefix match. `title:head*` matches "headphones", "headband".
- Suffix and substring wildcards are **not supported**.
- `field:*` — match any value (existence check). `published_at:*` finds rows where `published_at` is set.
- `-field:*` or `NOT field:*` — find rows where the field is null/unset.

### Saved searches

```
saved_search:my_search_name
```

References a `SavedSearch` by name (or by ID via the dedicated `savedSearchId` query argument). See [Saved Searches](#10-saved-searches).

### Escaping

Special characters inside values that need escaping: `:`, `\`, `(`, `)`, `"`. Prefix with backslash: `title:Bob\:s\ Bait`.

### Per-resource supported fields

This is not exhaustive — Shopify's docs list per-resource fields under each `query` argument. The common ones SyncApp will use:

#### Products (`products` query)

- `title`, `vendor`, `product_type`, `handle`
- `status` (active, draft, archived)
- `tag`, `tag_not`
- `barcode`, `sku`
- `created_at`, `updated_at`, `published_at`
- `inventory_total`, `inventory_quantity`
- `collection_id`
- `gift_card`
- `published_status` (published, unpublished, online_store_channel, etc.)

#### Orders (`orders` query)

- `name`, `id`
- `email`, `customer_id`
- `financial_status`, `fulfillment_status`
- `created_at`, `updated_at`, `processed_at`, `closed_at`, `cancelled_at`
- `tag`, `tag_not`
- `risk_level`
- `sales_channel`
- `total`, `subtotal`
- `country`, `country_code`
- `sku`
- `gateway`
- `location_id`

#### Customers (`customers` query)

- `email`, `first_name`, `last_name`, `phone`, `id`, `default`
- `accepts_marketing` (deprecated, use consent fields)
- `country`, `state`
- `customer_date`, `order_date`, `last_abandoned_order_date`, `updated_at`
- `orders_count`, `total_spent`
- `tag`, `tag_not`

#### Collections (`collections` query)

- `title`
- `collection_type` (custom, smart)
- `handle`
- `id`
- `product_id`
- `published_status`
- `updated_at`

#### Staff members (`staffMembers` query)

- `account_type` (collaborator, regular, saml, restricted)
- `email`, `first_name`, `last_name`, `id`

### Real examples

```graphql
# Products: active, tagged vip, updated last 7 days, with stock
products(first: 50, query: "status:active tag:vip updated_at:>'2026-05-17T00:00:00Z' inventory_total:>0") { ... }

# Orders: unfulfilled, paid, from US in last 30 days
orders(first: 100, query: "fulfillment_status:unfulfilled financial_status:paid country:US created_at:>'2026-04-24T00:00:00Z'") { ... }

# Customers: VIP or wholesale with > 5 orders, excluding archived
customers(first: 50, query: "(tag:vip OR tag:wholesale) orders_count:>5 -tag:archived") { ... }

# Collections: smart collections containing a specific product
collections(first: 20, query: "collection_type:smart product_id:gid://shopify/Product/123") { ... }
```

---

## 6. Customer object

Customers appear in SyncApp's data flow **only as references on orders** — the app does not store customer PII, and the `read_customers` scope is not requested. However, order webhooks may include customer references and partial data.

### Type signature (abridged)

```graphql
type Customer implements Node, HasMetafields, ... {
  # Core identity
  id: ID!
  displayName: String!
  firstName: String
  lastName: String
  createdAt: DateTime!
  updatedAt: DateTime!
  legacyResourceId: UnsignedInt64!
  locale: String!
  lifetimeDuration: String!

  # Contact (PROTECTED)
  defaultEmailAddress: CustomerEmailAddress
  defaultPhoneNumber: CustomerPhoneNumber
  addressesV2(first: Int, ...): MailingAddressConnection!
  defaultAddress: MailingAddress

  # Order / financial (PROTECTED)
  amountSpent: MoneyV2!
  numberOfOrders: UnsignedInt64!
  orders(first: Int, query: String, ...): OrderConnection!
  lastOrder: Order

  # Account state
  state: CustomerState!  # ENABLED, DISABLED, INVITED, DECLINED
  verifiedEmail: Boolean!
  dataSaleOptOut: Boolean!
  taxExempt: Boolean!
  taxExemptions: [TaxExemption!]!

  # Subscription / payment (PROTECTED)
  paymentMethods: CustomerPaymentMethodConnection!
  subscriptionContracts: SubscriptionContractConnection!
  storeCreditAccounts: StoreCreditAccountConnection!

  # Administrative
  canDelete: Boolean!
  mergeable: CustomerMergeable!
  companyContactProfiles: [CompanyContact!]!
  tags: [String!]!
  note: String
  image: Image!
  multipassIdentifier: String

  # Metafields / events
  metafields(first: Int, ...): MetafieldConnection!
  metafield(namespace: String!, key: String!): Metafield
  events(first: Int, ...): EventConnection!
}
```

### Protected fields

Without **Level 2 protected customer data approval**, requesting these fields returns `null` with a userError:

- `firstName`, `lastName`, `displayName` (when derived from name)
- `defaultEmailAddress`, `defaultPhoneNumber`
- `addressesV2`, `defaultAddress`
- `amountSpent`, `orders`, `lastOrder`
- `paymentMethods`, `subscriptionContracts`

See [Access scopes](#12-access-scopes) for the protected customer data tiers.

### Queries

**Single customer:**

```graphql
query GetCustomer($id: ID!) {
  customer(id: $id) {
    id
    displayName
    numberOfOrders
    amountSpent { amount currencyCode }
    tags
  }
}
```

**Paginated list:**

```graphql
query CustomersList($cursor: String) {
  customers(
    first: 50
    after: $cursor
    query: "orders_count:>5 country:US"
    sortKey: TOTAL_SPENT
    reverse: true
  ) {
    edges {
      cursor
      node {
        id
        displayName
        amountSpent { amount currencyCode }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
```

### `customers` query arguments

| Argument | Type | Notes |
|---|---|---|
| `first` / `last` | Int | Page size |
| `after` / `before` | String | Cursor |
| `query` | String | Search syntax — see [Search query syntax](#5-search-query-syntax) |
| `reverse` | Boolean | Default false |
| `sortKey` | `CustomerSortKeys` | Default `ID`. Sort by a key that matches your filter for index efficiency. |

### SyncApp specifics

- SyncApp does **not** request `read_customers`. The app sees customer references only through order payloads where shipping country / customer tag may be useful for velocity analytics (e.g., "B2B orders sync faster"). Anything beyond that requires upgrading the scope.
- If you ever need to add a feature that touches customer data, also add the scope to `shopify.app.toml` AND go through the protected customer data review in the Partner Dashboard. Plan for a multi-day review cycle.

---

## 7. Collection object

A `Collection` is a group of products — either **manual** (merchant-curated list of products) or **smart** (rule-based, automatically populated). SyncApp does not currently use collections, but the type is on the roadmap for "collection-scoped sync" (only sync products in collection X).

### Type signature (abridged)

```graphql
type Collection implements Node, HasMetafields, HasPublishedTranslations, ... {
  # Identity
  id: ID!
  legacyResourceId: UnsignedInt64!
  title: String!
  handle: String!
  templateSuffix: String

  # Content
  description: String!
  descriptionHtml: HTML!
  image: Image
  seo: SEO!

  # Product management
  products(first: Int, sortKey: ProductCollectionSortKeys, ...): ProductConnection!
  productsCount: Count!
  hasProduct(id: ID!): Boolean!
  ruleSet: CollectionRuleSet   # Non-null on smart collections; null on manual

  # Sort & publish
  sortOrder: CollectionSortOrder!  # MANUAL, BEST_SELLING, ALPHA_ASC, etc.
  publishedOnPublication(publicationId: ID!): Boolean!
  resourcePublications(first: Int, ...): ResourcePublicationConnection!
  unpublishedPublications(first: Int, ...): PublicationConnection!
  availablePublicationsCount: Count!

  # Misc
  updatedAt: DateTime!
  metafield(namespace: String!, key: String!): Metafield
  metafields(first: Int, ...): MetafieldConnection!
  translations(locale: String!, ...): [Translation!]!
  events(first: Int, ...): EventConnection!
  activeOperations: [ResourceOperation!]!
  feedback: ResourceFeedback
}
```

### Manual vs smart collections

| Aspect | Manual | Smart |
|---|---|---|
| Membership | Merchant explicitly adds products | Determined by rules in `ruleSet` |
| `ruleSet` field | `null` | Populated with `CollectionRuleSet` |
| Modifications | `collectionAddProducts` / `collectionRemoveProducts` mutations | Edit `ruleSet`; processing is **asynchronous** |
| When updating | Synchronous | Returns a `job` to poll |
| Use case | Curated seasonal/promotional | Tag, vendor, type, price-driven |

### `CollectionRuleSet`

```graphql
type CollectionRuleSet {
  appliedDisjunctively: Boolean!  # true = OR rules together, false = AND
  rules: [CollectionRule!]!
}

type CollectionRule {
  column: CollectionRuleColumn!     # TAG, TITLE, TYPE, VENDOR, VARIANT_PRICE, ...
  relation: CollectionRuleRelation! # EQUALS, NOT_EQUALS, GREATER_THAN, LESS_THAN, STARTS_WITH, ...
  condition: String!
  conditionObject: CollectionRuleConditionObject  # for product-based rules
}
```

### Queries

**Single collection by ID:**

```graphql
query GetCollection($id: ID!) {
  collection(id: $id) {
    id
    title
    handle
    productsCount { count }
    products(first: 50) {
      nodes { id title }
    }
  }
}
```

Note: the `collection` query takes **only `id`**, not `handle`. If you have a handle, use `collectionByHandle(handle: String!)` instead.

**Paginated list:**

```graphql
query CollectionsList($cursor: String) {
  collections(
    first: 50
    after: $cursor
    query: "collection_type:smart updated_at:>'2026-05-01T00:00:00Z'"
    sortKey: UPDATED_AT
    reverse: true
  ) {
    edges {
      cursor
      node {
        id
        title
        handle
        ruleSet {
          appliedDisjunctively
          rules { column relation condition }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

### Mutations

**Create:**

```graphql
mutation CreateCollection($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id title handle }
    userErrors { field message }
  }
}
```

`CollectionInput` fields: `title`, `descriptionHtml`, `handle`, `products` (array of product IDs, manual only), `ruleSet`, `image`, `metafields`, `seo`.

Requires `write_products` scope. Newly created collections are **unpublished by default** — publish via `publishablePublish`.

**Update:**

```graphql
mutation UpdateCollection($input: CollectionInput!) {
  collectionUpdate(input: $input) {
    collection { id title }
    job { id done }    # populated for smart collections — poll for completion
    userErrors { field message }
  }
}
```

For smart collections, updating the `ruleSet` triggers an async job. Poll `job.done` until true before relying on the new membership.

### SyncApp future use

If we add "sync only this catalog" filtering, the natural model is to let merchants pick a collection in the workspace config. We'd then read `collection.products` paginated to build the in-scope variant set. Smart collections are fine as long as we re-read on a cadence or hook into `COLLECTIONS_UPDATE` webhooks.

---

## 8. Discount automatic / Discount code

SyncApp does not directly interact with discounts. They affect **price**, not inventory — a 20% off discount does not reduce stock. The exception is **BXGY** (buy X get Y) discounts that grant a free product: those still consume inventory at checkout via the standard order line item path. From SyncApp's perspective, an order with a BXGY-granted line item looks like a normal order with that line item; inventory decrements normally.

This section is for orientation in case SyncApp ever adds analytics like "stock impact of promotional periods".

### High-level discount taxonomy

Shopify exposes two parallel discount hierarchies:

1. **Automatic discounts** — applied without a code, triggered when an order meets criteria.
2. **Code discounts** — applied when a customer enters a promo code at checkout.

Each comes in four flavors:

| Flavor | Type | Notes |
|---|---|---|
| **Basic** | Percentage / fixed amount off products or order | The everyday discount |
| **Bxgy** | Buy X Get Y (e.g., buy 2 get 1 free) | **Can consume inventory** — the "free" item still decrements stock |
| **Free shipping** | Waive shipping fees | No inventory impact |
| **App** | Custom logic via Discount Function API | Behavior depends on the function |

### `DiscountAutomatic` union

```graphql
union DiscountAutomatic =
  | DiscountAutomaticBasic
  | DiscountAutomaticBxgy
  | DiscountAutomaticFreeShipping
  | DiscountAutomaticApp
```

### `DiscountCode` union (similar shape, also includes `DiscountCodeApp`)

```graphql
# Conceptually:
union DiscountCode =
  | DiscountCodeBasic
  | DiscountCodeBxgy
  | DiscountCodeFreeShipping
  | DiscountCodeApp
```

### `DiscountType` enum (high-level classification)

The top-level `DiscountType` enum is **just three values**:

- `AUTOMATIC_DISCOUNT`
- `CODE_DISCOUNT`
- `MANUAL`

The granular split into basic/bxgy/free-shipping is encoded by which **concrete object type** (`DiscountAutomaticBasic`, etc.) is returned, not by an enum value.

### `DiscountStatus` enum

- `ACTIVE` — currently usable
- `EXPIRED` — past `endsAt`
- `SCHEDULED` — future `startsAt`

### Example fields on `DiscountAutomaticBasic`

```graphql
type DiscountAutomaticBasic {
  title: String!
  status: DiscountStatus!
  startsAt: DateTime!
  endsAt: DateTime
  asyncUsageCount: Int!
  recurringCycleLimit: Int
  customerGets: DiscountCustomerGets!     # what products + amount off
  minimumRequirement: DiscountMinimumRequirement
  combinesWith: DiscountCombinesWith!
  appliesOncePerCustomer: Boolean!
}
```

### When SyncApp might care

- **Promotional periods produce velocity spikes.** Future analytics feature could correlate `asyncUsageCount` on active discounts with SKU velocity changes.
- **BXGY discounts on SyncApp-managed SKUs** — the "free" item lines decrement inventory normally; nothing special to handle at the sync engine level. Just be aware that aggressive velocity calculations during a BXGY promo overweight the demand signal.

### Required scope

`read_discounts` for read-only access. SyncApp does **not** currently request this.

---

## 9. Selling Plans

Shopify Subscriptions and similar purchase options (deferred sales, pre-orders, try-before-you-buy) are modeled as **selling plans** grouped into **selling plan groups**.

### `SellingPlanGroup`

The container — has buyer-facing labels and merchant-facing labels, associates with products and variants.

```graphql
type SellingPlanGroup implements Node {
  id: ID!
  name: String!              # Buyer-facing label, e.g., "Subscribe and save"
  merchantCode: String!      # Merchant-facing identifier
  description: String
  appId: String              # If created by a subscription app
  createdAt: DateTime!
  position: Int
  options: [String!]!        # Option values across plans, e.g., ["Every week", "Every month"]
  summary: String

  products(first: Int, ...): ProductConnection!
  productVariants(first: Int, ...): ProductVariantConnection!
  sellingPlans(first: Int, ...): SellingPlanConnection!

  appliesToProduct(productId: ID!): Boolean!
  appliesToProductVariant(productVariantId: ID!): Boolean!
}
```

### `SellingPlan`

A single purchase option within a group — defines its billing cadence, delivery cadence, pricing adjustments, and inventory policy.

```graphql
type SellingPlan implements Node, HasMetafields, HasPublishedTranslations {
  id: ID!
  name: String!
  description: String
  category: SellingPlanCategory  # SUBSCRIPTION, PRE_ORDER, TRY_BEFORE_YOU_BUY, OTHER
  position: Int
  createdAt: DateTime!
  options: [String!]!

  billingPolicy: SellingPlanBillingPolicy!
  deliveryPolicy: SellingPlanDeliveryPolicy!
  pricingPolicies: [SellingPlanPricingPolicy!]!
  inventoryPolicy: SellingPlanInventoryPolicy   # Controls when stock is reserved

  metafield(namespace: String!, key: String!): Metafield
  metafields(first: Int, ...): MetafieldConnection!
  translations(locale: String!, ...): [Translation!]!
}
```

### `SellingPlanInventoryPolicy`

Controls **when inventory is reserved** for the purchase:

- `ON_FULFILLMENT` — inventory is reserved at fulfillment time (typical for subscriptions: a new shipment's stock is reserved when the recurring order is fulfilled).
- `ON_SALE` — inventory is reserved at order placement (pre-order behavior).

**SyncApp impact:** orders with selling plans may have an `inventoryBehavior` field on line items that diverges from the standard. For pre-orders configured `ON_SALE`, inventory decrements at order time — same as a normal order. For subscriptions with `ON_FULFILLMENT`, the order may be created without an immediate inventory decrement; the decrement happens when the recurring fulfillment is created.

In practice, SyncApp watches inventory webhooks rather than order webhooks for the source-of-truth decrement, so this is mostly transparent.

### Line item reference

An order line item may include:

```graphql
type LineItem {
  # ...
  sellingPlan: LineItemSellingPlan
  # ...
}

type LineItemSellingPlan {
  name: String!
  sellingPlanId: ID
}
```

Useful for analytics ("how much of our sync demand is subscription-driven?"). Not load-bearing for inventory math.

### Required scope

`read_products` covers reading selling plans associated with products. Writing requires `write_products`.

---

## 10. Saved Searches

A `SavedSearch` is a stored search query — a string that's been given a name and saved against a resource type. Merchants create them via the admin UI ("Save as filter" on a list view). Apps can read them and reuse the query string.

### Type signature

```graphql
type SavedSearch implements Node, LegacyInteroperability {
  id: ID!
  legacyResourceId: UnsignedInt64!
  name: String!
  query: String!                    # The actual filter string
  resourceType: SearchResultType!   # CUSTOMER, ORDER, PRODUCT, COLLECTION, DRAFT_ORDER, ...
  searchTerms: String!              # Free-text portion
  filters: [SearchFilter!]!         # Structured filter portion
}
```

### Resource-typed access

Each major resource exposes its saved searches as a top-level connection on `Shop`:

- `shop.productSavedSearches`
- `shop.customerSavedSearches`
- `shop.orderSavedSearches`
- `shop.collectionSavedSearches`
- `shop.draftOrderSavedSearches`

### Using a saved search in a query

Two ways:

1. **Reference by name inside the query string** — `query: "saved_search:my_search"`.
2. **Reference by ID via `savedSearchId` argument** — supported on queries like `collections`, `customers`, `orders`, `products`.

```graphql
query OrdersFromSavedSearch($id: ID!) {
  orders(first: 50, savedSearchId: $id) {
    edges { node { id name } }
  }
}
```

### Mutations

`savedSearchCreate`, `savedSearchUpdate`, `savedSearchDelete` — for apps that want to expose UX for creating filters programmatically.

### SyncApp use

Useful for dashboards. If a merchant has a saved search like "low stock products" or "subscription orders this week", SyncApp's analytics UI can offer them as a one-click filter source. Not load-bearing — purely a UX nicety.

---

## 11. Staff members / Permissions

Staff members are the human users within a merchant's organization who can access the Shopify admin. Generally **not relevant for SyncApp** — the app authenticates against the **shop**, not a specific staff member. Embedded apps run in the context of whichever staff member is currently signed in to the admin, but SyncApp does not query that.

### `staffMembers` query

```graphql
query StaffList {
  staffMembers(first: 50, query: "account_type:regular") {
    edges {
      node {
        id
        name
        email
        active
        isShopOwner
        accountType
      }
    }
  }
}
```

### Arguments

- `first` / `last` / `after` / `before` — pagination
- `query` — supports `account_type`, `email`, `first_name`, `last_name`, `id`
- `reverse`, `sortKey`

### `StaffMember` fields

```graphql
type StaffMember implements Node {
  id: ID!
  name: String!
  email: String!
  firstName: String
  lastName: String
  initials: [String!]
  phone: String
  locale: String!
  avatar(fallback: StaffMemberDefaultImage): Image!

  active: Boolean!                # Can sign in to admin
  isShopOwner: Boolean!           # The original shop owner
  exists: Boolean!                # Account exists in Shopify

  accountType: AccountType!       # REGULAR, COLLABORATOR, SAML, RESTRICTED, INVITED, INVITATION_REVOKED
  privateData: StaffMemberPrivateData!
}
```

**`AccountType` values:**

- `REGULAR` — standard staff member with shop-defined permissions
- `COLLABORATOR` — a Shopify Partner with collaborator access (e.g., a development agency)
- `SAML` — authenticated via SAML SSO
- `RESTRICTED` — limited permissions account
- `INVITED` — invited but not yet accepted
- `INVITATION_REVOKED` — invitation was revoked

### Permissions

The `StaffMember.privateData` and per-staff permission flags expose what each staff member can do in the admin — but **this is the shop's internal RBAC, not the app's**. SyncApp has its own role system (`owner > admin > manager > viewer`) defined in [app/lib/role.server.ts](../../../Desktop/SyncApp/app/lib/role.server.ts), populated when staff members install or are invited to SyncApp specifically.

### Required scope

`read_users` — not requested by SyncApp.

---

## 12. Access scopes

Scopes declare what data and operations the app may access on a shop. They're requested at OAuth install time and reviewed by Shopify (for protected data) and the merchant (who must approve them in the install consent screen).

**Configuration:** declared in `shopify.app.toml`:

```toml
[access_scopes]
scopes = "read_products,read_inventory,write_inventory,read_locations,read_orders,read_fulfillments"
```

### Scopes SyncApp currently uses

| Scope | Unlocks |
|---|---|
| `read_products` | `Product`, `ProductVariant`, `Collection`, `SellingPlanGroup`, `SellingPlan`. Read-only product catalog. |
| `read_inventory` | `InventoryItem`, `InventoryLevel`. Read current stock levels per location per SKU. |
| `write_inventory` | Mutations: `inventorySetQuantities`, `inventoryAdjustQuantities`, `inventoryActivate`, `inventoryDeactivate`. Sync engine's core capability. |
| `read_locations` | `Location` object. Required for any location-aware inventory operation. |
| `read_orders` | `Order`, `LineItem`, `Refund`, `Return`, abandoned checkouts, order transactions. **Default: 60-day window.** |
| `read_fulfillments` | `Fulfillment`, `FulfillmentOrder`. Needed to track inventory decrements at fulfillment time. |

### Webhooks scope inheritance

Subscribing to a webhook topic requires the equivalent read scope. SyncApp's `INVENTORY_LEVELS_UPDATE` subscription requires `read_inventory`. `ORDERS_CREATE` requires `read_orders`. `FULFILLMENTS_CREATE` requires `read_fulfillments`. The webhook subscriptions are declared in `shopify.app.toml` and checked against the app's scopes at install time.

### Scopes SyncApp explicitly **does not** request

| Scope | Why we don't have it |
|---|---|
| `write_products` | We do not modify product data — we only push inventory levels. |
| `read_customers` / `write_customers` | We do not need customer PII. Avoids protected customer data review. |
| `read_discounts` | No discount-related features (yet). |
| `read_all_orders` | We do not need historical orders beyond 60 days. Requires Shopify approval. |
| `read_themes`, `write_themes` | No theme integration. |
| `read_users` | No staff-member features. |

### The 60-day order window and `read_all_orders`

By default, `read_orders` only returns orders from the **last 60 days**. Older orders return null or are filtered out. To access the full order history, request `read_all_orders` in addition to `read_orders`.

**`read_all_orders` is a protected scope** — requires Shopify approval. Apps must:
1. Request the scope in `shopify.app.toml`.
2. Submit access request in the Partner Dashboard with justification.
3. Pass Shopify review (typically days, not minutes).

SyncApp's analytics work within the 60-day window. Reading older orders for backfill / historical velocity would require requesting `read_all_orders`. If we ever do, plan for the review process.

### Protected customer data scopes

These scopes require a **Partner Dashboard data protection review** before they work on production stores (development stores can use them freely):

| Scope | Tier | Notes |
|---|---|---|
| `read_customers` / `write_customers` | Level 1 minimum | Access to Customer object excluding name/address/phone/email. |
| `read_customers` (with PII fields requested) | Level 2 | Access to firstName, lastName, defaultEmailAddress, defaultPhoneNumber, addresses. Stricter review. |
| `read_all_orders` | — | Orders beyond 60 days. Requires justification. |
| `read_customer_payment_methods` | — | Vaulted payment methods. Highest scrutiny. |
| `read_own_subscription_contracts` / `write_own_subscription_contracts` | — | Subscription contracts. |

**Protected customer data tiers:**

- **Level 0** — App uses no protected customer data. No action required.
- **Level 1** — App uses protected customer data but **not** the directly identifying fields (name, address, phone, email). Requires Partner Dashboard request, level-1 requirements (encryption at rest, access logging, etc.).
- **Level 2** — App uses identifying fields. Requires level-1 requirements **plus** participating in data protection reviews.

Apps that request protected scopes without going through review will receive `null` values and `userErrors` for those fields in production.

### Other commonly-needed scopes (reference)

| Scope | Purpose |
|---|---|
| `read_draft_orders` / `write_draft_orders` | `DraftOrder` objects. |
| `read_payment_customizations` / `write_payment_customizations` | `PaymentCustomization`. |
| `read_payment_gateways` / `write_payment_gateways` | Payments Apps API. |
| `write_payment_sessions` | Payment processing sessions. |
| `read_discounts` / `write_discounts` | Discount/promotion management. |
| `read_reports` | `shopifyqlQuery` analytics. |
| `read_themes` / `write_themes` | Online Store theme assets. |
| `read_translations` / `write_translations` | Multi-language content. |
| `read_shopify_payments_disputes` | Payment disputes. |
| `read_shopify_payments_payouts` | Payouts data. |
| `read_metaobjects` / `write_metaobjects` | Metaobjects (custom data structures). |
| `read_metafields` / `write_metafields` | Cross-resource metafields (often implicit with object scopes). |

### Adding a new scope

1. Edit `shopify.app.toml`:
   ```toml
   [access_scopes]
   scopes = "read_products,read_inventory,write_inventory,read_locations,read_orders,read_fulfillments,read_NEW_SCOPE"
   ```
2. Run `shopify app deploy` to push the new app configuration to Shopify.
3. **Existing installs do not get the new scope automatically.** Shopify's `shopify.server.ts` middleware detects the mismatch on next request and triggers OAuth re-authorization (the merchant sees a consent screen).
4. For protected scopes, complete the Partner Dashboard request **before** users hit the consent screen — otherwise re-auth will succeed but the protected fields will be `null`.

### Best practices

- **Least privilege.** Only request what you'll actually use. Each extra scope is friction at install and a security surface.
- **Read before write.** If you only need to read, do not request the write scope "just in case".
- **Document scope rationale.** When a teammate asks "why do we have scope X?", there should be a clear answer tied to a feature.
- **Audit on change.** Any PR that adds a scope should be flagged for security review.

---

## Glossary of cross-references

- For the inventory side of the API (`InventoryItem`, `InventoryLevel`, mutations, webhooks): see the dedicated inventory skill file.
- For order objects (`Order`, `LineItem`, `Fulfillment`, `Refund`, `Return`): see the orders skill file.
- For metafields and metaobjects: see the metafields skill file.
- For the Shopify-specific guidance SyncApp follows: see [docs/SHOPIFY-API-REFERENCE.md](../../../Desktop/SyncApp/docs/SHOPIFY-API-REFERENCE.md) in the SyncApp repo.

---

## Quick lookup cheatsheet

| Need | Type / Field |
|---|---|
| Monetary value (any) | `MoneyV2 { amount: Decimal!, currencyCode: CurrencyCode! }` |
| Monetary value on an order | `MoneyBag { shopMoney, presentmentMoney }` |
| Resource identifier | `ID` as `gid://shopify/<Type>/<num>` |
| Numeric ID (legacy/REST) | `UnsignedInt64` — keep as string, use `BigInt` if math needed |
| Date / time | `DateTime` ISO 8601 UTC `"2026-05-24T15:50:00Z"` |
| Currency | `CurrencyCode` enum (~180 values) |
| Country | `CountryCode` enum (~249 values, alpha-2) |
| Weight | `weight: Float`, `weightUnit: WeightUnit (GRAMS \| KILOGRAMS \| OUNCES \| POUNDS)` |
| Search a list | `query: String` with `:`, `:>`, `:<`, `AND`, `OR`, `-`, `()`, `*`, `"..."` |
| Saved search | `saved_search:name` in `query` OR `savedSearchId: ID` argument |
| Pagination | `first`/`last` + `after`/`before` cursor + `pageInfo { hasNextPage, endCursor }` |
| Scope check | `shopify.app.toml` `[access_scopes].scopes` |

